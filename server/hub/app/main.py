"""FastAPI centrale hub: immediate file sync, events, categories merge."""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app import store

API_KEY = os.environ.get("CENTRALE_API_KEY", "").strip()

app = FastAPI(title="boekhouding-hub", version="0.1")

# Bank redirect hop must stay reachable even when hub_ips is set.
_HUB_IP_EXEMPT_PREFIXES = (
    "/api/consent/callback",
)


class _HubIpAllowlistMiddleware:
    """Pure ASGI middleware so POST bodies and ``request.client`` stay intact.

    ``BaseHTTPMiddleware`` can swallow JSON bodies and make every session look
    like the hub itself (loopback), so only one client appears as connected.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        client = scope.get("client")
        host = client[0] if isinstance(client, (list, tuple)) and client else None
        from app import upload_acl

        ip = upload_acl.client_ip(host)
        scope["hub_client_ip"] = ip
        if any(path.startswith(p) for p in _HUB_IP_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        hub_ips = upload_acl.hub_allowed_ips()
        if not hub_ips or ip in hub_ips or upload_acl.is_upload_http_path(path):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse("Not Found", status_code=404)
        await response(scope, receive, send)


app.add_middleware(_HubIpAllowlistMiddleware)


def _upload_client_ip(request: Request) -> str:
    """Best-effort client IP for upload endpoints behind a reverse proxy.

    Upload endpoints are protected by grant tokens, so using reverse-proxy
    headers is the only practical way to show the "real" IP in
    ``workspaces/upload.log``.
    """

    from app import upload_acl

    def _strip_port(token: str) -> str:
        t = (token or "").strip()
        if not t:
            return ""
        # IPv6 in brackets: [::1]:1234
        if t.startswith("[") and "]" in t:
            return t[1 : t.index("]")]
        # host:port (IPv4 or single-colon forms)
        if t.count(":") == 1:
            return t.split(":", 1)[0]
        return t

    def _valid_ip(token: str) -> bool:
        t = (token or "").strip()
        if not t:
            return False
        return t.lower() not in {"unknown", "none", "-"}

    headers = request.headers

    # Common proxy headers (Cloudflare / generic reverse proxies).
    for key in ("CF-Connecting-IP", "X-Real-IP"):
        raw = headers.get(key)
        if raw:
            cand = _strip_port(raw.split(",")[0])
            if _valid_ip(cand):
                return upload_acl.client_ip(cand)

    xff = headers.get("X-Forwarded-For")
    if xff:
        for part in xff.split(","):
            cand = _strip_port(part)
            if _valid_ip(cand):
                return upload_acl.client_ip(cand)

    # Fallback: what uvicorn sees (often the proxy IP).
    if request.client is not None and request.client.host:
        return upload_acl.client_ip(request.client.host)
    return upload_acl.client_ip("unknown")


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


class FilesPayload(BaseModel):
    categories: Any | None = None
    people: dict[str, Any] = Field(default_factory=dict)
    source: str = "local"


class FilePutPayload(BaseModel):
    path: str
    content: Any
    source: str = "local"
    client_revision: int | None = None


class SessionPayload(BaseModel):
    """Client listen port, config author, and optional computer hostname."""
    port: int | None = None
    author: str | None = None
    hostname: str | None = None


def _short_computer_name(raw: str) -> str:
    name = (raw or "").strip().rstrip(".")
    if not name:
        return ""
    # MagicDNS / FQDN → first label (e.g. my-laptop.tail123.ts.net → my-laptop)
    return name.split(".", 1)[0]


def _request_client_host(request: Request) -> str:
    from app import upload_acl

    stored = request.scope.get("hub_client_ip")
    if stored:
        return upload_acl.client_ip(str(stored))
    if request.client is not None and request.client.host:
        return upload_acl.client_ip(request.client.host)
    client = request.scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return upload_acl.client_ip(str(client[0]))
    return "unknown"


def _client_session_label(
    request: Request, _workspace: str, body: SessionPayload
) -> str:
    import socket

    host = _request_client_host(request)
    port = body.port
    addr = (
        f"{host}:{int(port)}"
        if port is not None and 1 <= int(port) <= 65535
        else host
    )
    author = (body.author or "").strip() or "?"
    computer = _short_computer_name(body.hostname or "")
    if not computer and host not in ("unknown", "127.0.0.1"):
        # Fallback when client is an older build: reverse-DNS / Tailscale MagicDNS.
        try:
            computer = _short_computer_name(socket.gethostbyaddr(host)[0])
        except OSError:
            computer = ""
    if computer:
        return f"{computer} @ {addr} ({author})"
    return f"{addr} ({author})"


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app.runtime import data_root, is_frozen

    return {
        "ok": True,
        "service": "boekhouding-hub",
        "frozen": is_frozen(),
        "data_root": str(data_root()),
        **store.get_status(),
    }


@app.get("/api/status")
def api_status(_: None = Depends(require_api_key)) -> dict[str, Any]:
    return store.get_status()


@app.get("/api/events")
def api_events(
    since_id: int = Query(default=0),
    viewer: str = Query(default="central"),
    workspace: str | None = Query(default=None),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.list_events(since_id=since_id, viewer=viewer, workspace=workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/start")
def api_local_session_start(
    workspace: str,
    request: Request,
    body: SessionPayload | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        label = _client_session_label(request, workspace, body or SessionPayload())
        return store.local_session_start(label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/end")
def api_local_session_end(
    workspace: str,
    request: Request,
    body: SessionPayload | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        label = _client_session_label(request, workspace, body or SessionPayload())
        return store.local_session_end(label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/heartbeat")
def api_local_session_heartbeat(
    workspace: str,
    request: Request,
    body: SessionPayload | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Refresh last-seen so force-killed clients drop after TTL."""
    try:
        label = _client_session_label(request, workspace, body or SessionPayload())
        return store.local_session_start(label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ClearSessionsPayload(BaseModel):
    label: str | None = None


@app.post("/api/sessions/clear")
def api_sessions_clear(
    body: ClearSessionsPayload = ClearSessionsPayload(),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.clear_local_sessions(body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/files")
def api_get_files(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    try:
        return store.read_workspace_files(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/local/{workspace}/files")
def api_put_files(
    workspace: str,
    body: FilesPayload,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.write_workspace_files(
            workspace,
            {"categories": body.categories, "people": body.people},
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/file")
def api_get_file(
    workspace: str,
    path: str = Query(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.read_file(workspace, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/local/{workspace}/file")
def api_put_file(
    workspace: str,
    body: FilePutPayload,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.put_file(
            workspace,
            body.path,
            body.content,
            source=body.source,
            client_revision=body.client_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/recalculate")
def api_recalculate_workspace(
    workspace: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        return store.mutate_and_recalculate(workspace, [], source="central")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class SettingsTermsRequest(BaseModel):
    terms: list[str] = Field(default_factory=list)
    source: str = "local"


class AddTermRequest(BaseModel):
    category_name: str
    term: str
    general: bool = False
    person: str | None = None
    source: str = "local"


class ModificationRequest(BaseModel):
    transaction: dict[str, Any]
    source: str = "local"


class RefreshRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None


class PersonRefreshRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    new_year: bool = False


class CreatePersonRequest(BaseModel):
    folder: str
    account_name: str
    country: str = "NL"
    aspsp: str = "ING"
    redirect_url: str | None = None


class BootstrapFetchRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None


@app.get("/api/local/{workspace}/capabilities")
def api_capabilities(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.capabilities(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/people")
def api_people(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.people(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/matrix")
def api_matrix(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.matrix(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/transactions/{short}/{category_name}")
def api_transactions(
    workspace: str,
    short: str,
    category_name: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.transactions(workspace, short, category_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/local/{workspace}/transactions/{short}/modification")
def api_modification(
    workspace: str,
    short: str,
    body: ModificationRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.record_modification(
            workspace, short, body.transaction, source=body.source
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/local/{workspace}/settings")
def api_settings(workspace: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.settings(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/local/{workspace}/settings/{group}/{category_name}")
def api_update_settings(
    workspace: str,
    group: str,
    category_name: str,
    body: SettingsTermsRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.update_settings(
            workspace, group, category_name, body.terms, source=body.source
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/settings/add-term")
def api_add_term(
    workspace: str,
    body: AddTermRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.add_term(
            workspace,
            category_name=body.category_name,
            term=body.term,
            general=body.general,
            person=body.person,
            source=body.source,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/refresh")
def api_refresh(
    workspace: str,
    body: RefreshRequest | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    req = body or RefreshRequest()
    try:
        return workspace_api.refresh(
            workspace,
            date_from=req.date_from,
            date_to=req.date_to,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/refresh/{short}")
def api_refresh_person(
    workspace: str,
    short: str,
    body: PersonRefreshRequest | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    req = body or PersonRefreshRequest()
    try:
        return workspace_api.refresh_person(
            workspace,
            short,
            date_from=req.date_from,
            date_to=req.date_to,
            new_year=req.new_year,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/shutdown")
def api_shutdown(_: None = Depends(require_api_key)) -> dict[str, Any]:
    """Stop the hub process (for the Stop button on http://127.0.0.1:8200/)."""
    import threading
    import time

    def _stop() -> None:
        time.sleep(0.35)
        os._exit(0)

    threading.Thread(target=_stop, name="hub-shutdown", daemon=True).start()
    return {"ok": True, "stopping": True}


@app.get("/api/consent/callback")
def consent_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Enable Banking redirect target (via deoudegracht → ``:8200``).

    No API key: the bank browser hits this URL. ``state`` selects the person
    registered when the authorization link was created.
    """
    from app import consent_flow
    from app.core.single_client import EnableBankingError, complete_authorization
    from app.paths import CALC_LOCK, bind_person
    from app.people import get_person
    from app.runtime import set_active_workspace
    from app.settings import init_app

    if error:
        detail = error_description or error
        return HTMLResponse(
            content=(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Bank consent failed</title></head><body>"
                f"<h1>Bank consent failed</h1><p>{detail}</p>"
                "<p>You can close this tab and return to Boekhouding.</p>"
                "</body></html>"
            ),
            status_code=400,
        )

    if not code or not str(code).strip():
        return HTMLResponse(
            content=(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Missing code</title></head><body>"
                "<h1>No authorization code received</h1>"
                "<p>You can close this tab and try the authorization link again.</p>"
                "</body></html>"
            ),
            status_code=400,
        )

    pending = consent_flow.take_pending(state)
    if not pending:
        return HTMLResponse(
            content=(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Unknown consent</title></head><body>"
                "<h1>Unknown or expired authorization</h1>"
                "<p>Start Refresh again to get a new authorization link, then retry.</p>"
                "</body></html>"
            ),
            status_code=400,
        )

    ws = str(pending.get("workspace") or "")
    short = str(pending.get("short") or "")
    folder = str(pending.get("folder") or short)
    raw_code = str(code).strip()
    try:
        with CALC_LOCK:
            set_active_workspace(ws)
            init_app()
            pack = get_person(short)
            with bind_person(pack):
                complete_authorization(raw_code)
            consent_flow.mark_ready(workspace=ws, short=short, folder=folder)
    except (EnableBankingError, KeyError, FileNotFoundError, ValueError) as exc:
        return HTMLResponse(
            content=(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Bank consent failed</title></head><body>"
                f"<h1>Bank consent failed ({short})</h1><p>{exc}</p>"
                "<p>You can close this tab and return to Boekhouding.</p>"
                "</body></html>"
            ),
            status_code=400,
        )
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            content=(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Bank consent failed</title></head><body>"
                f"<h1>Bank consent failed ({short})</h1><p>{exc}</p>"
                "</body></html>"
            ),
            status_code=500,
        )

    return HTMLResponse(
        content=(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Bank consent received</title></head><body>"
            f"<h1>Bank consent received — {short}</h1>"
            f"<p>Updated consent for {folder} in workspace {ws}.</p>"
            f"<p>Return to Boekhouding and use <strong>fetch for {short}</strong> "
            "(optional new year overwrite). You can close this tab.</p>"
            "<script>window.close();</script>"
            "</body></html>"
        )
    )


@app.get("/api/local/{workspace}/consent-ready")
def api_consent_ready(
    workspace: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import consent_flow

    return {"ready": consent_flow.list_ready(workspace)}


@app.post("/api/local/{workspace}/consent-ready/{short}/clear")
def api_consent_ready_clear(
    workspace: str,
    short: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import consent_flow

    return {
        "ok": True,
        "cleared": consent_flow.clear_ready(workspace=workspace, short=short),
    }


@app.post("/api/local/{workspace}/people/create")
def api_create_person(
    workspace: str,
    body: CreatePersonRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    try:
        return workspace_api.create_person(
            workspace,
            folder=body.folder,
            account_name=body.account_name,
            country=body.country,
            aspsp=body.aspsp,
            redirect_url=body.redirect_url,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/people/{short}/pem")
async def api_upload_person_pem(
    workspace: str,
    short: str,
    file: UploadFile = File(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    raw = await file.read()
    try:
        return workspace_api.upload_person_pem(
            workspace,
            short,
            filename=file.filename or "key.pem",
            content=raw,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/people/{short}/bootstrap-fetch")
def api_bootstrap_person_fetch(
    workspace: str,
    short: str,
    body: BootstrapFetchRequest | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from app import workspace_api

    req = body or BootstrapFetchRequest()
    try:
        return workspace_api.bootstrap_person_fetch(
            workspace,
            short,
            date_from=req.date_from,
            date_to=req.date_to,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Centrale boekhouding</title>
  <style>
    :root { font-family: Georgia, "Times New Roman", serif; color: #1a1a1a; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: linear-gradient(160deg, #e8eef5 0%, #f7f4ef 55%, #dde6f0 100%); }
    main { width: min(42rem, 94vw); padding: 2rem; }
    h1 { font-size: 1.75rem; margin: 0 0 0.35rem; }
    p.lead { margin: 0 0 1.25rem; color: #444; line-height: 1.45; }
    .status { padding: 0.85rem 1rem; margin-bottom: 1rem; border-left: 4px solid #2a5a8c;
              background: rgba(255,255,255,0.75); white-space: pre-wrap; line-height: 1.45; }
    .notify-wrap { display: flex; flex-direction: column; gap: 0.5rem; min-height: 3rem; }
    .notify-btn {
      font: inherit; text-align: left; padding: 0.45rem 0.85rem;
      border: 1px solid #f472b6; border-radius: 999px;
      background: #fbcfe8; color: #831843;
      box-shadow: none;
    }
    .meta { margin-top: 1.25rem; font-size: 0.85rem; color: #666; }
    .err { color: #a33; margin-top: 0.75rem; min-height: 1.2em; }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-top: 0.75rem; }
    a.action, button.action, button.stop {
      box-sizing: border-box;
      margin: 0;
      font: inherit;
      font-weight: 700;
      font-size: 0.9rem;
      cursor: pointer;
      padding: 0.55rem 1rem;
      min-height: 2.35rem;
      border-radius: 6px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      color: #fff;
    }
    a.action, button.action {
      border: 1px solid #2a5a8c;
      background: #2a5a8c;
    }
    a.action:hover, button.action:hover {
      background: #1e4470;
      border-color: #1e4470;
    }
    button.stop {
      border: 1px solid #8b3a3a;
      background: #8b3a3a;
    }
    button.stop:hover {
      background: #6f2e2e;
      border-color: #6f2e2e;
    }
  </style>
</head>
<body>
  <main>
    <h1>Centrale boekhouding</h1>
    <p class="lead">Immediate sync hub. Client changes appear here as short-lived notifications.</p>
    <div class="status" id="status">Loading…</div>
    <div class="notify-wrap" id="notify" aria-live="polite"></div>
    <div class="actions">
      <a class="action" href="/add-person">Add person</a>
      <a class="action" href="/upload">Upload data</a>
      <button class="action" id="btnClearSessions" type="button">Clear sessions</button>
      <button class="stop" id="btnStop" type="button">Stop hub</button>
    </div>
    <p id="err" class="err"></p>
    <p class="meta" id="meta"></p>
  </main>
  <script>
    let sinceId = 0;
    const notifyEl = document.getElementById("notify");
    const statusEl = document.getElementById("status");
    const errEl = document.getElementById("err");
    const metaEl = document.getElementById("meta");

    async function api(method, path, body) {
      const opts = { method, headers: { "Accept": "application/json" } };
      if (body !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      if (!r.ok) throw new Error(await r.text() || r.statusText);
      return r.json();
    }

    function showNotify(displayPath) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "notify-btn";
      btn.textContent = displayPath;
      notifyEl.appendChild(btn);
    }

    function replaceNotifications(paths) {
      notifyEl.replaceChildren();
      for (const fp of paths) showNotify(fp);
    }

    async function refreshStatus() {
      const s = await api("GET", "/api/status");
      const sessions = s.local_sessions || [];
      const sessionText = sessions.length
        ? sessions.map((label) => "• " + label).join("\\n")
        : "(none)";
      statusEl.textContent =
        "Sessions:\\n" + sessionText +
        "\\n\\nworkspaces: " + ((s.workspaces || []).join(", ") || "(none)");
      metaEl.textContent = "latest_event_id=" + (s.latest_event_id || 0);
    }

    async function pollEvents() {
      errEl.textContent = "";
      try {
        const data = await api("GET", `/api/events?viewer=central&since_id=${sinceId}`);
        const events = data.events || [];
        if (events.length) {
          // Keep chips until the next mutation; then replace with that mutation's files.
          const latest = events[events.length - 1];
          const files = (latest.affected_files && latest.affected_files.length)
            ? latest.affected_files
            : [latest.display_path || (latest.workspace + "/" + latest.file_path)];
          replaceNotifications(files);
          for (const ev of events) sinceId = Math.max(sinceId, ev.id);
        }
        if (data.latest_id) sinceId = Math.max(sinceId, data.latest_id);
        await refreshStatus();
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    }

    document.getElementById("btnClearSessions").onclick = async () => {
      errEl.textContent = "";
      try {
        await api("POST", "/api/sessions/clear", {});
        await refreshStatus();
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    };

    document.getElementById("btnStop").onclick = async () => {
      if (!window.confirm("Stop the hub on port 8200?")) return;
      errEl.textContent = "";
      try {
        await api("POST", "/api/shutdown", {});
        statusEl.textContent = "Hub is stopping…";
        metaEl.textContent = "You can close this tab.";
      } catch (e) {
        // Connection drop after shutdown is expected.
        statusEl.textContent = "Hub stopped (or unreachable).";
        metaEl.textContent = String(e.message || e);
      }
    };

    pollEvents();
    setInterval(pollEvents, 1500);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def admin_page() -> str:
    return _ADMIN_HTML


_ADD_PERSON_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Add person — hub</title>
  <style>
    :root { font-family: Georgia, "Times New Roman", serif; color: #1a1a1a; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: linear-gradient(160deg, #e8eef5 0%, #f7f4ef 55%, #dde6f0 100%); }
    main { width: min(40rem, 94vw); padding: 2rem; }
    h1 { font-size: 1.6rem; margin: 0 0 0.35rem; }
    p.lead { margin: 0 0 1rem; color: #444; line-height: 1.45; }
    table { width: 100%; border-collapse: collapse; margin: 0.75rem 0 1rem; }
    th, td { text-align: left; padding: 0.45rem 0.35rem; border-bottom: 1px solid #cbd5e1;
             vertical-align: middle; font-size: 0.95rem; }
    th { width: 42%; color: #334155; font-weight: 600; }
    input[type="text"], input[type="password"], select {
      width: 100%; box-sizing: border-box; font: inherit; padding: 0.35rem 0.45rem;
      border: 1px solid #94a3b8; border-radius: 4px; background: #fff;
    }
    .actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; }
    button, .link-btn {
      font: inherit; cursor: pointer; padding: 0.5rem 0.9rem;
      border: 1px solid #2a5a8c; background: #c1f4ff; color: #0f172a; border-radius: 6px;
      font-weight: 600; text-decoration: none; display: inline-block;
    }
    button:disabled { opacity: 0.6; cursor: progress; }
    .step { display: none; margin-top: 1rem; padding: 0.85rem 1rem;
            background: rgba(255,255,255,0.8); border-left: 4px solid #2a5a8c; }
    .step.active { display: block; }
    .err { color: #a33; margin-top: 0.75rem; min-height: 1.2em; white-space: pre-wrap; }
    .ok { color: #166534; margin-top: 0.5rem; }
    .meta { font-size: 0.85rem; color: #666; margin-top: 1rem; }
    code { font-size: 0.85em; }
    .remind {
      margin: 0.85rem 0 0; padding: 0.75rem 0.9rem;
      background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px;
      font-size: 0.9rem; line-height: 1.45;
    }
    .remind h2 {
      margin: 0 0 0.45rem; font-size: 0.95rem; color: #1e3a5f; font-weight: 700;
    }
    .remind ol { margin: 0.35rem 0 0; padding-left: 1.25rem; }
    .remind li { margin: 0.35rem 0; }
    .remind dl {
      margin: 0.35rem 0 0; display: grid;
      grid-template-columns: minmax(9rem, 38%) 1fr; gap: 0.25rem 0.75rem;
    }
    .remind dt { color: #475569; margin: 0; }
    .remind dd { margin: 0; word-break: break-all; }
    .remind .note { margin: 0.55rem 0 0; color: #475569; font-size: 0.85rem; }
  </style>
</head>
<body>
  <main>
    <h1>Add person</h1>
    <p class="lead">Create a person pack on this hub, register Enable Banking, upload the PEM, then fetch YTD transactions. No shell access on the server required.</p>

    <label>Workspace
      <select id="workspace"></select>
    </label>
    <label style="display:block;margin-top:0.5rem">API key (only if hub requires Bearer)
      <input id="apiKey" type="password" placeholder="optional" autocomplete="off"/>
    </label>

    <div id="step1" class="step active">
      <table>
        <tr><th>folder name</th><td><input id="folder" type="text" placeholder="juleon_schins"/></td></tr>
        <tr><th>name on bank account</th><td><input id="accountName" type="text" placeholder="Hr Dr J M Schins"/></td></tr>
        <tr><th>country</th><td><input id="country" type="text" value="NL"/></td></tr>
        <tr><th>bank (aspsp)</th><td><input id="aspsp" type="text" value="ING"/></td></tr>
        <tr><th>redirect URL</th><td><input id="redirect" type="text" value="https://deoudegracht.nl/banking-callback.html"/></td></tr>
      </table>
      <div class="actions">
        <button type="button" id="btnCreate">Create folder</button>
      </div>
    </div>

    <div id="step2" class="step">
      <p>Folder created for <strong id="createdLabel"></strong>.</p>
      <div class="actions">
        <a class="link-btn" id="ebLink" href="https://enablebanking.com/cp/applications" target="_blank" rel="noopener noreferrer">Open Enable Banking applications</a>
      </div>

      <div class="remind">
        <h2>1. Create the API application — fill in:</h2>
        <dl>
          <dt>Application name</dt><dd>e.g. <code id="hintAppName">boekh-juleon_schins</code></dd>
          <dt>Redirect URL</dt><dd><code id="hintRedirect">https://deoudegracht.nl/banking-callback.html</code></dd>
          <dt>Description of app</dt><dd>e.g. <code>boekhouding</code></dd>
          <dt>Data protection email</dt><dd>e.g. <code>j.m.schins@gmail.com</code></dd>
          <dt>Privacy policy URL</dt><dd><a href="https://deoudegracht.nl/privacy.html" target="_blank" rel="noopener noreferrer">https://deoudegracht.nl/privacy.html</a></dd>
          <dt>Terms of service URL</dt><dd><a href="https://deoudegracht.nl/terms.html" target="_blank" rel="noopener noreferrer">https://deoudegracht.nl/terms.html</a></dd>
        </dl>
        <p class="note">Download / save the private key (<code>.pem</code>) when Enable Banking offers it — you only get it once. Keep the filename (Application ID).</p>
      </div>

      <div class="remind">
        <h2>2. After creating the app, link it:</h2>
        <dl>
          <dt>Country</dt><dd>e.g. <code id="hintCountry">Netherlands</code></dd>
          <dt>ASPSP</dt><dd>e.g. <code id="hintAspsp">ING</code></dd>
          <dt>Usage type</dt><dd><code>personal</code></dd>
        </dl>
        <p class="note">Then hit <strong>Link</strong>.</p>
      </div>

      <div class="remind">
        <h2>3. Upload the key here</h2>
        <ol>
          <li>Save the <code>.pem</code> on this laptop (do not rename if possible — stem becomes <code>app_id</code>).</li>
          <li>Return to this wizard and choose the file below.</li>
          <li>Click <strong>Upload PEM &amp; fetch YTD</strong>.</li>
        </ol>
      </div>

      <p style="margin-top:1rem">Upload the downloaded <code>.pem</code> (filename should be the Application ID):</p>
      <input id="pemFile" type="file" accept=".pem,application/x-pem-file,application/octet-stream"/>
      <div class="actions">
        <button type="button" id="btnPem">Upload PEM &amp; fetch YTD</button>
      </div>
    </div>

    <div id="step3" class="step">
      <p class="ok" id="doneMsg">Done.</p>
      <p>Return to Boekhouding and use <strong>Refresh</strong> / consent renewal if the bank still requires authorization.</p>
      <pre id="fetchOut" style="white-space:pre-wrap;font-size:0.8rem;background:#f8fafc;padding:0.75rem;overflow:auto"></pre>
    </div>

    <p id="err" class="err"></p>
    <p class="meta"><a href="/">← Hub status</a></p>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const errEl = document.getElementById("err");
    let created = null;

    function apiKey() {
      return (document.getElementById("apiKey").value || "").trim();
    }

    function headers(json) {
      const h = { "Accept": "application/json" };
      if (json) h["Content-Type"] = "application/json";
      const key = apiKey();
      if (key) h["Authorization"] = "Bearer " + key;
      return h;
    }

    async function api(method, path, body) {
      const opts = { method, headers: headers(body !== undefined) };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const r = await fetch(path, opts);
      const text = await r.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { detail: text }; }
      if (!r.ok) throw new Error(data.detail || text || r.statusText);
      return data;
    }

    function showStep(id) {
      for (const el of document.querySelectorAll(".step")) el.classList.remove("active");
      document.getElementById(id).classList.add("active");
    }

    async function loadWorkspaces() {
      const sel = document.getElementById("workspace");
      sel.replaceChildren();
      let names = [];
      try {
        const s = await api("GET", "/api/status");
        names = s.workspaces || [];
      } catch (_) {}
      const preferred = (params.get("workspace") || "").trim();
      if (preferred && !names.includes(preferred)) names = [preferred, ...names];
      if (!names.length) names = [preferred || "dkg"];
      for (const ws of names) {
        const opt = document.createElement("option");
        opt.value = ws;
        opt.textContent = ws;
        if (ws === preferred) opt.selected = true;
        sel.appendChild(opt);
      }
    }

    const COUNTRY_LABELS = { NL: "Netherlands", BE: "Belgium", DE: "Germany", FR: "France" };

    document.getElementById("btnCreate").onclick = async () => {
      errEl.textContent = "";
      const workspace = document.getElementById("workspace").value;
      const folder = document.getElementById("folder").value.trim();
      const countryCode = document.getElementById("country").value.trim().toUpperCase() || "NL";
      const aspsp = document.getElementById("aspsp").value.trim() || "ING";
      const redirect = document.getElementById("redirect").value.trim()
        || "https://deoudegracht.nl/banking-callback.html";
      const body = {
        folder,
        account_name: document.getElementById("accountName").value,
        country: countryCode,
        aspsp,
        redirect_url: redirect,
      };
      try {
        created = await api("POST", `/api/local/${encodeURIComponent(workspace)}/people/create`, body);
        document.getElementById("createdLabel").textContent =
          `${created.folder} in ${created.workspace}`;
        document.getElementById("ebLink").href = created.enable_banking_url || "https://enablebanking.com/cp/applications";
        document.getElementById("hintAppName").textContent =
          `boekh-${(created.folder || folder || "person").toLowerCase()}`;
        document.getElementById("hintRedirect").textContent = redirect;
        document.getElementById("hintCountry").textContent =
          COUNTRY_LABELS[countryCode] || countryCode;
        document.getElementById("hintAspsp").textContent = aspsp;
        showStep("step2");
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    };

    document.getElementById("btnPem").onclick = async () => {
      errEl.textContent = "";
      if (!created) { errEl.textContent = "Create the folder first."; return; }
      const fileInput = document.getElementById("pemFile");
      const file = fileInput.files && fileInput.files[0];
      if (!file) { errEl.textContent = "Choose a .pem file."; return; }
      const workspace = created.workspace;
      const short = created.folder || created.person;
      try {
        const fd = new FormData();
        fd.append("file", file, file.name);
        const h = { "Accept": "application/json" };
        const key = apiKey();
        if (key) h["Authorization"] = "Bearer " + key;
        const up = await fetch(
          `/api/local/${encodeURIComponent(workspace)}/people/${encodeURIComponent(short)}/pem`,
          { method: "POST", headers: h, body: fd }
        );
        const upText = await up.text();
        let upData = {};
        try { upData = upText ? JSON.parse(upText) : {}; } catch (_) { upData = { detail: upText }; }
        if (!up.ok) throw new Error(upData.detail || upText || up.statusText);

        const fetchResult = await api(
          "POST",
          `/api/local/${encodeURIComponent(workspace)}/people/${encodeURIComponent(short)}/bootstrap-fetch`,
          {}
        );
        document.getElementById("doneMsg").textContent =
          `PEM saved as ${upData.key_file}. Bootstrap fetch finished for ${short}.`;
        document.getElementById("fetchOut").textContent = JSON.stringify(fetchResult, null, 2);
        showStep("step3");
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    };

    loadWorkspaces().catch((e) => { errEl.textContent = String(e.message || e); });
  </script>
</body>
</html>
"""


@app.get("/add-person", response_class=HTMLResponse)
def add_person_page() -> str:
    return _ADD_PERSON_HTML


_UPLOAD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Upload data — hub</title>
  <style>
    :root { font-family: Georgia, "Times New Roman", serif; color: #1a1a1a; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: linear-gradient(160deg, #e8eef5 0%, #f7f4ef 55%, #dde6f0 100%); }
    main { width: min(40rem, 94vw); padding: 2rem; }
    h1 { font-size: 1.6rem; margin: 0 0 0.35rem; }
    p.lead { margin: 0 0 1rem; color: #444; line-height: 1.45; }
    label { display: block; margin-top: 0.75rem; font-size: 0.9rem; color: #334155; font-weight: 600; }
    input[type="text"], input[type="password"], input[type="number"], select {
      width: 100%; box-sizing: border-box; font: inherit; padding: 0.4rem 0.5rem;
      border: 1px solid #94a3b8; border-radius: 4px; background: #fff; margin-top: 0.25rem;
    }
    .actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1rem; }
    button, .link-btn {
      font: inherit; cursor: pointer; padding: 0.5rem 0.9rem; border-radius: 6px;
      border: 1px solid #2a5a8c; background: #c1f4ff; color: #0f172a; font-weight: 700;
      text-decoration: none; display: inline-flex; align-items: center;
    }
    button.primary { background: #2a5a8c; color: #fff; }
    .panel { margin-top: 1rem; padding: 0.85rem 1rem; background: rgba(255,255,255,0.85);
             border-left: 4px solid #2a5a8c; border-radius: 0 6px 6px 0; }
    .panel h2 { margin: 0 0 0.4rem; font-size: 0.95rem; }
    .panel ul { margin: 0.35rem 0 0; padding-left: 1.2rem; font-size: 0.85rem; }
    .err { color: #a33; margin-top: 0.75rem; min-height: 1.2em; white-space: pre-wrap; }
    .ok { color: #166534; margin-top: 0.75rem; white-space: pre-wrap; font-weight: 700; }
    .meta { font-size: 0.85rem; color: #666; margin-top: 1rem; }
    code { font-size: 0.85em; }
    #grantBox { display: none; }
  </style>
</head>
<body>
  <main>
    <h1>Upload data</h1>

    <div id="grantBox" class="panel" style="display:none">
      <h2 id="grantLabel"></h2>
      <div>Your IP: <code id="yourIp"></code></div>
      <label>Year
        <input id="year" type="number" min="1990" max="2100" step="1" placeholder="__YEAR__" inputmode="numeric"/>
      </label>
      <label>Format
        <input id="format" type="text"/>
      </label>
      <div>Upload folder:</div>
      <ul id="pathList"></ul>
      <div id="xlsxBox">
        <div>Excel files already on the hub <span id="fileCount" style="color:#666"></span>:</div>
        <ul id="xlsxList"></ul>
      </div>
      <label>File <span style="font-weight:400;color:#666">(max 32 MB)</span>
        <input id="file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/>
      </label>
      <div class="actions">
        <button type="button" id="btnUpload" class="primary">Upload</button>
      </div>
    </div>

    <p id="err" class="err"></p>
    <p id="ok" class="ok"></p>
    <div id="doneBox" style="display:none" class="actions">
      <button type="button" id="btnDone" class="primary">Done</button>
    </div>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const errEl = document.getElementById("err");
    const okEl = document.getElementById("ok");
    const _STORAGE_KEY = "upload_token";

    const urlToken = (params.get("t") || "").trim();
    let _token = urlToken;
    if (_token) {
      try { localStorage.setItem(_STORAGE_KEY, _token); } catch (_) {}
    } else {
      try { _token = (localStorage.getItem(_STORAGE_KEY) || "").trim(); } catch (_) {}
    }

    function token() { return _token; }
    const yearEl = document.getElementById("year");
    const formatEl = document.getElementById("format");
    function yearValue() { return (yearEl.value || yearEl.placeholder || "").trim(); }
    function formatValue() { return (formatEl.value || "Excel").trim(); }

    async function api(method, path, body, isForm) {
      const opts = { method, headers: { "Accept": "application/json" } };
      const t = token();
      if (t) opts.headers["Authorization"] = "Bearer " + t;
      if (isForm) {
        opts.body = body;
      } else if (body !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      const text = await r.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { detail: text }; }
      if (!r.ok) throw new Error(data.detail || text || r.statusText);
      return data;
    }

    let _grantLoaded = false;
    function showGrant(g) {
      document.getElementById("grantBox").style.display = "block";
      document.getElementById("grantLabel").textContent = (g.center || "") + "/" + (g.person || "");
      if (!_grantLoaded) {
        _grantLoaded = true;
        if (g.year && !yearEl.value) yearEl.value = g.year;
        if (g.format) formatEl.value = g.format;
      }
      document.getElementById("yourIp").textContent = g.client_ip || "?";
      const ul = document.getElementById("pathList");
      ul.replaceChildren();
      const li = document.createElement("li");
      li.innerHTML = "<code>" + (g.folder || "") + "</code>";
      ul.appendChild(li);
      const xlsxUl = document.getElementById("xlsxList");
      xlsxUl.replaceChildren();
      const files = g.xlsx_files || [];
      const countEl = document.getElementById("fileCount");
      countEl.textContent = files.length ? "(" + files.length + ")" : "";
      if (files.length === 0) {
        const empty = document.createElement("li");
        empty.textContent = "(none yet)";
        xlsxUl.appendChild(empty);
      } else {
        for (const name of files) {
          const item = document.createElement("li");
          item.textContent = name;
          xlsxUl.appendChild(item);
        }
      }
    }

    async function loadGrant() {
      const g = await api(
        "GET",
        "/upload/api/upload/grant?year=" + encodeURIComponent(yearValue())
      );
      showGrant(g);
      return g;
    }

    yearEl.addEventListener("change", async () => {
      if (document.getElementById("grantBox").style.display === "none") return;
      errEl.textContent = "";
      try {
        await loadGrant();
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    });

    function showDone() {
      document.getElementById("doneBox").style.display = "flex";
    }

    document.getElementById("btnDone").onclick = () => {
      location.assign("/upload");
    };

    document.getElementById("btnUpload").onclick = async () => {
      errEl.textContent = "";
      okEl.textContent = "";
      document.getElementById("doneBox").style.display = "none";
      const fileInput = document.getElementById("file");
      const file = fileInput.files && fileInput.files[0];
      if (!file) { errEl.textContent = "Choose a file."; return; }
      try {
        const fd = new FormData();
        fd.append("file", file, file.name);
        fd.append("year", yearValue());
        const fmt = formatValue().toLowerCase();
        if (fmt === "test" || fmt === "dry run") fd.append("format", fmt);
        const res = await api("POST", "/upload/api/upload", fd, true);
        if (fmt === "dry run") {
          if (res.balance_check === "pass") {
            okEl.textContent = "Dry run: balance check passed for " + res.path + ".";
          } else if (res.balance_check === "fail") {
            errEl.textContent = "Dry run: balance check failed — " + (res.detail || "unknown error");
          } else {
            okEl.textContent = "Dry run: " + res.path + " (" + res.bytes + " bytes) — no balance check (not xlsx).";
          }
        } else if (fmt === "test") {
          okEl.textContent = "Saved " + res.path + " (" + res.bytes + " bytes) — no processing (test mode).";
          showDone();
        } else {
          okEl.textContent = "Uploaded " + res.path + " (" + res.bytes + " bytes) via " + res.via + ".";
          showDone();
        }
      } catch (e) {
        errEl.textContent = String(e.message || e);
      }
    };

    if (token()) {
      loadGrant().catch((e) => { errEl.textContent = String(e.message || e); });
    } else {
      errEl.textContent = "No token provided. Add ?t=<token> to the URL.";
    }
  </script>
</body>
</html>
"""


@app.get("/upload", response_class=HTMLResponse)
def upload_page() -> str:
    from app import upload_acl
    from app.yearpath import current_year

    upload_acl.ensure_example_acl()
    from app.yearpath import default_upload_year

    return _UPLOAD_HTML.replace("__YEAR__", default_upload_year())


@app.get("/api/upload/grant")
def api_upload_grant(
    request: Request,
    authorization: str | None = Header(default=None),
    year: str | None = Query(default=None),
) -> dict[str, Any]:
    from app import upload_acl
    from app.yearpath import parse_year

    token = _upload_token(authorization, None)
    grant = upload_acl.find_grant_by_token(token)
    if grant is None:
        raise HTTPException(status_code=401, detail="Invalid upload token")
    ip = _upload_client_ip(request)
    from app.yearpath import default_upload_year

    try:
        y = parse_year(year) if year else default_upload_year()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "person": grant.person,
        "center": grant.center,
        "year": y,
        "format": grant.format,
        "folder": grant.year_folder(y),
        "client_ip": ip,
        "xlsx_files": upload_acl.list_grant_xlsx_files(grant, year=y),
    }


@app.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    path: str | None = Form(None),
    token: str | None = Form(None),
    year: str | None = Form(None),
    format: str | None = Form(None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    from app import upload_acl

    auth_token = _upload_token(authorization, token)
    grant = upload_acl.find_grant_by_token(auth_token)
    if grant is None:
        raise HTTPException(status_code=401, detail="Invalid upload token")
    ip = _upload_client_ip(request)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    # Cap uploads at 32 MiB
    if len(content) > 32 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 32 MiB)")
    dest = (path or "").strip()
    fmt = (format or "").strip().lower()
    test_mode = fmt == "test"
    dry_run = fmt == "dry run"
    try:
        return await run_in_threadpool(
            upload_acl.save_upload,
            grant=grant,
            ip=ip,
            rel_path=dest,
            content=content,
            filename=file.filename,
            year=year,
            test=test_mode,
            dry_run=dry_run,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/upload/api/upload/grant")
def api_upload_grant_proxy(
    request: Request,
    authorization: str | None = Header(default=None),
    year: str | None = Query(default=None),
) -> dict[str, Any]:
    """Upload endpoints variant under ``/upload``.

    This lets a reverse proxy expose only ``/upload`` over HTTPS without
    also forwarding ``/api/upload``.
    """

    return api_upload_grant(request, authorization=authorization, year=year)


@app.post("/upload/api/upload")
async def api_upload_proxy(
    request: Request,
    file: UploadFile = File(...),
    path: str | None = Form(None),
    token: str | None = Form(None),
    year: str | None = Form(None),
    format: str | None = Form(None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Upload endpoints variant under ``/upload`` (see ``api_upload_grant_proxy``)."""

    return await api_upload(
        request,
        file=file,
        path=path,
        token=token,
        year=year,
        format=format,
        authorization=authorization,
    )


def _upload_token(authorization: str | None, form_token: str | None) -> str:
    if form_token and form_token.strip():
        return form_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def run() -> None:
    import logging

    import uvicorn

    class _MutePollAccess(logging.Filter):
        """Drop noisy poll access lines (clients / admin UI)."""

        _MUTE = (
            "GET /api/events",
            "GET /api/status",
            "/capabilities",
            "/consent-ready",
            "/session/heartbeat",
        )

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return not any(p in msg for p in self._MUTE)

    logging.getLogger("uvicorn.access").addFilter(_MutePollAccess())

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8200"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
