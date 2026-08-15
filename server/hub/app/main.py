"""FastAPI centrale hub: immediate file sync, events, categories merge."""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import store

API_KEY = os.environ.get("CENTRALE_API_KEY", "").strip()

app = FastAPI(title="boekhouding-hub", version="0.1")


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
    """Client listen port (BFF) + author id; hub shows ``ip:port (author)``."""
    port: int | None = None
    author: str | None = None


def _client_session_label(
    request: Request, _workspace: str, body: SessionPayload
) -> str:
    host = "unknown"
    if request.client is not None and request.client.host:
        host = request.client.host
    # Normalize IPv6 loopback / mapped forms for readability.
    if host in ("::1", "0:0:0:0:0:0:0:1"):
        host = "127.0.0.1"
    elif host.startswith("::ffff:"):
        host = host.split("::ffff:", 1)[-1]
    port = body.port
    addr = (
        f"{host}:{int(port)}"
        if port is not None and 1 <= int(port) <= 65535
        else host
    )
    author = (body.author or "").strip() or "?"
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
    body: SessionPayload = SessionPayload(),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        label = _client_session_label(request, workspace, body)
        return store.local_session_start(label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/end")
def api_local_session_end(
    workspace: str,
    request: Request,
    body: SessionPayload = SessionPayload(),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    try:
        label = _client_session_label(request, workspace, body)
        return store.local_session_end(label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/{workspace}/session/heartbeat")
def api_local_session_heartbeat(
    workspace: str,
    request: Request,
    body: SessionPayload = SessionPayload(),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Refresh last-seen so force-killed clients drop after TTL."""
    try:
        label = _client_session_label(request, workspace, body)
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
    main { width: min(36rem, 94vw); padding: 2rem; }
    h1 { font-size: 1.75rem; margin: 0 0 0.35rem; }
    p.lead { margin: 0 0 1.25rem; color: #444; line-height: 1.45; }
    .status { padding: 0.85rem 1rem; margin-bottom: 1rem; border-left: 4px solid #2a5a8c;
              background: rgba(255,255,255,0.75); }
    .notify-wrap { display: flex; flex-direction: column; gap: 0.5rem; min-height: 3rem; }
    .notify-btn {
      font: inherit; text-align: left; padding: 0.45rem 0.85rem;
      border: 1px solid #f472b6; border-radius: 999px;
      background: #fbcfe8; color: #831843;
      box-shadow: none;
    }
    .meta { margin-top: 1.25rem; font-size: 0.85rem; color: #666; }
    .err { color: #a33; margin-top: 0.75rem; min-height: 1.2em; }
    button.action { margin-top: 0.75rem; font: inherit; cursor: pointer;
                    padding: 0.55rem 1rem; border: 1px solid #2a5a8c; background: #2a5a8c; color: #fff; }
    button.stop { margin-top: 0.75rem; margin-left: 0.5rem; font: inherit; cursor: pointer;
                  padding: 0.55rem 1rem; border: 1px solid #8b3a3a; background: #8b3a3a; color: #fff; }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem; }
  </style>
</head>
<body>
  <main>
    <h1>Centrale boekhouding</h1>
    <p class="lead">Immediate sync hub. Client changes appear here as short-lived notifications.</p>
    <div class="status" id="status">Loading…</div>
    <div class="notify-wrap" id="notify" aria-live="polite"></div>
    <div class="actions">
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
      statusEl.textContent =
        "Sessions: " + ((s.local_sessions || []).join(", ") || "(none)") +
        " · workspaces: " + ((s.workspaces || []).join(", ") || "(none)");
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
