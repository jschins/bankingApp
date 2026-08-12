"""Workspace file I/O, per-file revisions, categories merge, and change events."""
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
_merging_categories = False

PERSONAL_CATEGORIES = "personal_categories.json"
CATEGORIZED = "categorized_transactions.json"
SHARED_CATEGORIES = "categories.json"
MERGED_WORKSPACE = "_merged"  # synthetic workspace id for root categories.json events
DELETIONS_FILE = "_categories_deletions.json"


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
        if (child / SHARED_CATEGORIES).is_file() or any(
            p.is_dir() and (p / "data").is_dir() for p in child.iterdir() if p.is_dir()
        ):
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


def merged_categories_path() -> Path:
    """Root-level merged categories.json (one level above peer folders)."""
    return data_root() / SHARED_CATEGORIES


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
    if len(parts) == 3 and parts[1] == "data" and parts[2] in (PERSONAL_CATEGORIES, CATEGORIZED):
        return True
    return False


def _meta_key(workspace: str, rel_path: str) -> str:
    return f"{_clean_workspace(workspace)}/{_normalize_rel_path(rel_path)}"


def resolve_file_path(workspace: str, rel_path: str) -> Path:
    rel = _normalize_rel_path(rel_path)
    if not _is_tracked(rel):
        raise ValueError(f"Path is not a tracked sync file: {rel}")
    return workspace_dir(workspace) / rel


def read_file(workspace: str, rel_path: str) -> dict[str, Any]:
    rel = _normalize_rel_path(rel_path)
    path = resolve_file_path(workspace, rel)
    key = _meta_key(workspace, rel)
    content = _read_json_or_none(path)
    with _lock:
        meta = dict(_file_meta.get(key) or {})
    return {
        "ok": True,
        "workspace": _clean_workspace(workspace),
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
    skip_categories_merge: bool = False,
) -> dict[str, Any]:
    """Write one tracked file. Central always wins when local is behind."""
    if source not in ("local", "central"):
        raise ValueError("source must be 'local' or 'central'")
    rel = _normalize_rel_path(rel_path)
    path = resolve_file_path(workspace, rel)
    key = _meta_key(workspace, rel)
    ws = _clean_workspace(workspace)

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

        new_rev = current_rev + 1
        if rel == SHARED_CATEGORIES and not skip_categories_merge:
            previous = _read_json_or_none(path)
            record_category_term_diff(previous, content)
        _write_json(path, content)
        now = time.time()
        _file_meta[key] = {"revision": new_rev, "source": source, "mtime": now}
        event = _append_event_unlocked(
            workspace=ws,
            file_path=rel,
            source=source,
            revision=new_rev,
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

    if rel == SHARED_CATEGORIES and not skip_categories_merge:
        merge_info = rebuild_merged_categories(trigger_workspace=ws, trigger_source=source)
        result["categories_merge"] = merge_info
    return result


def merge_categories_payloads(
    payloads: list[dict[str, Any]],
    *,
    deletions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Merge peer categories.json documents: union abbreviations and category terms.

    ``deletions`` maps category name → terms that must stay removed across peers.
    """
    merged: dict[str, Any] = {}
    abbr: dict[str, str] = {}
    cats: dict[str, list[str]] = {}
    typerules: list[Any] = []
    other: dict[str, Any] = {}

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        raw_abbr = payload.get("abbreviations")
        if isinstance(raw_abbr, dict):
            for k, v in raw_abbr.items():
                key = str(k)
                if key not in abbr:
                    abbr[key] = str(v)
        raw_cats = payload.get("categories")
        if isinstance(raw_cats, dict):
            for name, terms in raw_cats.items():
                cat = str(name)
                bucket = cats.setdefault(cat, [])
                seen = set(bucket)
                if isinstance(terms, list):
                    for term in terms:
                        t = str(term)
                        if t not in seen:
                            bucket.append(t)
                            seen.add(t)
        raw_rules = payload.get("typerules")
        if isinstance(raw_rules, list):
            for rule in raw_rules:
                if rule not in typerules:
                    typerules.append(rule)
        for k, v in payload.items():
            if k in ("abbreviations", "categories", "typerules"):
                continue
            if k not in other:
                other[k] = v

    if deletions:
        for cat, removed in deletions.items():
            if cat not in cats:
                continue
            ban = {str(t) for t in removed}
            cats[cat] = [t for t in cats[cat] if t not in ban]

    if abbr:
        merged["abbreviations"] = abbr
    merged["categories"] = cats
    if typerules:
        merged["typerules"] = typerules
    merged.update(other)
    return merged


def _deletions_path() -> Path:
    return data_root() / DELETIONS_FILE


def load_category_deletions() -> dict[str, list[str]]:
    raw = _read_json_or_none(_deletions_path())
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for cat, terms in raw.items():
        if isinstance(terms, list):
            cleaned = [str(t) for t in terms if str(t).strip()]
            if cleaned:
                out[str(cat)] = cleaned
    return out


def save_category_deletions(deletions: dict[str, list[str]]) -> None:
    _write_json(_deletions_path(), deletions)


def _category_term_map(payload: Any) -> dict[str, set[str]]:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("categories")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[str]] = {}
    for name, terms in raw.items():
        if isinstance(terms, list):
            out[str(name)] = {str(t) for t in terms if str(t).strip()}
    return out


def record_category_term_diff(old_payload: Any, new_payload: Any) -> dict[str, list[str]]:
    """Update persisted deletions from a peer categories write (removes stay gone; re-adds clear)."""
    old_map = _category_term_map(old_payload)
    new_map = _category_term_map(new_payload)
    deletions = load_category_deletions()

    all_cats = set(old_map) | set(new_map) | set(deletions)
    for cat in all_cats:
        before = old_map.get(cat, set())
        after = new_map.get(cat, set())
        removed = before - after
        added = after - before
        bucket = set(deletions.get(cat, []))
        bucket |= removed
        bucket -= added
        if bucket:
            deletions[cat] = sorted(bucket)
        else:
            deletions.pop(cat, None)

    save_category_deletions(deletions)
    return deletions


def rebuild_merged_categories(
    *,
    trigger_workspace: str | None = None,
    trigger_source: str = "central",
) -> dict[str, Any]:
    """Rebuild root categories.json from all peers and push the merge to every peer."""
    global _merging_categories
    if _merging_categories:
        return {"ok": True, "skipped": True, "reason": "merge already in progress"}

    _merging_categories = True
    try:
        peers = list_workspaces()
        payloads: list[dict[str, Any]] = []
        for peer in peers:
            raw = _read_json_or_none(workspace_dir(peer) / SHARED_CATEGORIES)
            if isinstance(raw, dict):
                payloads.append(raw)
        deletions = load_category_deletions()
        merged = merge_categories_payloads(payloads, deletions=deletions)
        root_path = merged_categories_path()
        _write_json(root_path, merged)

        with _lock:
            key = f"{MERGED_WORKSPACE}/{SHARED_CATEGORIES}"
            meta = dict(_file_meta.get(key) or {"revision": 0})
            new_rev = int(meta.get("revision") or 0) + 1
            _file_meta[key] = {
                "revision": new_rev,
                "source": "central",
                "mtime": time.time(),
            }
            event = _append_event_unlocked(
                workspace=MERGED_WORKSPACE,
                file_path=SHARED_CATEGORIES,
                source="central",
                revision=new_rev,
                display_path=SHARED_CATEGORIES,
                broadcast=True,
            )

        peer_results: list[dict[str, Any]] = []
        for peer in peers:
            peer_results.append(
                put_file(
                    peer,
                    SHARED_CATEGORIES,
                    merged,
                    source="central",
                    skip_categories_merge=True,
                )
            )

        return {
            "ok": True,
            "merged_revision": new_rev,
            "peers": peers,
            "trigger_workspace": trigger_workspace,
            "trigger_source": trigger_source,
            "deletions": deletions,
            "event": event,
            "peer_writes": [
                {"workspace": r.get("workspace"), "revision": r.get("revision"), "ok": r.get("ok")}
                for r in peer_results
            ],
        }
    finally:
        _merging_categories = False


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

    - central: all ``source=local`` events
    - local: ``source=central`` person-file events for that workspace, plus
      broadcast merged ``categories.json`` events (all peers)
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
            # Merged categories → notify every peer.
            if ev.get("broadcast") or (
                ev["file_path"] == SHARED_CATEGORIES and ev["workspace"] == MERGED_WORKSPACE
            ):
                out.append(ev)
                continue
            # Skip per-peer categories writes that are part of merge fan-out;
            # peers react to the broadcast merged event instead.
            if ev["file_path"] == SHARED_CATEGORIES:
                continue
            if ev["workspace"] == ws:
                out.append(ev)
        else:
            raise ValueError("viewer must be 'central' or 'local'")

    return {"events": out, "latest_id": (events[-1]["id"] if events else 0)}


def read_workspace_files(workspace: str) -> dict[str, Any]:
    root = workspace_dir(workspace)
    categories = _read_json_or_none(root / SHARED_CATEGORIES)
    people: dict[str, Any] = {}
    for name in list_person_folders(workspace):
        data = root / name / "data"
        people[name] = {
            "categorized_transactions": _read_json_or_none(data / CATEGORIZED),
            "personal_categories": _read_json_or_none(data / PERSONAL_CATEGORIES),
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
