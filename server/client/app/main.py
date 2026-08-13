"""Thin BFF: frontend + proxy to hub domain APIs (no local workspace copies)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.centrale_sync import (
        end_session_and_push,
        load_config,
        start_event_worker,
        start_session_and_pull,
        stop_event_worker,
    )

    cfg = load_config(force_reload=True)
    pull = start_session_and_pull()
    if not pull.get("ok"):
        print(f"ERROR: hub required but unavailable: {pull.get('error')}")
    else:
        print(f"hub session ok (workspace={pull.get('workspace')}, role={cfg.role})")
    start_event_worker()
    try:
        yield
    finally:
        stop_event_worker()
        push = end_session_and_push()
        if not push.get("ok"):
            print(f"WARNING: hub end-session failed: {push.get('error')}")
        elif not push.get("skipped"):
            print(f"hub end-session ok (workspace={push.get('workspace')})")


app = FastAPI(title="boekhouding-client", version="0.2", lifespan=lifespan)


class SettingsTermsRequest(BaseModel):
    terms: list[str] = Field(default_factory=list)


class AddTermRequest(BaseModel):
    category_name: str
    term: str
    general: bool = False
    person: str | None = None


class ModificationRequest(BaseModel):
    transaction: dict[str, Any]


class RefreshRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None


def _hub_error(exc: Exception) -> HTTPException:
    msg = str(exc)
    if msg.startswith("hub 403"):
        return HTTPException(status_code=403, detail=msg)
    if msg.startswith("hub 404"):
        return HTTPException(status_code=404, detail=msg)
    if msg.startswith("hub 400"):
        return HTTPException(status_code=400, detail=msg)
    return HTTPException(status_code=502, detail=msg)


def _source() -> str:
    from app.centrale_sync import _push_source

    return _push_source()


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app.centrale_sync import load_config, sync_status
    from app.runtime import frontend_dist_ok, is_frozen

    cfg = load_config()
    status = sync_status()
    return {
        "ok": status.get("error") is None and bool(cfg.enabled),
        "app": "boekhouding-client",
        "frozen": is_frozen(),
        "frontend_ok": frontend_dist_ok(),
        "hub": {
            "url": cfg.url,
            "workspace": cfg.workspace,
            "enabled": cfg.enabled,
            "port": cfg.port,
            "role": cfg.role,
            "error": status.get("error"),
            "has_secrets": status.get("has_secrets"),
        },
    }


@app.get("/api/workspaces")
def api_workspaces() -> dict[str, Any]:
    from app.centrale_sync import list_hub_workspaces, load_config

    cfg = load_config()
    return {
        "workspaces": list_hub_workspaces(),
        "workspace": cfg.workspace,
        "role": cfg.role,
    }


class WorkspaceRequest(BaseModel):
    workspace: str


@app.post("/api/workspace")
def api_set_workspace(body: WorkspaceRequest) -> dict[str, Any]:
    from app.centrale_sync import list_hub_workspaces, load_config, switch_workspace

    cfg = load_config()
    if cfg.role != "central_admin":
        raise HTTPException(status_code=400, detail="workspace switch requires multiple workspaces in config")
    names = list_hub_workspaces()
    if body.workspace not in names and names:
        raise HTTPException(status_code=404, detail=f"Unknown workspace: {body.workspace!r}")
    result = switch_workspace(body.workspace)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "switch failed")
    return {
        "ok": True,
        "workspace": body.workspace,
        "people": result.get("people") or [],
    }


@app.get("/api/centrale/status")
def api_centrale_status() -> dict[str, Any]:
    from app.centrale_sync import list_hub_workspaces, load_config, poll_central_events, sync_status

    poll_central_events()
    status = sync_status()
    cfg = load_config()
    if cfg.role == "central_admin":
        status["workspaces"] = list_hub_workspaces()
    try:
        from app.centrale_sync import refresh_capabilities

        caps = refresh_capabilities()
        status["has_secrets"] = bool(caps.get("has_secrets"))
    except Exception:
        pass
    return status


@app.get("/api/centrale/notifications")
def api_centrale_notifications() -> dict[str, Any]:
    from app.centrale_sync import poll_central_events, pop_notifications

    poll_central_events()
    return pop_notifications()


@app.get("/api/centrale/refusals")
def api_centrale_refusals() -> dict[str, Any]:
    from app.centrale_sync import pop_central_wins_alerts

    return pop_central_wins_alerts()


class RefusalAckRequest(BaseModel):
    id: int


@app.post("/api/centrale/refusals/ack")
def api_centrale_refusal_ack(body: RefusalAckRequest) -> dict[str, Any]:
    from app.centrale_sync import ack_central_wins_alert

    return ack_central_wins_alert(body.id)


@app.get("/api/people")
def api_people() -> dict[str, Any]:
    from app.centrale_sync import hub_get

    try:
        return hub_get("/people")
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.get("/api/matrix")
def api_matrix() -> dict[str, Any]:
    from app.centrale_sync import hub_get

    try:
        return hub_get("/matrix")
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/recalculate")
def api_recalculate() -> dict[str, Any]:
    from app.centrale_sync import hub_post

    try:
        result = hub_post("/recalculate", {})
        matrix = result.get("matrix")
        if isinstance(matrix, dict):
            return matrix
        return result
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/refresh")
def api_refresh(body: RefreshRequest | None = None) -> dict[str, Any]:
    from app.centrale_sync import hub_post

    req = body or RefreshRequest()
    try:
        return hub_post(
            "/refresh",
            {"date_from": req.date_from, "date_to": req.date_to},
            timeout=300.0,
        )
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.get("/api/transactions/{short}/{category_name}")
def api_transactions(short: str, category_name: str) -> dict[str, Any]:
    from app.centrale_sync import hub_get
    import urllib.parse

    try:
        return hub_get(
            f"/transactions/{urllib.parse.quote(short)}/{urllib.parse.quote(category_name)}"
        )
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.put("/api/transactions/{short}/modification")
def api_modification(short: str, body: ModificationRequest) -> dict[str, Any]:
    from app.centrale_sync import hub_put
    import urllib.parse

    try:
        return hub_put(
            f"/transactions/{urllib.parse.quote(short)}/modification",
            {"transaction": body.transaction, "source": _source()},
        )
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    from app.centrale_sync import hub_get

    try:
        return hub_get("/settings")
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.put("/api/settings/{group}/{category_name}")
def api_update_settings(
    group: str, category_name: str, body: SettingsTermsRequest
) -> dict[str, Any]:
    from app.centrale_sync import hub_put
    import urllib.parse

    try:
        return hub_put(
            f"/settings/{urllib.parse.quote(group)}/{urllib.parse.quote(category_name)}",
            {"terms": body.terms, "source": _source()},
        )
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/settings/add-term")
def api_add_term(body: AddTermRequest) -> dict[str, Any]:
    from app.centrale_sync import hub_post

    try:
        return hub_post(
            "/settings/add-term",
            {
                "category_name": body.category_name,
                "term": body.term,
                "general": body.general,
                "person": body.person,
                "source": _source(),
            },
        )
    except Exception as exc:
        raise _hub_error(exc) from exc


def _mount_frontend() -> None:
    import sys

    from fastapi.staticfiles import StaticFiles

    from app.runtime import frontend_dist_dir, frontend_dist_ok

    dist = frontend_dist_dir()
    if frontend_dist_ok():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
        return

    print(
        f"WARNING: UI not bundled — {dist / 'index.html'} missing.\n"
        "Rebuild frontend with: npm run build (in frontend/)\n"
        "API still works at /api/health",
        file=sys.stderr,
    )


_mount_frontend()


def run() -> None:
    import logging
    import os
    import threading
    import time
    import webbrowser

    import uvicorn

    from app.centrale_sync import load_config
    from app.runtime import is_frozen

    class _MutePollAccess(logging.Filter):
        _MUTE = (
            "GET /api/centrale/status",
            "GET /api/centrale/notifications",
            "GET /api/centrale/refusals",
        )

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return not any(p in msg for p in self._MUTE)

    logging.getLogger("uvicorn.access").addFilter(_MutePollAccess())

    cfg = load_config()
    host = os.environ.get("HOST", "127.0.0.1" if is_frozen() else "0.0.0.0")
    port = int(os.environ.get("PORT", str(cfg.port)))
    print(f"boekhouding-client → hub {cfg.url} (workspace={cfg.workspace}, port={port})")

    if is_frozen():

        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}/")

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
