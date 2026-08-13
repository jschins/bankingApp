"""Pending Enable Banking consent renewals (state → person), for hub callback."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_lock = threading.Lock()
# state -> {workspace, short, folder, created}
_pending: dict[str, dict[str, Any]] = {}
# workspace|short -> {workspace, short, folder, created} after successful callback
_ready: dict[str, dict[str, Any]] = {}
_TTL_SEC = 30 * 60
_READY_TTL_SEC = 60 * 60


def _prune_unlocked(now: float | None = None) -> None:
    t = now if now is not None else time.time()
    cutoff = t - _TTL_SEC
    stale = [k for k, v in _pending.items() if float(v.get("created") or 0) < cutoff]
    for k in stale:
        _pending.pop(k, None)
    ready_cutoff = t - _READY_TTL_SEC
    ready_stale = [
        k for k, v in _ready.items() if float(v.get("created") or 0) < ready_cutoff
    ]
    for k in ready_stale:
        _ready.pop(k, None)


def register_pending(
    *,
    workspace: str,
    short: str,
    folder: str,
    state: str | None = None,
) -> str:
    """Remember which person an authorization ``state`` belongs to."""
    token = (state or "").strip() or uuid.uuid4().hex
    with _lock:
        _prune_unlocked()
        _pending[token] = {
            "workspace": workspace,
            "short": short,
            "folder": folder,
            "created": time.time(),
        }
        # Also keep a single "latest" slot for callbacks whose state we cannot match.
        _pending["__latest__"] = {
            "workspace": workspace,
            "short": short,
            "folder": folder,
            "created": time.time(),
            "state": token,
        }
    return token


def take_pending(state: str | None) -> dict[str, Any] | None:
    """Pop pending entry for ``state`` (one-shot); fall back to latest if needed."""
    key = (state or "").strip()
    with _lock:
        _prune_unlocked()
        if key and key in _pending and key != "__latest__":
            _pending.pop("__latest__", None)
            return _pending.pop(key, None)
        latest = _pending.pop("__latest__", None)
        if latest:
            real = str(latest.get("state") or "").strip()
            if real:
                _pending.pop(real, None)
            return {
                "workspace": latest.get("workspace"),
                "short": latest.get("short"),
                "folder": latest.get("folder"),
                "created": latest.get("created"),
            }
        return None


def _ready_key(workspace: str, short: str) -> str:
    return f"{(workspace or '').strip().lower()}|{(short or '').strip().lower()}"


def mark_ready(*, workspace: str, short: str, folder: str) -> None:
    """Record that bank consent for this person was completed via callback."""
    with _lock:
        _prune_unlocked()
        _ready[_ready_key(workspace, short)] = {
            "workspace": workspace,
            "short": short,
            "folder": folder,
            "created": time.time(),
        }


def list_ready(workspace: str | None = None) -> list[dict[str, Any]]:
    """Return completed consents, optionally filtered to one workspace."""
    with _lock:
        _prune_unlocked()
        items = list(_ready.values())
    if workspace is None:
        return items
    needle = workspace.strip().lower()
    return [x for x in items if str(x.get("workspace") or "").strip().lower() == needle]


def clear_ready(*, workspace: str, short: str) -> bool:
    """Drop a ready marker after the person-only fetch (or cancel)."""
    with _lock:
        return _ready.pop(_ready_key(workspace, short), None) is not None
