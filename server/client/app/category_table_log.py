"""Optional diagnostic log for the admin app (writes under app_root)."""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_LOG_NAME = "boekhouding.log"
# Set True to write boekhouding.log under the workspace folder.
DEBUG = False


def log_path() -> Path:
    from app.runtime import app_root

    return app_root() / _LOG_NAME


def clear_log() -> Path:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def log(step: str, **fields: Any) -> None:
    if not DEBUG:
        return
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    parts = [f"[{stamp}] {step}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 500:
            text = text[:500] + "…"
        parts.append(f"{key}={text}")
    line = " | ".join(parts)
    path = log_path()
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def log_exception(step: str, exc: BaseException, **fields: Any) -> None:
    if not DEBUG:
        return
    log(step, error=f"{type(exc).__name__}: {exc}", **fields)
    path = log_path()
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
            handle.write("\n")
