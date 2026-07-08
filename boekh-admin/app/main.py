"""FastAPI entrypoint for bankingApp-admin.

Bank data is fetched by the psd2-api collector (``POST /api/refresh``), which
reads each person's consent from bankingApp-server and writes the raw bank files
(``transactions_*``, ``balances_*``, ``details_*``) **straight into local
storage** — the raw data never travels through bankingApp-server, which only holds the
small ``consent.json`` handles. The raw files are then *distilled* into each
person's ``<person>_transactions.json`` (new ids appended, existing ids and
local modifications kept) and the raw sources are deleted — the simplified file
is the durable local record. Every API request reads **live from disk**, so the
listing always reflects the actual disk contents:

  - GET  /api/health                 liveness probe
  - GET  /api/files                  list all on-disk JSON files
  - GET  /api/files/{id}             raw JSON contents of one file (id = "person/name")
  - GET  /api/files/{id}/summary     pandas-derived summary of one file
  - DELETE /api/files/{id}           delete one file from local disk
  - POST /api/reload                 re-distill local raw files, re-read disk
  - POST /api/refresh                fetch from banks into local storage, then distill

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import export, transform
from .config import Settings, get_settings
from .storage_client import StorageClient, StorageError, StoredFile

logger = logging.getLogger("bankingApp-admin")

_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")


def _safe_segment(value: str) -> str:
    # Defensive guard against path traversal when mirroring to local disk.
    return _UNSAFE_SEGMENT.sub("_", Path(value).name).strip()


def _transactions_filename(person: str) -> str:
    """Name of a person's simplified transactions file (e.g. ``as_transactions.json``)."""
    return f"{person}_transactions.json"


def _load_json_file(path: Path) -> Any:
    """Load JSON from ``path``, or ``None`` if it is missing or invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _tx_id_order(transaction: Any) -> int:
    """Sort key putting transactions in descending numeric ``id`` order.

    Real ids are big numeric strings (newest = largest); anything missing or
    non-numeric sorts last.
    """
    tid = transaction.get("id") if isinstance(transaction, dict) else None
    text = str(tid) if tid is not None else ""
    return int(text) if text.isdigit() else -1


def _merge_transactions(existing: Any, incoming: Any) -> dict[str, Any]:
    """Merge server ``incoming`` transactions into the ``existing`` local file.

    Transactions already present locally (matched by ``id``) are kept untouched;
    transactions with a new ``id`` are appended; the combined list is then sorted
    in descending numeric ``id`` order (newest first). The local ``modifications``
    list is preserved exactly and never replaced by the server's copy.
    """
    existing = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}

    existing_tx = existing.get("transactions")
    existing_tx = existing_tx if isinstance(existing_tx, list) else []
    incoming_tx = incoming.get("transactions")
    incoming_tx = incoming_tx if isinstance(incoming_tx, list) else []

    seen = {t.get("id") for t in existing_tx if isinstance(t, dict)}
    merged = list(existing_tx)
    for t in incoming_tx:
        if isinstance(t, dict) and t.get("id") not in seen:
            merged.append(t)
            seen.add(t.get("id"))
    merged.sort(key=_tx_id_order, reverse=True)

    # Start from the local file so its keys (notably user modifications) survive;
    # fall back to the server payload only when there is no local file yet.
    result = dict(existing) if existing else dict(incoming)
    result["transactions"] = merged
    if isinstance(existing.get("modifications"), list):
        result["modifications"] = existing["modifications"]
    return result


def _mirror_to_disk(base: Path, files: list[StoredFile]) -> None:
    """Write every file to ``base/<person>/<name>``, mirroring bankingApp-server's
    layout (e.g. storage/js, storage/as) and never deleting anything.

    Most files overwrite their local copy byte-for-byte. The one exception is a
    person's ``<person>_transactions.json``: it is *merged* into the existing
    file (new ids appended, existing ids and local modifications kept) so a
    server pull only adds transactions and never discards local history.
    """
    for f in files:
        person = _safe_segment(f.person)
        name = _safe_segment(f.name)
        person_dir = base / person
        person_dir.mkdir(parents=True, exist_ok=True)
        target = person_dir / name
        if name == _transactions_filename(person):
            merged = _merge_transactions(_load_json_file(target), f.content)
            target.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            target.write_bytes(f.raw)


def _to_stored(person: str, name: str, path: Path) -> StoredFile:
    raw = path.read_bytes()
    try:
        content: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        content = None
    return StoredFile(
        id=f"{person}/{name}",
        person=person,
        name=name,
        size=len(raw),
        content=content,
        raw=raw,
    )


def _read_local_files(base: Path) -> list[StoredFile]:
    """Read every JSON file currently on the local disk mirror.

    The disk is the source of truth, so the listing reflects exactly what is on
    disk right now: files still on the server and old ones that no longer are,
    minus anything that has been deleted locally.
    """
    files: list[StoredFile] = []
    if not base.is_dir():
        return files
    for person_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for path in sorted(person_dir.glob("*.json")):
            files.append(_to_stored(person_dir.name, path.name, path))
    return files


def _resolve_local_path(base: Path, file_id: str) -> Path | None:
    """Map an id (``person/name``) to a safe local path, or ``None`` if invalid."""
    parts = file_id.split("/")
    if len(parts) != 2:
        return None
    person, name = _safe_segment(parts[0]), _safe_segment(parts[1])
    if not person or not name:
        return None
    return base / person / name


def _read_local_file(base: Path, file_id: str) -> StoredFile | None:
    """Read one file (id = ``person/name``) live from disk, or ``None``."""
    path = _resolve_local_path(base, file_id)
    if path is None or not path.is_file():
        return None
    return _to_stored(path.parent.name, path.name, path)


def _delete_local_file(base: Path, file_id: str) -> bool:
    """Delete one file (id = ``person/name``) from local disk. Returns success."""
    path = _resolve_local_path(base, file_id)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


async def _refresh_from_server(app: FastAPI) -> str | None:
    """Re-distill local raw files; bankingApp-admin holds only its own artifacts.

    bankingApp-server no longer stores raw bank data — the collector writes that
    straight into local storage — and the per-person ``consent.json`` handles it
    does hold are only consumed by ``psd2-api collect`` (read directly from the
    server), so they are **not** mirrored to local disk. This therefore just
    re-distills any raw files already present. The server is still contacted so an
    unreachable server is reported (returns the error message), else ``None``.
    """
    try:
        files = await app.state.storage.load_all()
    except StorageError as exc:
        logger.warning("Could not refresh from storage: %s", exc)
        return str(exc)
    base = get_settings().local_storage_dir
    # Mirror everything except consent.json — bankingApp-admin has no use for it
    # locally and bankingApp-server stays the single source of truth for consent.
    _mirror_to_disk(base, [f for f in files if f.name != "consent.json"])
    export.build_all(storage_dir=base, delete_sources=True)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.storage = StorageClient(get_settings())
    # Always pull all server files and write them locally (overwriting).
    await _refresh_from_server(app)
    count = len(_read_local_files(get_settings().local_storage_dir))
    logger.info("Serving %d file(s) from local disk at startup", count)
    try:
        yield
    finally:
        await app.state.storage.aclose()


app = FastAPI(title="bankingApp-admin", version="0.1", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _meta(f: StoredFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "owner_id": f.person,
        "filename": f.name,
        "content_type": "application/json",
        "size": f.size,
        "created_at": "",
        "stored_path": f.id,
    }


def _get_file(file_id: str) -> StoredFile:
    f = _read_local_file(get_settings().local_storage_dir, file_id)
    if f is None:
        raise HTTPException(status_code=404, detail=f"file {file_id!r} not found")
    return f


@app.get("/api/health", tags=["health"])
def health() -> dict[str, Any]:
    settings: Settings = get_settings()
    return {
        "status": "ok",
        "service": "bankingApp-admin",
        "storage_base_url": settings.storage_base_url,
        "local_storage_dir": str(settings.local_storage_dir),
        "files_on_disk": len(_read_local_files(settings.local_storage_dir)),
    }


@app.get("/api/files", tags=["files"])
def list_files() -> dict[str, Any]:
    files = _read_local_files(get_settings().local_storage_dir)
    return {"count": len(files), "files": [_meta(f) for f in files]}


@app.get("/api/files/{file_id:path}/summary", tags=["files"])
def get_file_summary(file_id: str) -> dict[str, Any]:
    return transform.summarize(_get_file(file_id).content)


@app.get("/api/files/{file_id:path}", tags=["files"])
def get_file(file_id: str) -> Any:
    return _get_file(file_id).content


@app.delete("/api/files/{file_id:path}", tags=["files"])
def delete_file(file_id: str) -> dict[str, Any]:
    # Deletes the local copy only; it is re-pulled on the next refresh if it
    # still exists on the server.
    if not _delete_local_file(get_settings().local_storage_dir, file_id):
        raise HTTPException(status_code=404, detail=f"file {file_id!r} not found")
    return {"id": file_id, "deleted": True}


def _categories_payload(base: Path) -> dict[str, Any]:
    table = export.build_h_table(storage_dir=base)
    return {
        "count": len(table["categories"]),
        **table,
        "abbreviations": export.general_abbreviations(),
        "widths": export.general_widths(),
    }


@app.get("/api/categories", tags=["categories"])
def list_categories() -> dict[str, Any]:
    """Category names plus the consolidation (H) table of per-person totals.

    The table mirrors bankingApp-editor's H table: a ``Category`` column followed by
    one column per person, each cell the signed total (in euros) for that
    category.
    """
    return _categories_payload(get_settings().local_storage_dir)


@app.post("/api/recalculate", tags=["categories"])
def recalculate() -> dict[str, Any]:
    """Re-categorise every person's transactions, then return the fresh H-table.

    Used after editing keyword lists in the settings view so the consolidation
    totals reflect the new categorisation.
    """
    base = get_settings().local_storage_dir
    # Import any raw files still present, then re-apply categorisation to the
    # distilled files so freshly edited keyword lists take effect.
    export.build_all(storage_dir=base)
    export.recategorize_all(storage_dir=base)
    return _categories_payload(base)


@app.get("/api/settings", tags=["categories"])
def settings_view() -> dict[str, Any]:
    """Keyword lists per category (general + per-person) for the S-table."""
    base = get_settings().local_storage_dir
    return export.build_settings(storage_dir=base)


class TermsUpdate(BaseModel):
    terms: list[str]


@app.put("/api/settings/{group}/{category}", tags=["categories"])
def update_settings(group: str, category: str, body: TermsUpdate) -> dict[str, Any]:
    """Save the keyword list for one category of one group (Algemeen or person).

    ``group`` is ``"general"`` or a person's short.
    """
    base = get_settings().local_storage_dir
    if group != "general":
        person = _safe_segment(group)
        if not person or not (base / person).is_dir():
            raise HTTPException(status_code=404, detail=f"person {group!r} not found")
        group = person

    saved = export.update_category_terms(group, category, body.terms, storage_dir=base)
    return {"group": group, "category": category, "terms": saved}


@app.get("/api/transactions/{short}/{category}", tags=["transactions"])
def get_transactions(short: str, category: int) -> dict[str, Any]:
    """Return one person's transactions for a single category code (the P-table).

    Both the person ``short`` and the integer ``category`` code are required.
    """
    base = get_settings().local_storage_dir
    person = _safe_segment(short)
    tx_path = base / person / f"{person}_transactions.json"
    if not person or not tx_path.is_file():
        raise HTTPException(
            status_code=404, detail=f"no transactions for {short!r}"
        )

    payload = json.loads(tx_path.read_text(encoding="utf-8"))
    # Apply modifications (matched by id) so the P-table reflects corrected
    # categories/descriptions, exactly like the O-table totals do.
    raw_mods = payload.get("modifications")
    mods_by_id = {
        m.get("id"): m
        for m in (raw_mods if isinstance(raw_mods, list) else [])
        if isinstance(m, dict)
    }
    effective = [
        mods_by_id.get(t.get("id"), t) for t in payload.get("transactions", [])
    ]
    transactions = [t for t in effective if t.get("category") == category]
    modified_ids = [t.get("id") for t in transactions if t.get("id") in mods_by_id]
    return {
        "short": person,
        "category": category,
        "count": len(transactions),
        "transactions": transactions,
        "modified_ids": modified_ids,
        "keywords": export.keywords_for_category(person, category, storage_dir=base),
    }


class ModificationBody(BaseModel):
    transaction: dict[str, Any]


@app.put("/api/transactions/{short}/modification", tags=["transactions"])
def add_modification(short: str, body: ModificationBody) -> dict[str, Any]:
    """Record a full modified transaction under ``modifications`` for a person."""
    base = get_settings().local_storage_dir
    person = _safe_segment(short)
    tx_path = base / person / f"{person}_transactions.json"
    if not person or not tx_path.is_file():
        raise HTTPException(status_code=404, detail=f"no transactions for {short!r}")

    saved = export.record_modification(person, body.transaction, storage_dir=base)
    return {"short": person, "modification": saved}


@app.post("/api/transactions/{short}", tags=["transactions"])
def build_transactions(short: str) -> dict[str, Any]:
    """Build ``<short>_transactions.json`` for one person (e.g. ``js``, ``as``).

    Reads that person's raw bank transactions file from local disk and writes
    the simplified transactions file into the same folder.
    """
    base = get_settings().local_storage_dir
    person = _safe_segment(short)
    person_dir = base / person
    if not person or not person_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"person {short!r} not found")

    out_path = export.build_person_transactions(person_dir)
    if out_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"no raw transactions file found for {short!r}",
        )
    return {"short": person, "written": f"{person}/{out_path.name}"}


@app.post("/api/reload", tags=["files"])
async def reload(request: Request) -> dict[str, Any]:
    refresh_error = await _refresh_from_server(request.app)
    count = len(_read_local_files(get_settings().local_storage_dir))
    return {"reloaded": True, "count": count, "refresh_error": refresh_error}


async def _run_collect(output_dir: Path) -> dict[str, Any]:
    """Run psd2-api's ``collect --all`` to fetch everyone's bank data.

    The collector reads each person's consent from bankingApp-server and writes the
    raw bank files straight into ``output_dir/<person>/`` (bankingApp-admin's
    storage). Returns a status dict: ``ok`` plus the parsed per-person
    ``summary`` (or an ``error`` message if psd2-api could not run).
    """
    settings = get_settings()
    date_from = (date.today() - timedelta(days=settings.collect_days_back)).isoformat()
    cmd = [
        "uv", "run", "python", "-m", "psd2_api", "collect",
        "--all", "--json", "--date-from", date_from,
        # Absolute: the collector runs with cwd=psd2-api, so a relative path
        # would land under psd2-api instead of bankingApp-admin's storage.
        "--output-dir", str(output_dir.resolve()),
    ]

    def _run() -> subprocess.CompletedProcess[str]:
        # Run blocking in a worker thread: uvicorn's Windows event loop cannot
        # spawn async subprocesses.
        return subprocess.run(
            cmd,
            cwd=str(settings.psd2_api_dir),
            capture_output=True,
            text=True,
            timeout=settings.collect_timeout,
        )

    try:
        proc = await asyncio.to_thread(_run)
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"could not start psd2-api ('uv' on PATH?): {exc}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "bank collection timed out"}

    text = proc.stdout or ""
    summary = None
    # The machine-readable summary is the last JSON object line on stdout.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
                break
            except ValueError:
                continue

    result: dict[str, Any] = {"ok": proc.returncode == 0, "returncode": proc.returncode}
    if summary is not None:
        result["summary"] = summary
    if proc.returncode != 0 and summary is None:
        result["error"] = (proc.stderr or "").strip() or text.strip()
    return result


@app.post("/api/refresh", tags=["files"])
async def refresh(delete_after: bool = True) -> dict[str, Any]:
    """One-button refresh: fetch everyone's bank data straight into local storage.

    Runs psd2-api's collector (per person: read consent from bankingApp-server ->
    fetch from the bank -> write raw files into ``storage/<person>/``), then
    distills them into each ``<person>_transactions.json`` (new ids appended,
    existing ids and local modifications kept). The raw bank data never touches
    bankingApp-server.

    ``delete_after`` (default true) deletes the raw source files once they are
    distilled. Pass ``delete_after=false`` (for debugging) to keep the raw
    ``transactions_*``/``balances_*``/``details_*`` files on local disk.
    """
    base = get_settings().local_storage_dir
    collect = await _run_collect(base)
    # Distill the freshly written raw files in place; keep them only for debug.
    await asyncio.to_thread(export.build_all, storage_dir=base, delete_sources=delete_after)
    count = len(_read_local_files(base))
    return {
        "collect": collect,
        "reloaded": True,
        "count": count,
    }
