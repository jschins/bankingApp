"""Hub client for the thin BFF — no local workspace file copies."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime import (
    exe_dir,
    is_central_admin,
    is_frozen,
    project_root,
    selected_workspace,
    set_runtime,
)

_hub_session_active = False
_last_error: str | None = None
_last_event_id = 0
_pending_notifications: list[dict[str, Any]] = []
_state_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()
_NOTIFY_TTL_SEC = 30.0
_config_cache: HubConfig | None = None
_data_epoch = 0
_cached_has_secrets: bool = False


@dataclass(frozen=True)
class HubConfig:
    url: str
    workspace: str  # currently selected target (from access / switcher)
    author: str  # fixed identity from client_config "workspace" (hub session label)
    person: str  # empty unless access=personal
    access: str  # central | local | personal
    api_key: str
    enabled: bool
    port: int
    role: str  # "local" | "central_admin"
    workspaces: tuple[str, ...] = ()  # locked targets for local/personal; empty for central


def config_path() -> Path:
    """``client_config.json`` next to the client project / exe."""
    env = os.environ.get("CLIENT_CONFIG", "").strip()
    if env:
        return Path(env)
    if is_frozen():
        return exe_dir() / "client_config.json"
    return project_root() / "client_config.json"


def load_config(*, force_reload: bool = False) -> HubConfig:
    global _config_cache
    if _config_cache is not None and not force_reload:
        if selected_workspace() and selected_workspace() != _config_cache.workspace:
            ws = selected_workspace() or _config_cache.workspace
            _config_cache = HubConfig(
                url=_config_cache.url,
                workspace=ws,
                author=_config_cache.author,
                person=_config_cache.person,
                access=_config_cache.access,
                api_key=_config_cache.api_key,
                enabled=_config_cache.enabled,
                port=_config_cache.port,
                role=_config_cache.role,
                workspaces=_config_cache.workspaces,
            )
        return _config_cache

    cfg_path = config_path()
    file_cfg: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            file_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            file_cfg = {}

    url = (
        os.environ.get("SERVER_URL", "").strip()
        or str(file_cfg.get("server_url") or "").strip()
        or "http://127.0.0.1:8200"
    ).rstrip("/")

    access = str(file_cfg.get("access") or "local").strip().lower()
    if access not in ("central", "local", "personal"):
        access = "local"

    workspace_key = str(file_cfg.get("workspace") or "").strip()
    person_key = (
        os.environ.get("CLIENT_PERSON", "").strip()
        or str(file_cfg.get("person") or "").strip()
    )

    if access == "central":
        person = ""
        role = "central_admin"
        author = workspace_key or "central"
        workspaces: tuple[str, ...] = ()
        workspace = selected_workspace() or workspace_key
        if not workspace:
            workspace = _first_hub_workspace(url) or "dkg"
    elif access == "personal":
        person = person_key
        if not person:
            raise ValueError('client_config access=personal requires a non-empty "person"')
        role = "local"
        author = workspace_key or "dkg"
        workspaces = (author,)
        workspace = author
    else:
        # local — one workspace, all people; ignore person key
        person = ""
        role = "local"
        author = workspace_key or "dkg"
        workspaces = (author,)
        workspace = author

    api_key = (
        os.environ.get("CENTRALE_API_KEY", "").strip()
        or str(file_cfg.get("api_key") or "").strip()
    )
    enabled_raw = os.environ.get("CENTRALE_SYNC", "").strip().lower()
    if enabled_raw in ("0", "false", "off", "no"):
        enabled = False
    elif enabled_raw in ("1", "true", "on", "yes"):
        enabled = True
    else:
        enabled = bool(file_cfg.get("enabled", True))

    default_port = 8300 if role == "central_admin" else (8302 if author == "jl" else 8301)
    try:
        port = int(
            os.environ.get("PORT", "").strip()
            or file_cfg.get("port")
            or default_port
        )
    except (TypeError, ValueError):
        port = default_port

    set_runtime(
        workspace=workspace,
        allowed_workspaces=list(workspaces),
        access=access,
    )

    _config_cache = HubConfig(
        url=url,
        workspace=workspace,
        author=author,
        person=person,
        access=access,
        api_key=api_key,
        enabled=enabled,
        port=port,
        role=role,
        workspaces=workspaces,
    )
    return _config_cache


def _first_hub_workspace(url: str) -> str | None:
    """Best-effort first workspace name from hub status (central bootstrap)."""
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/api/status",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = data.get("workspaces") or []
        if isinstance(names, list) and names:
            return str(names[0]).strip() or None
    except Exception:  # noqa: BLE001
        return None
    return None


def configured_person() -> str:
    """Return configured person short, or ``\"\"`` when all people are visible."""
    return (load_config().person or "").strip()


def person_allowed(short: str) -> bool:
    """True when ``short`` may be shown/mutated under the current person scope."""
    scope = configured_person()
    if not scope:
        return True
    return short.strip().lower() == scope.lower()


def require_person(short: str) -> None:
    if person_allowed(short):
        return
    scope = configured_person()
    raise PermissionError(
        f"This client is scoped to person {scope!r}; {short!r} is not available."
    )


def scope_people(people: list[Any] | None) -> list[Any]:
    scope = configured_person()
    if not scope or not isinstance(people, list):
        return list(people or [])
    needle = scope.lower()
    return [
        p
        for p in people
        if isinstance(p, dict) and str(p.get("short") or "").strip().lower() == needle
    ]


def scope_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep matrix categories but only the configured person column when scoped."""
    scope = configured_person()
    if not scope:
        return payload
    out = dict(payload)
    people = scope_people(out.get("people") if isinstance(out.get("people"), list) else [])
    out["people"] = people
    shorts = {str(p.get("short") or "") for p in people if isinstance(p, dict)}
    cells = out.get("cells")
    if isinstance(cells, dict):
        trimmed: dict[str, Any] = {}
        for cat, row in cells.items():
            if not isinstance(row, dict):
                continue
            trimmed[cat] = {
                k: v for k, v in row.items() if str(k) in shorts or str(k).lower() == scope.lower()
            }
        out["cells"] = trimmed
    return out


def scope_settings(payload: dict[str, Any]) -> dict[str, Any]:
    scope = configured_person()
    if not scope:
        return payload
    out = dict(payload)
    out["people"] = scope_people(out.get("people") if isinstance(out.get("people"), list) else [])
    personal = out.get("personal")
    if isinstance(personal, dict):
        out["personal"] = {
            k: v
            for k, v in personal.items()
            if str(k).strip().lower() == scope.lower()
        }
    return out


def scope_refresh(payload: dict[str, Any]) -> dict[str, Any]:
    scope = configured_person()
    if not scope:
        return payload
    out = dict(payload)
    if isinstance(out.get("matrix"), dict):
        out["matrix"] = scope_matrix(out["matrix"])
    results = out.get("results")
    if isinstance(results, list):
        out["results"] = [
            r
            for r in results
            if isinstance(r, dict) and str(r.get("short") or "").strip().lower() == scope.lower()
        ]
    warnings = out.get("warnings")
    if isinstance(warnings, list):
        needle = scope.lower()
        out["warnings"] = [
            w
            for w in warnings
            if isinstance(w, str)
            and (w.lower().startswith(f"{needle}:") or w.lower().startswith(f"{needle} ("))
        ]
    return out


def scope_consent_ready(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scope = configured_person()
    if not scope:
        return items
    needle = scope.lower()
    return [
        item
        for item in items
        if str(item.get("short") or "").strip().lower() == needle
    ]


def _events_viewer() -> str:
    return "central" if is_central_admin() else "local"


def _push_source() -> str:
    return "central" if is_central_admin() else "local"


def hub_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    cfg = load_config()
    if not cfg.enabled:
        raise RuntimeError("hub sync disabled in client_config.json")
    url = f"{cfg.url}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"hub {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"hub unreachable: {exc.reason}") from exc


# Back-compat name used internally before rename.
_request = hub_request


def workspace_path(suffix: str = "") -> str:
    cfg = load_config()
    base = f"/api/local/{urllib.parse.quote(cfg.workspace)}"
    return f"{base}{suffix}" if suffix.startswith("/") or not suffix else f"{base}/{suffix}"


def hub_get(suffix: str, *, timeout: float = 60.0) -> dict[str, Any]:
    return hub_request("GET", workspace_path(suffix), timeout=timeout)


def hub_post(suffix: str, body: dict[str, Any] | None = None, *, timeout: float = 120.0) -> dict[str, Any]:
    return hub_request("POST", workspace_path(suffix), body=body, timeout=timeout)


def hub_put(suffix: str, body: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
    return hub_request("PUT", workspace_path(suffix), body=body, timeout=timeout)


def refresh_capabilities() -> dict[str, Any]:
    global _cached_has_secrets, _last_error
    try:
        data = hub_get("/capabilities", timeout=15.0)
        _cached_has_secrets = bool(data.get("has_secrets"))
        _last_error = None
        return data
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        raise


def sync_status() -> dict[str, Any]:
    cfg = load_config()
    consent_ready: list[dict[str, Any]] = []
    if cfg.enabled:
        try:
            data = hub_get("/consent-ready", timeout=5.0)
            raw = data.get("ready") if isinstance(data, dict) else None
            if isinstance(raw, list):
                consent_ready = [
                    {
                        "short": str(item.get("short") or ""),
                        "folder": str(item.get("folder") or ""),
                    }
                    for item in raw
                    if isinstance(item, dict) and str(item.get("short") or "").strip()
                ]
        except Exception:  # noqa: BLE001
            consent_ready = []
    with _state_lock:
        notes = _active_notifications_unlocked()
    return {
        "enabled": cfg.enabled,
        "workspace": cfg.workspace,
        "author": cfg.author,
        "person": cfg.person,
        "access": cfg.access,
        "centrale_url": cfg.url,
        "local_session_active": _hub_session_active,
        "error": _last_error,
        "last_event_id": _last_event_id,
        "notifications": notes,
        "port": cfg.port,
        "role": cfg.role,
        "workspaces": list(cfg.workspaces) if cfg.workspaces else [cfg.workspace],
        "data_epoch": _data_epoch,
        "has_secrets": _cached_has_secrets,
        "layout": "central" if cfg.role == "central_admin" else "local",
        "consent_ready": scope_consent_ready(consent_ready),
    }


def pop_notifications() -> dict[str, Any]:
    with _state_lock:
        notes = _active_notifications_unlocked()
    return {"notifications": notes}


def pop_central_wins_alerts() -> dict[str, Any]:
    # No local file races when clients do not mirror files.
    return {"alerts": []}


def ack_central_wins_alert(alert_id: int) -> dict[str, Any]:
    return {"ok": True, "removed": 0, "alerts": []}


def _active_notifications_unlocked() -> list[dict[str, Any]]:
    now = time.time()
    alive = [n for n in _pending_notifications if float(n.get("expires_at", 0)) > now]
    by_path: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for n in alive:
        key = str(n.get("file_path") or "").replace("\\", "/").strip()
        if not key:
            continue
        if key not in by_path:
            order.append(key)
        prev = by_path.get(key)
        if prev is None or float(n.get("expires_at", 0)) >= float(prev.get("expires_at", 0)):
            by_path[key] = n
    deduped = [by_path[k] for k in order if k in by_path]
    _pending_notifications[:] = deduped
    return list(deduped)


def _queue_notification(display_path: str) -> None:
    key = display_path.replace("\\", "/").strip()
    if not key:
        return
    with _state_lock:
        _active_notifications_unlocked()
        _pending_notifications[:] = [
            n
            for n in _pending_notifications
            if str(n.get("file_path") or "").replace("\\", "/").strip() != key
        ]
        _pending_notifications.append(
            {
                "file_path": key,
                "expires_at": time.time() + _NOTIFY_TTL_SEC,
            }
        )


def list_hub_workspaces() -> list[str]:
    cfg = load_config()
    if cfg.access != "central":
        return [cfg.workspace]
    if not cfg.enabled:
        return [cfg.workspace] if cfg.workspace else []
    try:
        status = hub_request("GET", "/api/status", timeout=10.0)
        names = status.get("workspaces") or []
        if isinstance(names, list) and names:
            return [str(n).strip() for n in names if str(n).strip()]
    except Exception:
        pass
    return [cfg.workspace] if cfg.workspace else []


def poll_central_events() -> dict[str, Any]:
    """Poll hub events for UI chips + data_epoch; do not write local files."""
    global _last_event_id, _last_error, _data_epoch
    cfg = load_config()
    if not cfg.enabled or not _hub_session_active:
        return {"ok": True, "skipped": True}
    try:
        params: dict[str, Any] = {
            "viewer": _events_viewer(),
            "since_id": _last_event_id,
        }
        if not is_central_admin():
            params["workspace"] = cfg.workspace
        q = urllib.parse.urlencode(params)
        data = hub_request("GET", f"/api/events?{q}", timeout=10.0)
        applied_any = False
        for ev in data.get("events") or []:
            # UI chip: workspace author only (not every affected file path).
            author = str(ev.get("workspace") or "").strip()
            if author and author not in ("_shared", "_merged"):
                _queue_notification(author)
            applied_any = True
            _last_event_id = max(_last_event_id, int(ev.get("id") or 0))
        if applied_any:
            _data_epoch += 1
        _last_error = None
        return {
            "ok": True,
            "events": len(data.get("events") or []),
            "applied": applied_any,
            "last_event_id": _last_event_id,
        }
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return {"ok": False, "error": _last_error}


def switch_workspace(workspace: str) -> dict[str, Any]:
    global _last_error, _last_event_id, _data_epoch
    cfg = load_config()
    ws = workspace.strip()
    if cfg.access != "central":
        return {"ok": False, "error": "Workspace switch requires access=central"}
    names = list_hub_workspaces()
    if names and ws not in names:
        return {"ok": False, "error": f"Workspace {ws!r} not on hub"}
    set_runtime(workspace=ws, allowed_workspaces=[], access="central")
    load_config(force_reload=True)
    with _state_lock:
        _pending_notifications.clear()
    try:
        caps = refresh_capabilities()
        events = hub_request(
            "GET",
            f"/api/events?{urllib.parse.urlencode({'viewer': _events_viewer(), 'since_id': 0, 'workspace': ws})}",
            timeout=10.0,
        )
        _last_event_id = int(events.get("latest_id") or 0)
        _data_epoch += 1
        _last_error = None
        return {"ok": True, "workspace": ws, "people": caps.get("people") or []}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return {"ok": False, "error": _last_error, "workspace": ws}


def _session_body() -> dict[str, Any]:
    """Payload for hub session start / end / heartbeat."""
    cfg = load_config()
    try:
        hostname = socket.gethostname().strip() or None
    except OSError:
        hostname = None
    return {
        "port": cfg.port,
        "author": cfg.author,
        "hostname": hostname,
    }


def start_session_and_pull() -> dict[str, Any]:
    """Connect to hub (no file pull). Hub must be reachable."""
    global _hub_session_active, _last_error, _last_event_id
    load_config(force_reload=True)
    cfg = load_config()
    if not cfg.enabled:
        _hub_session_active = False
        return {"ok": False, "error": "hub sync disabled — enable client_config.enabled"}
    ws = cfg.workspace
    try:
        hub_request("GET", "/api/health", timeout=10.0)
        hub_request(
            "POST",
            f"/api/local/{urllib.parse.quote(ws)}/session/start",
            body=_session_body(),
        )
        caps = refresh_capabilities()
        events = hub_request(
            "GET",
            f"/api/events?{urllib.parse.urlencode({'viewer': _events_viewer(), 'workspace': ws, 'since_id': 0})}",
            timeout=10.0,
        )
        _last_event_id = int(events.get("latest_id") or 0)
        _hub_session_active = True
        _last_error = None
        return {
            "ok": True,
            "workspace": ws,
            "people": [p.get("short") for p in (caps.get("people") or [])],
        }
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        _hub_session_active = False
        return {"ok": False, "error": _last_error, "workspace": ws}


def end_session_and_push() -> dict[str, Any]:
    global _hub_session_active, _last_error
    cfg = load_config()
    if not _hub_session_active:
        return {"ok": True, "skipped": True, "reason": "no active hub session"}
    if not cfg.enabled:
        _hub_session_active = False
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    ws = cfg.workspace
    try:
        hub_request(
            "POST",
            f"/api/local/{urllib.parse.quote(ws)}/session/end",
            body=_session_body(),
        )
        _last_error = None
        result: dict[str, Any] = {"ok": True, "workspace": ws}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        result = {"ok": False, "error": _last_error, "workspace": ws}
    _hub_session_active = False
    return result


def _worker_loop() -> None:
    while not _worker_stop.is_set():
        if _worker_stop.wait(timeout=1.5):
            break
        try:
            if _hub_session_active:
                _heartbeat_session()
            poll_central_events()
        except Exception:
            pass


def _heartbeat_session() -> None:
    """Keep hub session list fresh; missing heartbeats drop force-killed clients."""
    cfg = load_config()
    if not cfg.enabled:
        return
    ws = cfg.workspace
    hub_request(
        "POST",
        f"/api/local/{urllib.parse.quote(ws)}/session/heartbeat",
        body=_session_body(),
        timeout=10.0,
    )


def start_event_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="hub-events", daemon=True)
    _worker_thread.start()


def stop_event_worker() -> None:
    _worker_stop.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=5.0)
