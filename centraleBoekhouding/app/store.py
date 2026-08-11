"""In-memory lock state and selected-file I/O under workspace folders."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.runtime import data_root

_lock = threading.Lock()
_central_admin_logged_in = False
_local_sessions: set[str] = set()

PERSONAL_CATEGORIES = "personal_categories.json"
CATEGORIZED = "categorized_transactions.json"
SHARED_CATEGORIES = "categories.json"


def get_lock_state() -> dict[str, Any]:
    with _lock:
        return _lock_state_unlocked()


def _lock_state_unlocked() -> dict[str, Any]:
    return {
        "central_admin_logged_in": _central_admin_logged_in,
        "local_sessions": sorted(_local_sessions),
    }


def central_login() -> dict[str, Any]:
    global _central_admin_logged_in
    with _lock:
        _central_admin_logged_in = True
        return _lock_state_unlocked()


def central_logout() -> dict[str, Any]:
    global _central_admin_logged_in
    with _lock:
        _central_admin_logged_in = False
        return _lock_state_unlocked()


def local_login(workspace: str) -> dict[str, Any]:
    ws = _clean_workspace(workspace)
    with _lock:
        _local_sessions.add(ws)
        return _lock_state_unlocked()


def local_logout(workspace: str) -> dict[str, Any]:
    ws = _clean_workspace(workspace)
    with _lock:
        _local_sessions.discard(ws)
        return _lock_state_unlocked()


def _clean_workspace(workspace: str) -> str:
    ws = workspace.strip().replace("\\", "/").strip("/")
    if not ws or ".." in ws.split("/") or ws.startswith("/"):
        raise ValueError(f"Invalid workspace: {workspace!r}")
    return ws


def workspace_dir(workspace: str) -> Path:
    return data_root() / _clean_workspace(workspace)


def list_person_folders(workspace: str) -> list[str]:
    root = workspace_dir(workspace)
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and (child / "data").is_dir():
            names.append(child.name)
    return names


def read_workspace_files(workspace: str) -> dict[str, Any]:
    """Return selected files for a workspace.

    Shape::

        {
          "categories": { ... } | null,
          "people": {
             "juleon_schins": {
               "categorized_transactions": {...} | null,
               "personal_categories": {...} | null
             },
             ...
          }
        }
    """
    root = workspace_dir(workspace)
    categories_path = root / SHARED_CATEGORIES
    categories = _read_json_or_none(categories_path)
    people: dict[str, Any] = {}
    for name in list_person_folders(workspace):
        data = root / name / "data"
        people[name] = {
            "categorized_transactions": _read_json_or_none(data / CATEGORIZED),
            "personal_categories": _read_json_or_none(data / PERSONAL_CATEGORIES),
        }
    return {"workspace": _clean_workspace(workspace), "categories": categories, "people": people}


def write_workspace_files(workspace: str, payload: dict[str, Any]) -> dict[str, Any]:
    root = workspace_dir(workspace)
    root.mkdir(parents=True, exist_ok=True)
    if "categories" in payload and payload["categories"] is not None:
        _write_json(root / SHARED_CATEGORIES, payload["categories"])
    people = payload.get("people")
    if isinstance(people, dict):
        for person, files in people.items():
            if not isinstance(files, dict):
                continue
            safe = Path(person).name
            data_dir = root / safe / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            if "categorized_transactions" in files and files["categorized_transactions"] is not None:
                _write_json(data_dir / CATEGORIZED, files["categorized_transactions"])
            if "personal_categories" in files and files["personal_categories"] is not None:
                _write_json(data_dir / PERSONAL_CATEGORIES, files["personal_categories"])
    return read_workspace_files(workspace)


def _read_json_or_none(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
