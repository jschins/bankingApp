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

from app.runtime import app_root

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
_NOTIFY_TTL_SEC = 15.0


@dataclass(frozen=True)
class CentraleConfig:
    url: str
    workspace: str
    api_key: str
    enabled: bool


def load_config() -> CentraleConfig:
    cfg_path = app_root() / "lokale_config.json"
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
    workspace = (
        os.environ.get("LOKALE_WORKSPACE", "").strip()
        or str(file_cfg.get("workspace") or "").strip()
        or "dkg"
    )
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
    return CentraleConfig(url=url, workspace=workspace, api_key=api_key, enabled=enabled)


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
    """Immediately PUT each local tracked file to centrale."""
    global _last_error
    cfg = load_config()
    if not cfg.enabled:
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    if not _local_session_active:
        return {"ok": True, "skipped": True, "reason": "session inactive"}

    results: list[dict[str, Any]] = []
    for rel in paths:
        rel_n = rel.replace("\\", "/").lstrip("/")
        content = read_local_path(rel_n)
        if content is None:
            results.append({"path": rel_n, "ok": False, "error": "missing local file"})
            continue
        try:
            # Always refresh hub revision before PUT. Using 0 after restart made
            # every local write look stale (central_wins) and overwrite the edit.
            rev = _hub_revision(cfg.workspace, rel_n)
            res = _request(
                "PUT",
                f"/api/local/{cfg.workspace}/file",
                body={
                    "path": rel_n,
                    "content": content,
                    "source": "local",
                    "client_revision": rev,
                },
            )
            if res.get("central_wins"):
                # True race: hub moved ahead while we wrote. Keep hub copy.
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


def _hub_revision(workspace: str, rel_path: str) -> int:
    try:
        q = urllib.parse.urlencode({"path": rel_path})
        data = _request("GET", f"/api/local/{workspace}/file?{q}", timeout=10.0)
        rev = int(data.get("revision") or 0)
        _file_revisions[rel_path] = rev
        return rev
    except Exception:
        return int(_file_revisions.get(rel_path, 0))



def mark_and_push(paths: list[str]) -> dict[str, Any]:
    return push_paths(paths)


def pull_event_file(event: dict[str, Any]) -> None:
    """Apply a central change event to the local workspace."""
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
                "viewer": "local",
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


def start_session_and_pull() -> dict[str, Any]:
    global _local_session_active, _last_error, _last_event_id
    cfg = load_config()
    if not cfg.enabled:
        _local_session_active = True
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    ws = cfg.workspace
    try:
        _request("POST", f"/api/local/{ws}/session/start")
        files = _request("GET", f"/api/local/{ws}/files")
        apply_remote_files(files)
        _local_session_active = True
        # Seed hub revisions so later local edits are not rejected as stale.
        for rel in [SHARED_CATEGORIES] + [
            f"{name}/data/{CATEGORIZED}"
            for name in (files.get("people") or {})
        ] + [
            f"{name}/data/{PERSONAL_CATEGORIES}"
            for name in (files.get("people") or {})
        ]:
            try:
                _hub_revision(ws, rel)
            except Exception:
                pass
        events = _request(
            "GET",
            f"/api/events?{urllib.parse.urlencode({'viewer': 'local', 'workspace': ws, 'since_id': 0})}",
            timeout=10.0,
        )
        _last_event_id = int(events.get("latest_id") or 0)
        _last_error = None
        return {"ok": True, "workspace": ws, "people": list((files.get("people") or {}).keys())}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        _local_session_active = True
        return {"ok": False, "error": _last_error, "workspace": ws}


def end_session_and_push() -> dict[str, Any]:
    """Best-effort push of all tracked files, then end session."""
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
    for child in root.iterdir():
        if child.is_dir() and (child / "data").is_dir():
            paths.append(f"{child.name}/data/{CATEGORIZED}")
            paths.append(f"{child.name}/data/{PERSONAL_CATEGORIES}")
    push = push_paths(paths)
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
    while not _worker_stop.wait(1.5):
        try:
            poll_central_events()
        except Exception:
            pass


def start_event_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="centrale-events", daemon=True)
    _worker_thread.start()


def stop_event_worker() -> None:
    global _worker_thread
    _worker_stop.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=2.0)
    _worker_thread = None
