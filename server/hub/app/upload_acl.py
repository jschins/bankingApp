"""Token-scoped Excel uploads into a person's data folder."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime import data_root
from app.yearpath import ensure_year_folder, has_person_layout, parse_year

ACL_FILENAME = "upload_acl.json"
USERS_DB_FILENAME = "users.db"
UPLOAD_LOG_FILENAME = "upload.log"
_SKIP_WORKSPACE_NAMES = frozenset(
    {ACL_FILENAME, USERS_DB_FILENAME, UPLOAD_LOG_FILENAME, "categories.json"}
)
_log_lock = threading.Lock()


@dataclass(frozen=True)
class UploadGrant:
    person: str
    token: str
    center: str
    banks: tuple[str, ...] = ()
    bank: str = ""
    csv_format: str = ""

    def year_folder(self, year: str | None = None) -> str:
        return f"{self.center}/{self.person}/{parse_year(year)}"

    def is_bank_grant(self) -> bool:
        return bool(self.banks)

    def effective_bank(self) -> str:
        if self.bank:
            return self.bank.strip()
        if len(self.banks) == 1:
            return self.banks[0]
        return ""

    def normalized_format(self) -> str:
        from app.core.bank_csv import format_for_bank, normalize_upload_format

        if self.csv_format:
            return normalize_upload_format(self.csv_format)
        bank = self.effective_bank()
        if bank:
            return format_for_bank(bank)
        return "excel"

    def format_label(self) -> str:
        from app.core.bank_csv import format_label_for_bank

        bank = self.effective_bank()
        if bank:
            return format_label_for_bank(bank)
        return "Excel"

    def bank_folder(self) -> str:
        return self.effective_bank()


def acl_path() -> Path:
    return data_root() / ACL_FILENAME


def upload_log_path() -> Path:
    return data_root() / UPLOAD_LOG_FILENAME


def _log_upload(*, ip: str, grant: UploadGrant, rel: str, nbytes: int) -> None:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}\t{client_ip(ip)}\t{grant.center}/{grant.person}\t{rel}\t{nbytes}\n"
    path = upload_log_path()
    with _log_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def _normalize_ip(raw: str) -> str:
    host = (raw or "").strip()
    if host in ("::1", "0:0:0:0:0:0:0:1"):
        return "127.0.0.1"
    if host.startswith("::ffff:"):
        return host.split("::ffff:", 1)[-1]
    return host


def _normalize_rel(raw: str) -> str:
    p = (raw or "").strip().replace("\\", "/").lstrip("/")
    if not p or p.endswith("/."):
        raise ValueError(f"Invalid path: {raw!r}")
    parts = [x for x in p.split("/") if x and x != "."]
    if not parts or ".." in parts:
        raise ValueError(f"Invalid path: {raw!r}")
    return "/".join(parts)


def load_acl_document() -> dict[str, Any]:
    path = acl_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _password_hash_by_person_center() -> dict[tuple[str, str], str]:
    from app import user_store

    return user_store.upload_token_by_person_center()


def discover_workspace_persons() -> list[tuple[str, str]]:
    """``(center, person)`` for each person folder under a workspace center."""
    root = data_root()
    if not root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for center_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not center_dir.is_dir() or center_dir.name.startswith("."):
            continue
        if center_dir.name in _SKIP_WORKSPACE_NAMES:
            continue
        for person_dir in sorted(center_dir.iterdir(), key=lambda p: p.name.lower()):
            if not person_dir.is_dir() or person_dir.name.startswith("."):
                continue
            if has_person_layout(person_dir):
                out.append((center_dir.name, person_dir.name))
    return out


def country_workspace_map() -> dict[str, list[str]]:
    """``countries`` from ``upload_acl.json``: country name → workspace folder names."""
    raw = load_acl_document().get("countries")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        name = str(key or "").strip().lower()
        if not name:
            continue
        if isinstance(value, list):
            workspaces = [str(item).strip() for item in value if str(item).strip()]
        else:
            workspaces = []
        out[name] = workspaces
    return out


def workspaces_for_country(country: str) -> list[str] | None:
    """Return workspace list for a known country, or ``None`` if ``country`` is not listed."""
    name = str(country or "").strip().lower()
    if not name:
        return None
    mapping = country_workspace_map()
    if name not in mapping:
        return None
    return list(mapping[name])


def hub_allowed_ips() -> frozenset[str]:
    """IPs allowed to reach the hub at all.

    When ``hub_ips`` in ``upload_acl.json`` is a non-empty list, every request
    (except the bank consent callback and ``/upload``) must come from one of
    those addresses. ``127.0.0.1`` is always included.
    Empty / missing ``hub_ips`` → no hub-wide gate.
    """
    raw = load_acl_document().get("hub_ips")
    if not isinstance(raw, list) or not raw:
        return frozenset()
    ips = {_normalize_ip(str(x)) for x in raw if str(x).strip()}
    ips.discard("")
    ips = {ip for ip in ips if "x" not in ip.lower()}
    if not ips:
        return frozenset()
    ips.add("127.0.0.1")
    return frozenset(ips)


def load_grants(*, force: bool = False) -> list[UploadGrant]:
    """Build upload grants from workspace folders + user-store password hashes."""
    from app.core.bank_csv import discover_person_banks

    del force
    tokens = _password_hash_by_person_center()
    out: list[UploadGrant] = []
    for center, person in discover_workspace_persons():
        token = tokens.get((person, center))
        if not token:
            continue
        banks = discover_person_banks(person, center)
        out.append(
            UploadGrant(
                person=person,
                token=token,
                center=center,
                banks=banks,
            )
        )
    return out


def grant_for_upload(
    grant: UploadGrant,
    *,
    bank: str | None = None,
    folder: str | None = None,
    for_csv: bool = False,
    csv_content: bytes | None = None,
) -> UploadGrant:
    """Resolve bank folder / csv format from the uploaded file (not existing workspace files)."""
    if not for_csv:
        return grant
    from app.core.bank_csv import (
        csv_layout,
        detect_csv_format_from_bytes,
        format_for_bank,
        infer_bank_folder_from_csv,
        person_uses_bank_subfolders,
        validate_bank_folder_name,
    )

    if not csv_content:
        raise ValueError("CSV content is required to detect format")
    detected = detect_csv_format_from_bytes(csv_content)
    if not detected:
        raise ValueError("Not a recognized bank CSV file")

    if not person_uses_bank_subfolders(grant.person, grant.center):
        return replace(grant, csv_format=detected)

    raw = (folder or bank or grant.bank or "").strip()
    if not raw:
        raw = infer_bank_folder_from_csv(csv_content)
    if not raw:
        if len(grant.banks) == 1:
            raw = grant.banks[0]
        else:
            raise ValueError("Enter a folder name for this upload")
    chosen = validate_bank_folder_name(raw)
    expected = format_for_bank(chosen)
    if csv_layout(detected) != csv_layout(expected):
        raise ValueError(f"CSV headers do not match folder {chosen!r}")
    return replace(grant, bank=chosen, csv_format=expected)


def list_grant_folder_options(grant: UploadGrant, *, year: str | None = None) -> list[str]:
    """Folder names for datalist: existing year subfolders + configured modalities."""
    from app.core.bank_csv import bank_modalities, list_year_bank_folders, person_uses_bank_subfolders

    y = parse_year(year)
    person_folder = resolve_under_data_root(f"{grant.center}/{grant.person}")
    year_path = person_folder / y
    existing: list[str] = []
    if person_uses_bank_subfolders(grant.person, grant.center) or grant.banks:
        existing = list_year_bank_folders(year_path)
    modality_folders = sorted(bank_modalities())
    seen: set[str] = set()
    out: list[str] = []
    for name in existing + list(grant.banks) + modality_folders:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def grant_with_folder(
    grant: UploadGrant, *, bank: str | None = None, folder: str | None = None
) -> UploadGrant:
    """Set target bank folder for UI display (no file read)."""
    from app.core.bank_csv import format_for_bank, validate_bank_folder_name

    raw = (folder or bank or "").strip()
    if not raw:
        if len(grant.banks) == 1:
            return replace(grant, bank=grant.banks[0])
        return grant
    chosen = validate_bank_folder_name(raw)
    format_for_bank(chosen)
    return replace(grant, bank=chosen)


def find_grant_by_token(token: str | None) -> UploadGrant | None:
    needle = "" if token is None else str(token)
    for grant in load_grants():
        try:
            if secrets.compare_digest(grant.token, needle):
                return grant
        except ValueError:
            continue
    return None


def grant_token_for_person(person: str, center: str) -> str | None:
    """Upload grant token for ``center/person``, if configured."""
    person_name = person.strip()
    center_name = center.strip()
    if not person_name or not center_name:
        return None
    for grant in load_grants():
        if grant.person == person_name and grant.center == center_name and grant.token:
            return grant.token
    return None


def client_ip(host: str | None) -> str:
    return _normalize_ip(host or "unknown")


def is_upload_http_path(path: str) -> bool:
    """Upload UI + upload API only (not the rest of the hub)."""
    p = path or ""
    return (
        p == "/upload"
        or p.startswith("/api/upload")
        or p.startswith("/upload/api/upload")
    )


def path_allowed(grant: UploadGrant, rel_path: str, *, year: str | None = None) -> bool:
    target = _normalize_rel(rel_path)
    prefix = grant.year_folder(year)
    return target == prefix or target.startswith(prefix + "/")


def normalize_upload_format(fmt: str | None) -> str:
    from app.core.bank_csv import normalize_upload_format as _norm

    return _norm(fmt)


def grant_csv_data_rel(grant: UploadGrant, year: str | None, filename: str) -> str:
    """Relative path for a CSV upload (flat year or ``YYYY/<bank>/``)."""
    from app.core.bank_csv import person_uses_bank_subfolders

    y = parse_year(year)
    bank = grant.bank_folder()
    if person_uses_bank_subfolders(grant.person, grant.center):
        if not bank:
            raise ValueError("Enter a folder name for this upload")
        return f"{grant.year_folder(y)}/{bank}/{filename}"
    return f"{grant.year_folder(y)}/{filename}"


def grant_csv_data_dir(grant: UploadGrant, year: str | None) -> Path:
    from app.core.bank_csv import person_uses_bank_subfolders

    y = parse_year(year)
    base = resolve_under_data_root(grant.year_folder(y))
    if person_uses_bank_subfolders(grant.person, grant.center):
        bank = grant.bank_folder()
        if not bank:
            raise ValueError("Enter a folder name for this upload")
        return base / bank
    return base


def list_grant_upload_files(grant: UploadGrant, *, year: str | None = None) -> list[str]:
    """Basenames of upload data files already in the grant year folder (or bank subfolder)."""
    folder = grant_csv_data_dir(grant, year) if grant.effective_bank() else resolve_under_data_root(
        grant.year_folder(year)
    )
    if not folder.is_dir():
        return []
    if grant.effective_bank():
        patterns = ("*.csv",)
    elif grant.banks:
        patterns = ("*.csv",)
    else:
        patterns = ("*.xlsx",)
    names: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(folder.glob(pattern), key=lambda p: p.name.lower()):
            if path.is_file() and not path.name.startswith("~$") and not path.name.startswith("."):
                if path.name not in seen:
                    seen.add(path.name)
                    names.append(path.name)
    return names


def list_grant_xlsx_files(grant: UploadGrant, *, year: str | None = None) -> list[str]:
    """Back-compat alias — prefer :func:`list_grant_upload_files`."""
    return list_grant_upload_files(grant, year=year)


def _process_excel_upload(grant: UploadGrant, *, year: str | None = None) -> dict[str, Any]:
    from app import store
    from app import workspace_api
    from app.core.excel_import import import_person_excel
    from app.paths import shared_categories_path

    y = parse_year(year)
    data_dir = resolve_under_data_root(grant.year_folder(y))
    info = import_person_excel(data_dir=data_dir, categories_path=shared_categories_path())
    with workspace_api._workspace_scope(grant.center) as ws:
        inputs = workspace_api._ingest_person_data_files(ws, folder_names=[grant.person], year=y)
    mut = store.mutate_and_recalculate(ws, inputs, source="central")
    return {
        "import": info,
        "affected_files": mut.get("affected_files") or [],
        "year": y,
    }


def _process_bank_csv_upload(grant: UploadGrant, *, year: str | None = None) -> dict[str, Any]:
    from app import store
    from app import workspace_api
    from app.core.bank_csv import consolidate_person_year, import_bank_csv_dir, person_uses_bank_subfolders
    from app.paths import shared_categories_path

    y = parse_year(year)
    fmt = grant.normalized_format()
    data_dir = grant_csv_data_dir(grant, y)
    categories_path = shared_categories_path()
    info = import_bank_csv_dir(data_dir, categories_path=categories_path, fmt=fmt)
    consolidation: dict[str, Any] = {"consolidated": False}
    if person_uses_bank_subfolders(grant.person, grant.center):
        person_folder = resolve_under_data_root(f"{grant.center}/{grant.person}")
        consolidation = consolidate_person_year(
            person_folder,
            year=y,
            person=grant.person,
            center=grant.center,
            categories_path=categories_path,
        )
    with workspace_api._workspace_scope(grant.center) as ws:
        inputs = workspace_api._ingest_person_data_files(ws, folder_names=[grant.person], year=y)
    mut = store.mutate_and_recalculate(ws, inputs, source="central")
    return {
        "import": info,
        "consolidation": consolidation,
        "affected_files": mut.get("affected_files") or [],
        "year": y,
        "bank": grant.bank_folder(),
    }


def resolve_under_data_root(rel_path: str) -> Path:
    rel = _normalize_rel(rel_path)
    root = data_root().resolve()
    full = (root / rel).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes data root: {rel_path!r}") from exc
    return full


def save_upload(
    *,
    grant: UploadGrant,
    ip: str,
    rel_path: str,
    content: bytes,
    filename: str | None = None,
    year: str | None = None,
    test: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write into ``{center}/{person}/{year}/{original filename}``.

    File type (Excel vs bank CSV) is determined from the uploaded filename.
    """
    client = client_ip(ip)
    safe_name = Path(filename or "").name if filename else ""
    if not safe_name:
        raise ValueError("filename is required")

    from app.paths import shared_categories_path
    from app.yearpath import previous_year_name

    person_folder = resolve_under_data_root(f"{grant.center}/{grant.person}")
    name_lower = safe_name.lower()
    is_xlsx = name_lower.endswith(".xlsx") or str(rel_path or "").lower().endswith(".xlsx")
    is_csv = name_lower.endswith(".csv") or str(rel_path or "").lower().endswith(".csv")
    created_year = False

    if is_csv:
        grant = grant_for_upload(grant, for_csv=True, csv_content=content)
    elif not is_xlsx:
        raise ValueError("Upload an .xlsx or bank .csv file")

    grant_fmt = grant.normalized_format()

    # --- Test: raw save only -------------------------------------------------
    if test:
        y = parse_year(year)
        if is_csv:
            dest = grant_csv_data_rel(grant, y, safe_name)
        else:
            dest = f"{grant.year_folder(y)}/{safe_name}"
        if not path_allowed(grant, dest, year=y):
            raise PermissionError(f"Path {dest!r} is not allowed for {grant.person}")
        rel = _normalize_rel(dest)
        full = resolve_under_data_root(rel)
        if not (person_folder / y).is_dir():
            ensure_year_folder(
                person_folder,
                y,
                categories_path=shared_categories_path(),
                include_downloaded=False,
            )
        else:
            full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
        _log_upload(ip=client, grant=grant, rel=rel, nbytes=len(content))
        return {
            "ok": True,
            "path": rel,
            "bytes": len(content),
            "via": "test",
            "person": grant.person,
            "year": y,
        }

    from app import store

    if is_csv:
        from app.core.bank_csv import csv_layout

        layout = csv_layout(grant_fmt)
        if layout == "debit_credit":
            from app.core.bos_lloyds_csv_import import first_entry_year_from_csv_bytes as _year_from_csv
        else:
            from app.core.natwest_csv_import import first_entry_year_from_csv_bytes as _year_from_csv

        try:
            sheet_year = _year_from_csv(content)
        except (UnicodeDecodeError, ValueError, OSError) as exc:
            raise ValueError(f"Could not read the bank CSV: {exc}") from exc
        if not sheet_year:
            raise ValueError("No dated transaction entry found in CSV")
        y = parse_year(sheet_year)
        dest = grant_csv_data_rel(grant, y, safe_name)
        year_path = person_folder / y
        data_path = grant_csv_data_dir(grant, y)
        if year_path.is_dir():
            ensure_year_folder(
                person_folder,
                y,
                categories_path=shared_categories_path(),
                include_downloaded=False,
            )
        elif not dry_run:
            ensure_year_folder(
                person_folder,
                y,
                categories_path=shared_categories_path(),
                include_downloaded=False,
            )
            created_year = True
        if not dry_run or data_path.parent.is_dir():
            data_path.mkdir(parents=True, exist_ok=True)
    elif is_xlsx:
        import xml.etree.ElementTree as ET
        import zipfile

        from app.core.excel_import import first_entry_year_from_xlsx_bytes

        try:
            sheet_year = first_entry_year_from_xlsx_bytes(content)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
            raise ValueError(
                "Could not read the Excel file — is it a valid .xlsx?"
            ) from exc
        if not sheet_year:
            raise ValueError("No dated transaction entry found in Excel sheet")
        y = parse_year(sheet_year)
        dest = f"{grant.year_folder(y)}/{safe_name}"
        year_path = person_folder / y
        if year_path.is_dir():
            ensure_year_folder(
                person_folder,
                y,
                categories_path=shared_categories_path(),
                include_downloaded=False,
            )
        elif not dry_run:
            ensure_year_folder(
                person_folder,
                y,
                categories_path=shared_categories_path(),
                include_downloaded=False,
            )
            created_year = True
    else:
        y = parse_year(year)
        dest = (rel_path or "").strip()
        if not dest:
            dest = f"{grant.year_folder(y)}/{safe_name}"
        elif not Path(dest).name and safe_name:
            dest = dest.rstrip("/") + "/" + safe_name
        if not dry_run:
            ensure_year_folder(person_folder, y, categories_path=shared_categories_path())

    if not path_allowed(grant, dest, year=y):
        raise PermissionError(f"Path {dest!r} is not allowed for {grant.person}")

    rel = _normalize_rel(dest)
    full = resolve_under_data_root(rel)
    if not dry_run or full.parent.is_dir():
        full.parent.mkdir(parents=True, exist_ok=True)

    if rel.endswith(".json"):
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON upload is not valid UTF-8 JSON") from exc
        try:
            if rel == store.SHARED_CATEGORIES:
                result = store.put_file(
                    list(store.list_workspaces() or ["dkg"])[0],
                    store.SHARED_CATEGORIES,
                    payload,
                    source="central",
                )
            else:
                parts = rel.split("/")
                if len(parts) < 2:
                    raise ValueError("workspace JSON path must be workspace/…")
                ws, rest = parts[0], "/".join(parts[1:])
                result = store.put_file(ws, rest, payload, source="central")
            payload_out = {
                "ok": True,
                "path": rel,
                "bytes": len(content),
                "via": "store",
                "person": grant.person,
                "result": {
                    "ok": result.get("ok"),
                    "revision": result.get("revision"),
                    "unchanged": result.get("unchanged"),
                    "path": result.get("path"),
                },
            }
            _log_upload(ip=client, grant=grant, rel=rel, nbytes=len(content))
            return payload_out
        except ValueError:
            pass

    if is_xlsx:
        import xml.etree.ElementTree as ET
        import zipfile

        from app.core.excel_import import check_xlsx_balance

        year_path = person_folder / y
        if year_path.is_dir():
            totals_path = year_path / "category_totals.json"
        else:
            prev = previous_year_name(person_folder, y)
            totals_path = (
                (person_folder / prev / "category_totals.json")
                if prev
                else year_path / "category_totals.json"
            )
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            try:
                if dry_run:
                    try:
                        check_xlsx_balance(tmp_path, totals_path)
                    except ValueError as exc:
                        return {
                            "ok": False,
                            "path": rel,
                            "bytes": len(content),
                            "via": "dry_run",
                            "person": grant.person,
                            "year": y,
                            "balance_check": "fail",
                            "detail": str(exc),
                        }
                    return {
                        "ok": True,
                        "path": rel,
                        "bytes": len(content),
                        "via": "dry_run",
                        "person": grant.person,
                        "year": y,
                        "balance_check": "pass",
                    }
                check_xlsx_balance(tmp_path, totals_path)
            except (zipfile.BadZipFile, KeyError, ET.ParseError):
                raise ValueError(
                    "Could not read the Excel file — is it a valid .xlsx?"
                )
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    if dry_run:
        return {
            "ok": True,
            "path": rel,
            "bytes": len(content),
            "via": "dry_run",
            "person": grant.person,
            "year": y,
        }

    full.write_bytes(content)
    _log_upload(ip=client, grant=grant, rel=rel, nbytes=len(content))
    payload_out: dict[str, Any] = {
        "ok": True,
        "path": rel,
        "bytes": len(content),
        "via": "raw",
        "person": grant.person,
        "year": y,
        "year_created": created_year,
    }
    if is_xlsx:
        payload_out["excel"] = _process_excel_upload(grant, year=y)
    elif is_csv:
        payload_out["bank_csv"] = _process_bank_csv_upload(grant, year=y)
        _maybe_record_first_upload_format(grant)
    return payload_out


def _maybe_record_first_upload_format(grant: UploadGrant) -> None:
    """On first flat-layout CSV upload, store detected format in users.db."""
    from app import user_store

    fmt = grant.normalized_format()
    if not fmt:
        return
    user = user_store.find_user(grant.person)
    if user is None:
        return
    if str(user.get("format") or "").strip():
        return
    # Only set when year folder is still flat (no bank subfolders) — empty format
    # otherwise means multi-folder or secret (see server/readme.md).
    from app.core.bank_csv import person_uses_bank_subfolders

    if person_uses_bank_subfolders(grant.person, grant.center):
        return
    user_store.set_user_format(username=grant.person, format=fmt)


def ensure_example_acl() -> Path:
    path = acl_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "hub_ips": ["127.0.0.1"],
        "grants": [],
    }
    path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    return path
