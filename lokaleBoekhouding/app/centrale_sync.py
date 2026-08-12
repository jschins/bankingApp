"""Immediate per-file sync client for centraleBoekhouding."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime import (
    app_root,
    central_data_root,
    exe_dir,
    is_central_admin,
    is_frozen,
    project_root,
    selected_workspace,
    set_runtime,
)

CATEGORIZED = "categorized_transactions.json"
PERSONAL_CATEGORIES = "personal_categories.json"
SHARED_CATEGORIES = "categories.json"

_local_session_active = False
_last_error: str | None = None
_file_revisions: dict[str, int] = {}
_last_event_id = 0
_pending_notifications: list[dict[str, Any]] = []
_state_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()
_push_wake = threading.Event()
_push_pending: set[str] = set()
_NOTIFY_TTL_SEC = 15.0
_config_cache: CentraleConfig | None = None


@dataclass(frozen=True)
class CentraleConfig:
    url: str
    workspace: str
    api_key: str
    enabled: bool
    port: int
    role: str  # "local" | "central_admin"
    centrale_data_root: str | None = None


def _default_central_data_root() -> Path:
    env = os.environ.get("CENTRALE_DATA_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    if is_frozen():
        return exe_dir()
    sibling = project_root().parent / "centraleBoekhouding" / "boekhouding"
    if sibling.is_dir():
        return sibling.resolve()
    return (project_root() / "boekhouding").resolve()


def config_path() -> Path:
    """Where ``lokale_config.json`` / central-admin config lives."""
    env = os.environ.get("LOKALE_CONFIG", "").strip()
    if env:
        return Path(env)
    if is_frozen():
        return exe_dir() / "lokale_config.json"
    ws = os.environ.get("LOKALE_WORKSPACE", "").strip()
    root = project_root()
    if ws:
        return root / ws / "lokale_config.json"
    if os.environ.get("LOKALE_ROLE", "").strip().lower() == "central_admin":
        return _default_central_data_root() / "lokale_config.json"
    for name in ("dkg", "jl"):
        p = root / name / "lokale_config.json"
        if p.is_file():
            return p
    return _default_central_data_root() / "lokale_config.json"


def load_config(*, force_reload: bool = False) -> CentraleConfig:
    global _config_cache
    if _config_cache is not None and not force_reload:
        # Keep workspace in sync with runtime switcher for central admin.
        if is_central_admin() and selected_workspace():
            ws = selected_workspace() or _config_cache.workspace
            if ws != _config_cache.workspace:
                _config_cache = CentraleConfig(
                    url=_config_cache.url,
                    workspace=ws,
                    api_key=_config_cache.api_key,
                    enabled=_config_cache.enabled,
                    port=_config_cache.port,
                    role=_config_cache.role,
                    centrale_data_root=_config_cache.centrale_data_root,
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
        os.environ.get("CENTRALE_URL", "").strip()
        or str(file_cfg.get("centrale_url") or "").strip()
        or "http://127.0.0.1:8400"
    ).rstrip("/")
    role = (
        os.environ.get("LOKALE_ROLE", "").strip()
        or str(file_cfg.get("role") or "").strip()
        or "local"
    ).lower()
    if role not in ("local", "central_admin"):
        role = "local"
    workspace = (
        os.environ.get("LOKALE_WORKSPACE", "").strip()
        or str(file_cfg.get("workspace") or "").strip()
        or "dkg"
    )
    if role == "central_admin" and selected_workspace():
        workspace = selected_workspace() or workspace
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
    # Ports: central admin 8300; lokale dkg 8301; lokale jl 8302 (overridable).
    default_port = 8300 if role == "central_admin" else (8302 if workspace == "jl" else 8301)
    try:
        port = int(
            os.environ.get("PORT", "").strip()
            or file_cfg.get("port")
            or default_port
        )
    except (TypeError, ValueError):
        port = default_port

    data_root_s: str | None = None
    if role == "central_admin":
        raw_root = (
            os.environ.get("CENTRALE_DATA_ROOT", "").strip()
            or str(file_cfg.get("centrale_data_root") or "").strip()
        )
        data_root = Path(raw_root).resolve() if raw_root else _default_central_data_root()
        data_root_s = str(data_root)
        set_runtime(
            role="central_admin",
            central_data_root=data_root,
            workspace=workspace,
            local_deploy_root=None,
        )
    else:
        if is_frozen():
            local_root = exe_dir()
        else:
            local_root = (project_root() / workspace).resolve()
            if not local_root.is_dir():
                local_root = app_root()
        set_runtime(
            role="local",
            central_data_root=None,
            workspace=workspace,
            local_deploy_root=local_root,
        )

    _config_cache = CentraleConfig(
        url=url,
        workspace=workspace,
        api_key=api_key,
        enabled=enabled,
        port=port,
        role=role,
        centrale_data_root=data_root_s,
    )
    return _config_cache


def _push_source() -> str:
    return "central" if is_central_admin() else "local"


def _events_viewer() -> str:
    return "central" if is_central_admin() else "local"



def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    cfg = load_config()
    url = f"{cfg.url}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def sync_status() -> dict[str, Any]:
    cfg = load_config()
    with _state_lock:
        notes = _active_notifications_unlocked()
    return {
        "enabled": cfg.enabled,
        "workspace": cfg.workspace,
        "centrale_url": cfg.url,
        "local_session_active": _local_session_active,
        "error": _last_error,
        "last_event_id": _last_event_id,
        "notifications": notes,
        "port": cfg.port,
        "role": cfg.role,
    }


def pop_notifications() -> dict[str, Any]:
    with _state_lock:
        notes = _active_notifications_unlocked()
    return {"notifications": notes}


def _active_notifications_unlocked() -> list[dict[str, Any]]:
    now = time.time()
    alive = [n for n in _pending_notifications if float(n.get("expires_at", 0)) > now]
    _pending_notifications[:] = alive
    return list(alive)


def _queue_notification(display_path: str) -> None:
    with _state_lock:
        _pending_notifications.append(
            {
                "file_path": display_path,
                "expires_at": time.time() + _NOTIFY_TTL_SEC,
            }
        )


def apply_local_path(rel_path: str, content: Any) -> None:
    root = app_root()
    rel = rel_path.replace("\\", "/").lstrip("/")
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def read_local_path(rel_path: str) -> Any | None:
    path = app_root() / rel_path.replace("\\", "/").lstrip("/")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def collect_local_files() -> dict[str, Any]:
    root = app_root()
    categories = read_local_path(SHARED_CATEGORIES)
    people: dict[str, Any] = {}
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or not (child / "data").is_dir():
            continue
        data = child / "data"
        people[child.name] = {
            "categorized_transactions": read_local_path(f"{child.name}/data/{CATEGORIZED}"),
            "personal_categories": read_local_path(f"{child.name}/data/{PERSONAL_CATEGORIES}"),
        }
    return {"categories": categories, "people": people}


def apply_remote_files(payload: dict[str, Any]) -> None:
    if payload.get("categories") is not None:
        apply_local_path(SHARED_CATEGORIES, payload["categories"])
    people = payload.get("people") or {}
    if not isinstance(people, dict):
        return
    for person, files in people.items():
        if not isinstance(files, dict):
            continue
        safe = Path(person).name
        if files.get("categorized_transactions") is not None:
            apply_local_path(
                f"{safe}/data/{CATEGORIZED}",
                files["categorized_transactions"],
            )
        if files.get("personal_categories") is not None:
            apply_local_path(
                f"{safe}/data/{PERSONAL_CATEGORIES}",
                files["personal_categories"],
            )


def push_paths(paths: list[str]) -> dict[str, Any]:
    """Immediately PUT each local tracked file to centrale.

    Unchanged files (same JSON as hub) are skipped so they do not create
    notifications for unrelated people.
    """
    global _last_error
    cfg = load_config()
    if not cfg.enabled:
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    if not _local_session_active:
        return {"ok": True, "skipped": True, "reason": "session inactive"}

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel in paths:
        rel_n = rel.replace("\\", "/").lstrip("/")
        if rel_n in seen:
            continue
        seen.add(rel_n)
        content = read_local_path(rel_n)
        if content is None:
            results.append({"path": rel_n, "ok": False, "error": "missing local file"})
            continue
        try:
            hub = _hub_file(cfg.workspace, rel_n)
            rev = int(hub.get("revision") or 0)
            _file_revisions[rel_n] = rev
            if _json_equal(hub.get("content"), content):
                results.append({"path": rel_n, "ok": True, "skipped": True, "reason": "unchanged"})
                continue
            res = _request(
                "PUT",
                f"/api/local/{cfg.workspace}/file",
                body={
                    "path": rel_n,
                    "content": content,
                    "source": _push_source(),
                    "client_revision": rev,
                },
            )
            if res.get("central_wins"):
                if res.get("content") is not None:
                    apply_local_path(rel_n, res["content"])
                _file_revisions[rel_n] = int(res.get("revision") or rev)
                results.append({"path": rel_n, "ok": True, "central_wins": True})
            else:
                _file_revisions[rel_n] = int(res.get("revision") or rev + 1)
                results.append({"path": rel_n, "ok": True, "central_wins": False})
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            results.append({"path": rel_n, "ok": False, "error": str(exc)})
    ok = all(r.get("ok") for r in results) if results else True
    if ok:
        _last_error = None
    return {"ok": ok, "results": results}


def _json_equal(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
            b, sort_keys=True, ensure_ascii=False
        )
    except (TypeError, ValueError):
        return False


def _hub_file(workspace: str, rel_path: str) -> dict[str, Any]:
    q = urllib.parse.urlencode({"path": rel_path})
    return _request("GET", f"/api/local/{workspace}/file?{q}", timeout=10.0)


def _hub_revision(workspace: str, rel_path: str) -> int:
    try:
        data = _hub_file(workspace, rel_path)
        rev = int(data.get("revision") or 0)
        _file_revisions[rel_path] = rev
        return rev
    except Exception:
        return int(_file_revisions.get(rel_path, 0))



def mark_and_push(paths: list[str]) -> dict[str, Any]:
    """Queue paths for background up-sync; return immediately (UI must not wait)."""
    normalized = [
        p.replace("\\", "/").lstrip("/")
        for p in paths
        if p and str(p).strip()
    ]
    if not normalized:
        return {"ok": True, "queued": False, "paths": []}
    with _state_lock:
        _push_pending.update(normalized)
    _push_wake.set()
    return {"ok": True, "queued": True, "paths": normalized}


def flush_pending_pushes() -> dict[str, Any]:
    """Synchronously push any queued paths (used on shutdown)."""
    with _state_lock:
        paths = sorted(_push_pending)
        _push_pending.clear()
    _push_wake.clear()
    if not paths:
        return {"ok": True, "skipped": True, "reason": "nothing queued"}
    return push_paths(paths)


def _drain_push_queue() -> None:
    with _state_lock:
        paths = sorted(_push_pending)
        _push_pending.clear()
    if paths:
        try:
            push_paths(paths)
        except Exception:
            pass


def pull_event_file(event: dict[str, Any]) -> None:
    """Apply a hub change event to the active workspace."""
    cfg = load_config()
    fp = str(event.get("file_path") or "")
    # Merged categories broadcast: pull this peer's categories.json (already merged on hub).
    if event.get("broadcast") or fp == SHARED_CATEGORIES:
        local_rel = SHARED_CATEGORIES
        remote_ws = cfg.workspace
        remote_path = SHARED_CATEGORIES
    else:
        local_rel = fp
        remote_ws = str(event.get("workspace") or cfg.workspace)
        remote_path = fp
        if remote_ws != cfg.workspace:
            # Central admin only applies events for the selected workspace
            # (person files); broadcast categories already handled above.
            return

    q = urllib.parse.urlencode({"path": remote_path})
    data = _request("GET", f"/api/local/{remote_ws}/file?{q}", timeout=15.0)
    content = data.get("content")
    if content is not None:
        apply_local_path(local_rel, content)
        _file_revisions[local_rel] = int(data.get("revision") or 0)
    display = str(event.get("display_path") or f"{remote_ws}/{remote_path}")
    _queue_notification(display)


def poll_central_events() -> dict[str, Any]:
    global _last_event_id, _last_error
    cfg = load_config()
    if not cfg.enabled or not _local_session_active:
        return {"ok": True, "skipped": True}
    try:
        q = urllib.parse.urlencode(
            {
                "viewer": _events_viewer(),
                "workspace": cfg.workspace,
                "since_id": _last_event_id,
            }
        )
        data = _request("GET", f"/api/events?{q}", timeout=10.0)
        for ev in data.get("events") or []:
            pull_event_file(ev)
            _last_event_id = max(_last_event_id, int(ev.get("id") or 0))
        latest = int(data.get("latest_id") or 0)
        if latest > _last_event_id:
            _last_event_id = latest
        _last_error = None
        return {"ok": True, "events": len(data.get("events") or [])}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return {"ok": False, "error": _last_error}


def list_hub_workspaces() -> list[str]:
    """Workspace names known to the hub (or local data root for central admin)."""
    cfg = load_config()
    if cfg.enabled:
        try:
            status = _request("GET", "/api/status", timeout=10.0)
            names = status.get("workspaces") or []
            if isinstance(names, list) and names:
                return [str(n) for n in names]
        except Exception:
            pass
    root = central_data_root()
    if root is not None and root.is_dir():
        found: list[str] = []
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if (child / SHARED_CATEGORIES).is_file() or any(
                p.is_dir() and (p / "data").is_dir() for p in child.iterdir() if p.is_dir()
            ):
                found.append(child.name)
        return found
    return [cfg.workspace] if cfg.workspace else []


def _seed_revisions(ws: str, files: dict[str, Any]) -> None:
    for rel in [SHARED_CATEGORIES] + [
        f"{name}/data/{CATEGORIZED}" for name in (files.get("people") or {})
    ] + [
        f"{name}/data/{PERSONAL_CATEGORIES}" for name in (files.get("people") or {})
    ]:
        try:
            _hub_revision(ws, rel)
        except Exception:
            pass


def start_session_and_pull() -> dict[str, Any]:
    global _local_session_active, _last_error, _last_event_id
    load_config(force_reload=True)
    cfg = load_config()
    if not cfg.enabled:
        _local_session_active = True
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    ws = cfg.workspace
    try:
        # Central admin does not register as a lokale peer session.
        if not is_central_admin():
            _request("POST", f"/api/local/{ws}/session/start")
        files = _request("GET", f"/api/local/{ws}/files")
        apply_remote_files(files)
        _local_session_active = True
        _seed_revisions(ws, files)
        events = _request(
            "GET",
            f"/api/events?{urllib.parse.urlencode({'viewer': _events_viewer(), 'workspace': ws, 'since_id': 0})}",
            timeout=10.0,
        )
        _last_event_id = int(events.get("latest_id") or 0)
        _last_error = None
        return {"ok": True, "workspace": ws, "people": list((files.get("people") or {}).keys())}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        _local_session_active = True
        return {"ok": False, "error": _last_error, "workspace": ws}


def switch_workspace(workspace: str) -> dict[str, Any]:
    """Central-admin: change active workspace, flush pushes, re-pull hub files."""
    global _last_event_id, _file_revisions, _last_error, _config_cache
    if not is_central_admin():
        return {"ok": False, "error": "workspace switch only available in central_admin role"}
    ws = workspace.strip()
    if not ws:
        return {"ok": False, "error": "workspace required"}
    flush_pending_pushes()
    root = central_data_root() or _default_central_data_root()
    set_runtime(
        role="central_admin",
        central_data_root=root,
        workspace=ws,
        local_deploy_root=None,
    )
    if _config_cache is not None:
        _config_cache = CentraleConfig(
            url=_config_cache.url,
            workspace=ws,
            api_key=_config_cache.api_key,
            enabled=_config_cache.enabled,
            port=_config_cache.port,
            role=_config_cache.role,
            centrale_data_root=_config_cache.centrale_data_root or str(root),
        )
    _file_revisions = {}
    with _state_lock:
        _pending_notifications.clear()
    cfg = load_config()
    try:
        if cfg.enabled:
            files = _request("GET", f"/api/local/{ws}/files")
            apply_remote_files(files)
            _seed_revisions(ws, files)
            events = _request(
                "GET",
                f"/api/events?{urllib.parse.urlencode({'viewer': 'central', 'workspace': ws, 'since_id': 0})}",
                timeout=10.0,
            )
            _last_event_id = int(events.get("latest_id") or 0)
        _last_error = None
        return {"ok": True, "workspace": ws}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return {"ok": False, "error": _last_error, "workspace": ws}


def end_session_and_push() -> dict[str, Any]:
    """Best-effort push of all tracked files, then end session (local peers only)."""
    global _local_session_active, _last_error
    cfg = load_config()
    if not _local_session_active:
        return {"ok": True, "skipped": True, "reason": "no active local session"}
    if not cfg.enabled:
        _local_session_active = False
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    ws = cfg.workspace
    paths = [SHARED_CATEGORIES]
    root = app_root()
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and (child / "data").is_dir():
                paths.append(f"{child.name}/data/{CATEGORIZED}")
                paths.append(f"{child.name}/data/{PERSONAL_CATEGORIES}")
    flush_pending_pushes()
    push = push_paths(paths)
    if is_central_admin():
        _local_session_active = False
        return {"ok": bool(push.get("ok")), "push": push, "workspace": ws}
    try:
        _request("POST", f"/api/local/{ws}/session/end")
        _last_error = None
        result: dict[str, Any] = {"ok": bool(push.get("ok")), "push": push, "workspace": ws}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        result = {"ok": False, "error": _last_error, "push": push, "workspace": ws}
    _local_session_active = False
    return result


def _worker_loop() -> None:
    while not _worker_stop.is_set():
        woken = _push_wake.wait(timeout=1.5)
        if _worker_stop.is_set():
            break
        if woken:
            _push_wake.clear()
            _drain_push_queue()
        try:
            poll_central_events()
        except Exception:
            pass
    # Final drain on stop.
    _drain_push_queue()


def start_event_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _push_wake.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="centrale-events", daemon=True)
    _worker_thread.start()


def stop_event_worker() -> None:
    global _worker_thread
    _worker_stop.set()
    _push_wake.set()  # unblock wait so shutdown can drain
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=5.0)
    flush_pending_pushes()
    _worker_thread = None
