"""Workspace file I/O, per-file revisions, and change events."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime import data_root

_lock = threading.Lock()
_local_sessions: set[str] = set()
_file_meta: dict[str, dict[str, Any]] = {}  # key -> {revision, source, mtime}
_events: list[dict[str, Any]] = []
_next_event_id = 1
_MAX_EVENTS = 200

PERSONAL_CATEGORIES = "personal_categories.json"
CATEGORIZED = "categorized_transactions.json"
CATEGORY_TOTALS = "category_totals.json"
DOWNLOADED = "downloaded_transactions.json"
SHARED_CATEGORIES = "categories.json"
# Synthetic workspace id for events on the single root categories.json
SHARED_META_WORKSPACE = "_shared"
_PERSON_DATA_FILES = frozenset(
    {PERSONAL_CATEGORIES, CATEGORIZED, CATEGORY_TOTALS, DOWNLOADED}
)

# Back-compat alias (older event viewers / docs).
MERGED_WORKSPACE = SHARED_META_WORKSPACE


def get_status() -> dict[str, Any]:
    with _lock:
        return {
            "local_sessions": sorted(_local_sessions),
            "event_count": len(_events),
            "latest_event_id": (_events[-1]["id"] if _events else 0),
            "workspaces": list_workspaces(),
        }


def local_session_start(workspace: str) -> dict[str, Any]:
    ws = _clean_workspace(workspace)
    with _lock:
        _local_sessions.add(ws)
    return get_status()


def local_session_end(workspace: str) -> dict[str, Any]:
    ws = _clean_workspace(workspace)
    with _lock:
        _local_sessions.discard(ws)
    return get_status()


def _clean_workspace(workspace: str) -> str:
    ws = workspace.strip().replace("\\", "/").strip("/")
    if not ws or ".." in ws.split("/") or ws.startswith("/"):
        raise ValueError(f"Invalid workspace: {workspace!r}")
    return ws


def workspace_dir(workspace: str) -> Path:
    base = data_root()
    ws = _clean_workspace(workspace)
    if base.name.lower() == ws.lower():
        return base
    return base / ws


def list_workspaces() -> list[str]:
    """Peer workspace folder names under the data root (e.g. dkg, jl)."""
    root = data_root()
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if any(p.is_dir() and (p / "data").is_dir() for p in child.iterdir() if p.is_dir()):
            names.append(child.name)
    return names


def list_person_folders(workspace: str) -> list[str]:
    root = workspace_dir(workspace)
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and (child / "data").is_dir():
            names.append(child.name)
    return names


def shared_categories_path() -> Path:
    """Single ``categories.json`` at the hub data root (all workspaces)."""
    return data_root() / SHARED_CATEGORIES


def merged_categories_path() -> Path:
    """Alias for ``shared_categories_path``."""
    return shared_categories_path()


def _normalize_rel_path(rel_path: str) -> str:
    p = rel_path.strip().replace("\\", "/").lstrip("/")
    if not p or ".." in p.split("/"):
        raise ValueError(f"Invalid path: {rel_path!r}")
    return p


def _is_tracked(rel_path: str) -> bool:
    p = _normalize_rel_path(rel_path)
    if p == SHARED_CATEGORIES:
        return True
    parts = p.split("/")
    if len(parts) == 3 and parts[1] == "data" and parts[2] in _PERSON_DATA_FILES:
        return True
    return False


def _path_triggers_recalc(rel_path: str) -> bool:
    p = _normalize_rel_path(rel_path)
    if p == SHARED_CATEGORIES:
        return True
    parts = p.split("/")
    if len(parts) == 3 and parts[1] == "data" and parts[2] in (
        PERSONAL_CATEGORIES,
        CATEGORIZED,
        DOWNLOADED,
    ):
        return True
    return False


def recalculate_workspace(workspace: str) -> dict[str, Any]:
    """Run boekhouding-style recalculate for one workspace under data_root."""
    from app.matrix import recalculate_all
    from app.runtime import set_active_workspace
    from app.settings import init_app

    ws = _clean_workspace(workspace)
    set_active_workspace(ws)
    init_app()
    matrix = recalculate_all()
    # Publish derived totals so clients can pull them.
    root = workspace_dir(ws)
    for child in root.iterdir():
        if not child.is_dir() or not (child / "data").is_dir():
            continue
        totals_path = child / "data" / CATEGORY_TOTALS
        if totals_path.is_file():
            try:
                content = json.loads(totals_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            put_file(
                ws,
                f"{child.name}/data/{CATEGORY_TOTALS}",
                content,
                source="central",
                skip_recalc=True,
            )
        cat_path = child / "data" / CATEGORIZED
        if cat_path.is_file():
            try:
                content = json.loads(cat_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            put_file(
                ws,
                f"{child.name}/data/{CATEGORIZED}",
                content,
                source="central",
                skip_recalc=True,
            )
    return {"ok": True, "workspace": ws, "matrix": matrix}


def _meta_key(workspace: str, rel_path: str) -> str:
    return f"{_clean_workspace(workspace)}/{_normalize_rel_path(rel_path)}"


def resolve_file_path(workspace: str, rel_path: str) -> Path:
    rel = _normalize_rel_path(rel_path)
    if not _is_tracked(rel):
        raise ValueError(f"Path is not a tracked sync file: {rel}")
    if rel == SHARED_CATEGORIES:
        return shared_categories_path()
    return workspace_dir(workspace) / rel


def read_file(workspace: str, rel_path: str) -> dict[str, Any]:
    rel = _normalize_rel_path(rel_path)
    path = resolve_file_path(workspace, rel)
    ws = _clean_workspace(workspace)
    meta_ws = SHARED_META_WORKSPACE if rel == SHARED_CATEGORIES else ws
    key = _meta_key(meta_ws, rel)
    content = _read_json_or_none(path)
    with _lock:
        meta = dict(_file_meta.get(key) or {})
    return {
        "ok": True,
        "workspace": ws,
        "path": rel,
        "content": content,
        "revision": int(meta.get("revision") or 0),
        "source": meta.get("source"),
        "mtime": meta.get("mtime"),
    }


def put_file(
    workspace: str,
    rel_path: str,
    content: Any,
    *,
    source: str,
    client_revision: int | None = None,
    skip_recalc: bool = False,
) -> dict[str, Any]:
    """Write one tracked file. Central always wins when local is behind."""
    if source not in ("local", "central"):
        raise ValueError("source must be 'local' or 'central'")
    rel = _normalize_rel_path(rel_path)
    path = resolve_file_path(workspace, rel)
    ws = _clean_workspace(workspace)
    meta_ws = SHARED_META_WORKSPACE if rel == SHARED_CATEGORIES else ws
    key = _meta_key(meta_ws, rel)

    with _lock:
        meta = dict(_file_meta.get(key) or {"revision": 0, "source": None, "mtime": 0.0})
        current_rev = int(meta.get("revision") or 0)
        last_source = meta.get("source")

        if source == "local" and current_rev > 0:
            base = 0 if client_revision is None else int(client_revision)
            if base < current_rev:
                existing = _read_json_or_none(path)
                return {
                    "ok": False,
                    "central_wins": True,
                    "workspace": ws,
                    "path": rel,
                    "content": existing,
                    "revision": current_rev,
                    "source": last_source,
                }

        existing = _read_json_or_none(path)
        if _json_equal(existing, content):
            return {
                "ok": True,
                "central_wins": False,
                "unchanged": True,
                "workspace": ws,
                "path": rel,
                "content": existing,
                "revision": current_rev,
                "source": last_source,
            }

        new_rev = current_rev + 1
        _write_json(path, content)
        now = time.time()
        _file_meta[key] = {"revision": new_rev, "source": source, "mtime": now}
        event = _append_event_unlocked(
            workspace=meta_ws,
            file_path=rel,
            source=source,
            revision=new_rev,
            display_path=SHARED_CATEGORIES if rel == SHARED_CATEGORIES else None,
            broadcast=rel == SHARED_CATEGORIES,
        )
        result = {
            "ok": True,
            "central_wins": False,
            "workspace": ws,
            "path": rel,
            "content": content,
            "revision": new_rev,
            "source": source,
            "event": event,
        }

    if not skip_recalc and _path_triggers_recalc(rel) and not result.get("central_wins"):
        try:
            result["recalculate"] = recalculate_workspace(ws)
        except Exception as exc:  # noqa: BLE001
            result["recalculate_error"] = str(exc)
    return result


def _append_event_unlocked(
    *,
    workspace: str,
    file_path: str,
    source: str,
    revision: int,
    display_path: str | None = None,
    broadcast: bool = False,
) -> dict[str, Any]:
    global _next_event_id
    event = {
        "id": _next_event_id,
        "workspace": workspace,
        "file_path": file_path,
        "display_path": display_path or f"{workspace}/{file_path}",
        "source": source,
        "revision": revision,
        "broadcast": broadcast,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _next_event_id += 1
    _events.append(event)
    if len(_events) > _MAX_EVENTS:
        del _events[: len(_events) - _MAX_EVENTS]
    return event


def list_events(
    *,
    since_id: int = 0,
    viewer: str = "central",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Filter change events for central UI or a local peer.

    - central: all ``source=local`` events (optional workspace filter)
    - local: ``source=central`` person-file events for that workspace, plus
      broadcast ``categories.json`` events (shared root file)
    """
    with _lock:
        events = list(_events)

    out: list[dict[str, Any]] = []
    for ev in events:
        if int(ev["id"]) <= since_id:
            continue
        if viewer == "central":
            if ev["source"] != "local":
                continue
            if workspace and ev["workspace"] != _clean_workspace(workspace):
                continue
            out.append(ev)
        elif viewer == "local":
            if not workspace:
                raise ValueError("workspace is required for viewer=local")
            ws = _clean_workspace(workspace)
            if ev["source"] != "central":
                continue
            # Shared root categories.json → notify every peer.
            if ev.get("broadcast") or (
                ev["file_path"] == SHARED_CATEGORIES
                and ev["workspace"] in (SHARED_META_WORKSPACE, MERGED_WORKSPACE)
            ):
                out.append(ev)
                continue
            if ev["file_path"] == SHARED_CATEGORIES:
                continue
            if ev["workspace"] == ws:
                out.append(ev)
        else:
            raise ValueError("viewer must be 'central' or 'local'")

    return {"events": out, "latest_id": (events[-1]["id"] if events else 0)}


def read_workspace_files(workspace: str) -> dict[str, Any]:
    root = workspace_dir(workspace)
    categories = _read_json_or_none(merged_categories_path())
    people: dict[str, Any] = {}
    for name in list_person_folders(workspace):
        data = root / name / "data"
        people[name] = {
            "categorized_transactions": _read_json_or_none(data / CATEGORIZED),
            "personal_categories": _read_json_or_none(data / PERSONAL_CATEGORIES),
            "category_totals": _read_json_or_none(data / CATEGORY_TOTALS),
            "downloaded_transactions": _read_json_or_none(data / DOWNLOADED),
        }
    return {"workspace": _clean_workspace(workspace), "categories": categories, "people": people}


def write_workspace_files(
    workspace: str,
    payload: dict[str, Any],
    *,
    source: str = "local",
) -> dict[str, Any]:
    if "categories" in payload and payload["categories"] is not None:
        put_file(workspace, SHARED_CATEGORIES, payload["categories"], source=source)
    people = payload.get("people")
    if isinstance(people, dict):
        for person, files in people.items():
            if not isinstance(files, dict):
                continue
            safe = Path(person).name
            if files.get("categorized_transactions") is not None:
                put_file(
                    workspace,
                    f"{safe}/data/{CATEGORIZED}",
                    files["categorized_transactions"],
                    source=source,
                )
            if files.get("personal_categories") is not None:
                put_file(
                    workspace,
                    f"{safe}/data/{PERSONAL_CATEGORIES}",
                    files["personal_categories"],
                    source=source,
                )
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


def _json_equal(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
            b, sort_keys=True, ensure_ascii=False
        )
    except (TypeError, ValueError):
        return False
