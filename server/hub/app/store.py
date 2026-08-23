"""Workspace file I/O, per-file revisions, and change events."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime import data_root
from app.yearpath import has_person_layout, is_year_name, parse_year

_lock = threading.Lock()
# label -> last_seen monotonic time (force-kill never calls session/end)
_local_sessions: dict[str, float] = {}
_SESSION_TTL_SEC = 20.0
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
_YEAR_FILES = frozenset({CATEGORIZED, CATEGORY_TOTALS, DOWNLOADED})
_PERSON_DATA_FILES = _YEAR_FILES | {PERSONAL_CATEGORIES}

# Back-compat alias (older event viewers / docs).
MERGED_WORKSPACE = SHARED_META_WORKSPACE


def _prune_sessions_unlocked(now: float | None = None) -> None:
    cutoff = (now if now is not None else time.monotonic()) - _SESSION_TTL_SEC
    stale = [label for label, seen in _local_sessions.items() if seen < cutoff]
    for label in stale:
        _local_sessions.pop(label, None)


def get_status() -> dict[str, Any]:
    with _lock:
        _prune_sessions_unlocked()
        return {
            "local_sessions": sorted(_local_sessions.keys()),
            "event_count": len(_events),
            "latest_event_id": (_events[-1]["id"] if _events else 0),
            "workspaces": list_workspaces(),
            "session_ttl_sec": _SESSION_TTL_SEC,
        }


def local_session_start(client_addr: str) -> dict[str, Any]:
    """Register / refresh a connected client (``ip:port (workspace)``)."""
    label = _clean_client_addr(client_addr)
    with _lock:
        _prune_sessions_unlocked()
        _local_sessions[label] = time.monotonic()
    return get_status()


def local_session_end(client_addr: str) -> dict[str, Any]:
    label = _clean_client_addr(client_addr)
    with _lock:
        _local_sessions.pop(label, None)
        _prune_sessions_unlocked()
    return get_status()


def clear_local_sessions(label: str | None = None) -> dict[str, Any]:
    """Drop one session label, or all sessions when ``label`` is empty."""
    with _lock:
        if label and label.strip():
            _local_sessions.pop(_clean_client_addr(label), None)
        else:
            _local_sessions.clear()
        _prune_sessions_unlocked()
    return get_status()


def _clean_client_addr(client_addr: str) -> str:
    label = (client_addr or "").strip()
    if not label:
        raise ValueError("client address is required")
    if len(label) > 128 or "\n" in label or "\r" in label:
        raise ValueError(f"Invalid client address: {client_addr!r}")
    return label


def _clean_workspace(workspace: str) -> str:
    ws = workspace.strip().replace("\\", "/").strip("/")
    if not ws or ".." in ws.split("/") or ws.startswith("/"):
        raise ValueError(f"Invalid workspace: {workspace!r}")
    return ws


def workspace_dir(workspace: str) -> Path:
    """Path to an existing-or-not workspace folder (never created here)."""
    base = data_root()
    ws = _clean_workspace(workspace)
    if base.name.lower() == ws.lower():
        return base
    return base / ws


def require_workspace_dir(workspace: str) -> Path:
    """Return the workspace folder, or raise if it is missing.

    Workspace directories are created outside the hub (by an admin on disk).
    The hub only scaffolds person packs *inside* an existing workspace.
    """
    path = workspace_dir(workspace)
    if not path.is_dir():
        raise FileNotFoundError(
            f"Workspace {workspace!r} does not exist under {data_root()}. "
            "Create the workspace folder on disk first; the hub does not initialize workspaces."
        )
    return path


def list_workspaces() -> list[str]:
    """Peer workspace folder names under the data root (e.g. dkg, jl, gph).

    Lists directories that already exist on disk (including empty ones).
    Does not create workspace folders. Skips known meta dirs/names.
    """
    skip = frozenset({"upload_acl.json", "users.db", "upload.log", "categories.json"})
    root = data_root()
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if child.name in skip:
            continue
        names.append(child.name)
    return names


def list_person_folders(workspace: str) -> list[str]:
    root = workspace_dir(workspace)
    if not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and has_person_layout(child):
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


def person_year_rel(person: str, filename: str, *, year: str | None = None) -> str:
    return f"{Path(person).name}/{parse_year(year)}/{filename}"


def person_secret_rel(person: str, filename: str) -> str:
    return f"{Path(person).name}/secret/{filename}"


def _is_tracked(rel_path: str) -> bool:
    p = _normalize_rel_path(rel_path)
    if p == SHARED_CATEGORIES:
        return True
    parts = p.split("/")
    if len(parts) == 3 and is_year_name(parts[1]) and parts[2] in _YEAR_FILES:
        return True
    if len(parts) == 3 and parts[1] == "secret" and parts[2] == PERSONAL_CATEGORIES:
        return True
    return False


def _path_triggers_recalc(rel_path: str) -> bool:
    p = _normalize_rel_path(rel_path)
    if p == SHARED_CATEGORIES:
        return True
    parts = p.split("/")
    if len(parts) == 3 and is_year_name(parts[1]) and parts[2] in (CATEGORIZED, DOWNLOADED):
        return True
    if len(parts) == 3 and parts[1] == "secret" and parts[2] == PERSONAL_CATEGORIES:
        return True
    return False


def recalculate_workspace(
    workspace: str,
    *,
    skip_events: bool = False,
    person_folders: list[str] | None = None,
) -> dict[str, Any]:
    """Run boekhouding-style recalculate for one workspace under data_root.

    When ``person_folders`` is set, only those person packs are recategorized
    and only their derived files are re-published.
    """
    from app.matrix import recalculate_all
    from app.paths import CALC_LOCK
    from app.runtime import set_active_workspace
    from app.settings import init_app

    ws = _clean_workspace(workspace)
    wanted = {Path(name).name for name in person_folders} if person_folders else None
    with CALC_LOCK:
        set_active_workspace(ws)
        init_app()
        matrix = recalculate_all(person_folders=list(wanted) if wanted else None)
        root = workspace_dir(ws)
        for child in root.iterdir():
            if not child.is_dir() or not has_person_layout(child):
                continue
            if wanted is not None and child.name not in wanted:
                continue
            year = parse_year(None)
            totals_path = child / year / CATEGORY_TOTALS
            if totals_path.is_file():
                try:
                    content = json.loads(totals_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                put_file(
                    ws,
                    person_year_rel(child.name, CATEGORY_TOTALS, year=year),
                    content,
                    source="central",
                    skip_recalc=True,
                    skip_event=skip_events,
                )
            cat_path = child / year / CATEGORIZED
            if cat_path.is_file():
                try:
                    content = json.loads(cat_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                put_file(
                    ws,
                    person_year_rel(child.name, CATEGORIZED, year=year),
                    content,
                    source="central",
                    skip_recalc=True,
                    skip_event=skip_events,
                )
        return {"ok": True, "workspace": ws, "matrix": matrix}


def derived_paths_for_workspace(workspace: str) -> list[str]:
    """categorized_transactions + category_totals for every person in ``workspace``."""
    ws = _clean_workspace(workspace)
    paths: list[str] = []
    for name in list_person_folders(ws):
        paths.append(f"{ws}/{person_year_rel(name, CATEGORIZED)}")
        paths.append(f"{ws}/{person_year_rel(name, CATEGORY_TOTALS)}")
    return paths


def derived_paths_for_person(workspace: str, folder_name: str) -> list[str]:
    ws = _clean_workspace(workspace)
    safe = Path(folder_name).name
    return [
        f"{ws}/{person_year_rel(safe, CATEGORIZED)}",
        f"{ws}/{person_year_rel(safe, CATEGORY_TOTALS)}",
    ]


def _normalize_input_path(raw: str, primary: str) -> str:
    p = str(raw).replace("\\", "/").lstrip("/")
    if p == SHARED_CATEGORIES:
        return SHARED_CATEGORIES
    if any(p.startswith(f"{w}/") for w in list_workspaces()):
        return p
    return f"{primary}/{p}"


def _person_folder_from_path(path: str, workspace: str) -> str | None:
    """Return person folder from ``ws/person/YYYY/...`` or ``.../secret/...`` paths."""
    p = str(path).replace("\\", "/").lstrip("/")
    if p == SHARED_CATEGORIES:
        return None
    prefix = f"{_clean_workspace(workspace)}/"
    if p.startswith(prefix):
        p = p[len(prefix) :]
    parts = p.split("/")
    if len(parts) >= 2 and (parts[1] == "secret" or is_year_name(parts[1])):
        return Path(parts[0]).name
    return None


def _mutation_scope(
    primary: str,
    input_paths: list[str],
    *,
    recalc_all_workspaces: bool,
) -> tuple[list[str], list[str] | None, bool]:
    """Return (expected announce paths, person_folders or None=all, multi-workspace)."""
    expected: list[str] = []
    person_folders: set[str] = set()
    scope_all_people = False

    normalized = [_normalize_input_path(raw, primary) for raw in input_paths]
    if not normalized:
        scope_all_people = True

    for p in normalized:
        expected.append(p)
        if p == SHARED_CATEGORIES:
            scope_all_people = True
            continue
        folder = _person_folder_from_path(p, primary)
        if folder:
            person_folders.add(folder)
        else:
            scope_all_people = True

    multi = bool(recalc_all_workspaces or SHARED_CATEGORIES in normalized)
    targets = list_workspaces() if multi else [primary]
    if primary not in targets:
        targets.insert(0, primary)

    if scope_all_people or not person_folders:
        for ws in targets:
            expected.extend(derived_paths_for_workspace(ws))
        return expected, None, multi

    # Person-scoped: only that pack's derived files in the primary workspace.
    for folder in sorted(person_folders):
        expected.extend(derived_paths_for_person(primary, folder))
    return expected, sorted(person_folders), False


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in paths:
        p = str(raw).replace("\\", "/").strip().lstrip("/")
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def announce_mutation(
    workspace: str,
    paths: list[str],
    *,
    source: str = "central",
) -> list[str]:
    """Broadcast deduped file paths for client / hub notification chips."""
    ws = _clean_workspace(workspace)
    unique = _dedupe_paths(paths)
    if not unique:
        return []
    with _lock:
        meta = dict(
            _file_meta.get(_meta_key(SHARED_META_WORKSPACE, "mutation"))
            or {"revision": 0}
        )
        new_rev = int(meta.get("revision") or 0) + 1
        _file_meta[_meta_key(SHARED_META_WORKSPACE, "mutation")] = {
            "revision": new_rev,
            "source": source,
            "mtime": time.time(),
        }
        _append_event_unlocked(
            workspace=ws,
            file_path="mutation",
            source=source,
            revision=new_rev,
            display_path=unique[0] if len(unique) == 1 else f"{len(unique)} files",
            broadcast=True,
            affected_files=unique,
        )
    return unique


def mutate_and_recalculate(
    workspace: str,
    input_paths: list[str],
    *,
    source: str = "central",
    recalc_all_workspaces: bool = False,
) -> dict[str, Any]:
    """Announce expected files, recalculate affected person(s)/workspace(s), return matrix."""
    from app.paths import CALC_LOCK

    primary = _clean_workspace(workspace)
    expected, person_folders, multi = _mutation_scope(
        primary,
        input_paths,
        recalc_all_workspaces=recalc_all_workspaces,
    )
    targets = list_workspaces() if multi else [primary]
    if primary not in targets:
        targets.insert(0, primary)

    announced = announce_mutation(primary, expected, source=source)
    matrices: dict[str, Any] = {}
    with CALC_LOCK:
        for ws in targets:
            # Person-scoped edits only recalculate those packs (and only on primary).
            folders = person_folders if (person_folders and ws == primary) else (
                None if person_folders is None else []
            )
            if folders == []:
                continue
            matrices[ws] = recalculate_workspace(
                ws,
                skip_events=True,
                person_folders=folders,
            )
    primary_result = matrices.get(primary) or {}
    matrix_payload = primary_result.get("matrix")
    if isinstance(matrix_payload, dict) and "workspace" not in matrix_payload:
        matrix_payload = {**matrix_payload, "workspace": primary}
    return {
        "ok": True,
        "workspace": primary,
        "affected_files": announced,
        "matrix": matrix_payload,
        "recalculated": list(matrices.keys()),
    }


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
    skip_event: bool = False,
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
        display = SHARED_CATEGORIES if rel == SHARED_CATEGORIES else f"{ws}/{rel}"
        event = None
        if not skip_event:
            event = _append_event_unlocked(
                workspace=meta_ws if rel == SHARED_CATEGORIES else ws,
                file_path=rel,
                source=source,
                revision=new_rev,
                display_path=display,
                broadcast=rel == SHARED_CATEGORIES,
                affected_files=[display],
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
            result["recalculate"] = recalculate_workspace(ws, skip_events=True)
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
    affected_files: list[str] | None = None,
) -> dict[str, Any]:
    global _next_event_id
    files = _dedupe_paths(affected_files or ([display_path] if display_path else [file_path]))
    event = {
        "id": _next_event_id,
        "workspace": workspace,
        "file_path": file_path,
        "display_path": display_path or f"{workspace}/{file_path}",
        "source": source,
        "revision": revision,
        "broadcast": broadcast,
        "affected_files": files,
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
    """Filter change events for clients.

    Broadcast mutations (``affected_files``) are visible to every viewer.
    """
    with _lock:
        events = list(_events)

    out: list[dict[str, Any]] = []
    for ev in events:
        if int(ev["id"]) <= since_id:
            continue
        if ev.get("broadcast"):
            out.append(ev)
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
        data = root / name / parse_year(None)
        secret = root / name / "secret"
        people[name] = {
            "categorized_transactions": _read_json_or_none(data / CATEGORIZED),
            "personal_categories": _read_json_or_none(secret / PERSONAL_CATEGORIES),
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
                    f"{person_year_rel(safe, CATEGORIZED)}",
                    files["categorized_transactions"],
                    source=source,
                )
            if files.get("personal_categories") is not None:
                put_file(
                    workspace,
                    person_secret_rel(safe, PERSONAL_CATEGORIES),
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
