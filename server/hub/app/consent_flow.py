"""Pending Enable Banking consent renewals (state → person), for hub callback."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_lock = threading.Lock()
# state -> {workspace, short, folder, created}
_pending: dict[str, dict[str, Any]] = {}
_TTL_SEC = 30 * 60


def _prune_unlocked(now: float | None = None) -> None:
    cutoff = (now if now is not None else time.time()) - _TTL_SEC
    stale = [k for k, v in _pending.items() if float(v.get("created") or 0) < cutoff]
    for k in stale:
        _pending.pop(k, None)


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
