"""Storage layer: JSON files on local disk, one folder per person.

Each person (e.g. ``js``, ``as``) gets a subdirectory under
``settings.storage_dir``. Files are plain ``.json`` blobs written and read as
bytes; the server only validates that the payload is well-formed JSON.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import get_settings

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_segment(value: str) -> str:
    # Drop any directory components a client might send (path-traversal guard),
    # then keep only a filesystem-friendly character set.
    name = Path(value).name
    return _UNSAFE.sub("_", name).strip()


def _person_dir(person: str) -> Path:
    safe = _safe_segment(person)
    if not safe:
        raise ValueError("invalid person")
    return get_settings().storage_dir / safe


def save_json(person: str, name: str, data: bytes) -> str:
    safe_name = _safe_segment(name)
    if not safe_name:
        raise ValueError("invalid name")
    if not safe_name.lower().endswith(".json"):
        safe_name = f"{safe_name}.json"

    person_dir = _person_dir(person)
    person_dir.mkdir(parents=True, exist_ok=True)
    (person_dir / safe_name).write_bytes(data)
    return safe_name


def read_json(person: str, name: str) -> bytes | None:
    path = _person_dir(person) / _safe_segment(name)
    if not path.is_file():
        return None
    return path.read_bytes()


def delete_json(person: str, name: str) -> bool:
    path = _person_dir(person) / _safe_segment(name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_json(person: str) -> list[str]:
    person_dir = _person_dir(person)
    if not person_dir.is_dir():
        return []
    return sorted(p.name for p in person_dir.glob("*.json"))


def list_people() -> list[str]:
    base = get_settings().storage_dir
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())
