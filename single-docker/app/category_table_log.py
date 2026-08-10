"""Append-only diagnostic log for category → P-table production."""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_LOG_NAME = "category_table.log"


def log_path() -> Path:
    """``data/category_table.log`` next to the executable (or project root in dev)."""
    from app.runtime import data_dir

    return data_dir() / _LOG_NAME


def clear_log() -> Path:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def log(step: str, **fields: Any) -> None:
    """Append one timestamped step. ``fields`` are rendered as key=value."""
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    parts = [f"[{stamp}] {step}"]
    for key, value in fields.items():
        parts.append(f"{key}={_fmt(value)}")
    line = " | ".join(parts)
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def log_exception(step: str, exc: BaseException, **fields: Any) -> None:
    log(step, error=f"{type(exc).__name__}: {exc}", **fields)
    path = log_path()
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
            handle.write("\n")


def _fmt(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, Path):
        return str(value)
    text = str(value)
    if len(text) > 500:
        return text[:500] + "…"
    return text
