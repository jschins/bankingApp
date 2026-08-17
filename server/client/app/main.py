"""Thin BFF: frontend + proxy to hub domain APIs (no local workspace copies)."""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
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


class PersonRefreshRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    new_year: bool = False


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


def _category_code_from_name(category_name: str) -> int | None:
    match = re.match(r"^\s*(\d{1,2})", str(category_name))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _valid_category_codes_from_categories_json(path: Path) -> list[int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cats = payload.get("categories") if isinstance(payload, dict) else None
    if not isinstance(cats, dict):
        return []
    codes: set[int] = set()
    for name in cats.keys():
        code = _category_code_from_name(str(name))
        if code is not None:
            codes.add(code)
    return sorted(codes)


def _workspace_data_roots(workspace: str) -> list[Path]:
    from app.runtime import project_root, server_root

    ws = workspace.strip()
    if not ws:
        return []
    roots = [
        server_root() / "workspaces" / ws,         # normal dev layout
        project_root().parents[1] / "workspaces" / ws,  # extra safety in dev
        server_root() / ws,                        # when client exe sits in workspaces/
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _local_transactions_payload(
    *,
    workspace: str,
    short: str,
    category_name: str,
    folder: str,
) -> dict[str, Any] | None:
    category_code = _category_code_from_name(category_name)
    if category_code is None:
        return None

    for root in _workspace_data_roots(workspace):
        categorized_path = root / folder / "data" / "categorized_transactions.json"
        if not categorized_path.is_file():
            continue
        try:
            payload = json.loads(categorized_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        raw_transactions = payload.get("transactions")
        transactions: list[dict[str, Any]] = []
        if isinstance(raw_transactions, list):
            for item in raw_transactions:
                if not isinstance(item, dict):
                    continue
                try:
                    code = int(float(str(item.get("category", "")).strip()))
                except ValueError:
                    continue
                if code != category_code:
                    continue
                tx = dict(item)
                tx["person"] = short
                transactions.append(tx)

        modifications = payload.get("modifications")
        modified_ids: list[str] = []
        if isinstance(modifications, list):
            for mod in modifications:
                if isinstance(mod, dict) and "id" in mod:
                    modified_ids.append(str(mod["id"]))

        columns = ["date", "amount", "type", "name", "description", "category"]
        categories_path = root.parent / "categories.json"
        valid_codes = _valid_category_codes_from_categories_json(categories_path)
        return {
            "person": short,
            "folder": folder,
            "category": category_name,
            "columns": columns,
            "transactions": transactions,
            "description_modified_ids": modified_ids,
            "category_modified_ids": modified_ids,
            "keywords": [],
            "abbreviations": {},
            "valid_category_codes": valid_codes,
            "remainder_category": "18 Overige uitgaven",
        }
    return None


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
        raise HTTPException(status_code=400, detail="workspace switch requires access=central")
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
    from app.centrale_sync import hub_get, scope_people

    try:
        payload = hub_get("/people")
        if isinstance(payload, dict):
            payload = {
                **payload,
                "people": scope_people(
                    payload.get("people") if isinstance(payload.get("people"), list) else []
                ),
            }
        return payload
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.get("/api/matrix")
def api_matrix() -> dict[str, Any]:
    from app.centrale_sync import hub_get, scope_matrix

    try:
        payload = hub_get("/matrix")
        return scope_matrix(payload) if isinstance(payload, dict) else payload
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/recalculate")
def api_recalculate() -> dict[str, Any]:
    from app.centrale_sync import hub_post, scope_matrix

    try:
        result = hub_post("/recalculate", {})
        matrix = result.get("matrix")
        if isinstance(matrix, dict):
            return scope_matrix(matrix)
        if isinstance(result, dict):
            return scope_matrix(result)
        return result
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/refresh")
def api_refresh(body: RefreshRequest | None = None) -> dict[str, Any]:
    from app.centrale_sync import configured_person, hub_post, scope_refresh
    import urllib.parse

    req = body or RefreshRequest()
    try:
        person = configured_person()
        if person:
            # Scoped client: refresh that person only (append-only, no new-year).
            result = hub_post(
                f"/refresh/{urllib.parse.quote(person)}",
                {
                    "date_from": req.date_from,
                    "date_to": req.date_to,
                    "new_year": False,
                },
                timeout=300.0,
            )
        else:
            result = hub_post(
                "/refresh",
                {"date_from": req.date_from, "date_to": req.date_to},
                timeout=300.0,
            )
        return scope_refresh(result) if isinstance(result, dict) else result
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/refresh/{short}")
def api_refresh_person(short: str, body: PersonRefreshRequest | None = None) -> dict[str, Any]:
    from app.centrale_sync import hub_post, require_person, scope_refresh
    import urllib.parse

    req = body or PersonRefreshRequest()
    try:
        require_person(short)
        result = hub_post(
            f"/refresh/{urllib.parse.quote(short)}",
            {
                "date_from": req.date_from,
                "date_to": req.date_to,
                "new_year": req.new_year,
            },
            timeout=300.0,
        )
        return scope_refresh(result) if isinstance(result, dict) else result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.get("/api/transactions/{short}/{category_name}")
def api_transactions(short: str, category_name: str) -> dict[str, Any]:
    from app.centrale_sync import hub_get, require_person
    import urllib.parse

    try:
        require_person(short)
        # Rafael-style local categorized JSON fallback: if present on disk, use it
        # for detail table rows (category click in overview), bypassing hub parser assumptions.
        people_payload = hub_get("/people")
        people = people_payload.get("people") if isinstance(people_payload, dict) else []
        folder = ""
        if isinstance(people, list):
            for person in people:
                if not isinstance(person, dict):
                    continue
                if str(person.get("short") or "").strip().lower() == short.strip().lower():
                    folder = str(person.get("folder") or "").strip()
                    break
        if folder:
            from app.centrale_sync import load_config

            cfg = load_config()
            local = _local_transactions_payload(
                workspace=cfg.workspace,
                short=short,
                category_name=category_name,
                folder=folder,
            )
            if local is not None:
                return local
        return hub_get(
            f"/transactions/{urllib.parse.quote(short)}/{urllib.parse.quote(category_name)}"
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.put("/api/transactions/{short}/modification")
def api_modification(short: str, body: ModificationRequest) -> dict[str, Any]:
    from app.centrale_sync import hub_put, require_person, scope_matrix
    import urllib.parse

    try:
        require_person(short)
        result = hub_put(
            f"/transactions/{urllib.parse.quote(short)}/modification",
            {"transaction": body.transaction, "source": _source()},
        )
        if isinstance(result, dict) and isinstance(result.get("matrix"), dict):
            result = {**result, "matrix": scope_matrix(result["matrix"])}
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    from app.centrale_sync import hub_get, scope_settings

    try:
        payload = hub_get("/settings")
        return scope_settings(payload) if isinstance(payload, dict) else payload
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.put("/api/settings/{group}/{category_name}")
def api_update_settings(
    group: str, category_name: str, body: SettingsTermsRequest
) -> dict[str, Any]:
    from app.centrale_sync import hub_put, person_allowed, require_person, scope_matrix, scope_settings
    import urllib.parse

    try:
        # Personal term groups are named by person short; general/shared stay open.
        if group not in ("general", "shared", "categories") and not person_allowed(group):
            require_person(group)
        result = hub_put(
            f"/settings/{urllib.parse.quote(group)}/{urllib.parse.quote(category_name)}",
            {"terms": body.terms, "source": _source()},
        )
        if isinstance(result, dict):
            if isinstance(result.get("matrix"), dict):
                result = {**result, "matrix": scope_matrix(result["matrix"])}
            result = scope_settings(result)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise _hub_error(exc) from exc


@app.post("/api/settings/add-term")
def api_add_term(body: AddTermRequest) -> dict[str, Any]:
    from app.centrale_sync import hub_post, require_person, scope_matrix, scope_settings

    try:
        if not body.general and body.person:
            require_person(body.person)
        result = hub_post(
            "/settings/add-term",
            {
                "category_name": body.category_name,
                "term": body.term,
                "general": body.general,
                "person": body.person,
                "source": _source(),
            },
        )
        if isinstance(result, dict):
            if isinstance(result.get("matrix"), dict):
                result = {**result, "matrix": scope_matrix(result["matrix"])}
            result = scope_settings(result)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
    print(
        f"boekhouding-client → hub {cfg.url} "
        f"(access={cfg.access}, workspace={cfg.workspace}, person={cfg.person or '*'}, port={port})"
    )

    if is_frozen():

        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}/")

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
