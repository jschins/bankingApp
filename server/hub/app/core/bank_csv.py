"""Bank CSV upload layout: modalities, subfolders, year consolidation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.excel_import import build_category_totals, category_name_map
from app.runtime import data_root

CSV_FORMATS = frozenset({"bos-csv", "lloyds-csv", "rbs-csv", "natwest-csv"})
DEBIT_CREDIT_FORMATS = frozenset({"bos-csv", "lloyds-csv"})
VALUE_BALANCE_FORMATS = frozenset({"rbs-csv", "natwest-csv"})


def normalize_upload_format(fmt: str | None) -> str:
    """``excel`` | ``test`` | ``bos-csv`` | ``lloyds-csv`` | ``rbs-csv`` | ``natwest-csv``."""
    value = str(fmt or "Excel").strip().lower().replace("_", "-")
    if value == "test":
        return "test"
    aliases = {
        "bos-csv": "bos-csv",
        "bos csv": "bos-csv",
        "bos": "bos-csv",
        "lloyds-csv": "lloyds-csv",
        "lloyds csv": "lloyds-csv",
        "lloyds": "lloyds-csv",
        "rbs-csv": "rbs-csv",
        "rbs csv": "rbs-csv",
        "rbs": "rbs-csv",
        "natwest-csv": "natwest-csv",
        "natwest csv": "natwest-csv",
        "natwest": "natwest-csv",
        # legacy
        "bos-lloyds-csv": "lloyds-csv",
        "bos-lloyds csv": "lloyds-csv",
    }
    if value in aliases:
        return aliases[value]
    return "excel"


def is_csv_bank_format(fmt: str) -> bool:
    return normalize_upload_format(fmt) in CSV_FORMATS


def csv_layout(fmt: str) -> str:
    normalized = normalize_upload_format(fmt)
    if normalized in DEBIT_CREDIT_FORMATS:
        return "debit_credit"
    if normalized in VALUE_BALANCE_FORMATS:
        return "value_balance"
    raise ValueError(f"Not a bank CSV format: {fmt!r}")


def _acl_document() -> dict[str, Any]:
    path = data_root() / "upload_acl.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def tx_type_label(fmt: str) -> str:
    normalized = normalize_upload_format(fmt)
    labels = {
        "bos-csv": "BoS-csv",
        "lloyds-csv": "LLOYDS-csv",
        "rbs-csv": "RBS-csv",
        "natwest-csv": "Natwest-csv",
    }
    try:
        return labels[normalized]
    except KeyError as exc:
        raise ValueError(f"Not a bank CSV format: {fmt!r}") from exc


def bank_modalities() -> dict[str, str]:
    """Subfolder name → csv format (from ``upload_acl.json`` ``bank modalities``)."""
    raw = _acl_document().get("bank modalities")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for folder, fmt in raw.items():
        name = str(folder or "").strip()
        if not name:
            continue
        out[name] = normalize_upload_format(str(fmt or ""))
    return out


def validate_bank_folder_name(name: str) -> str:
    folder = str(name or "").strip()
    if not folder or folder in (".", ".."):
        raise ValueError("Folder name is required")
    if "/" in folder or "\\" in folder or ".." in folder:
        raise ValueError(f"Invalid folder name: {name!r}")
    return folder


def format_for_bank(bank: str) -> str:
    """Normalized csv format for a bank subfolder name (incl. ``Natwest_private``)."""
    folder = validate_bank_folder_name(bank)
    modalities = bank_modalities()
    if folder in modalities:
        return modalities[folder]
    best_name = ""
    best_fmt = ""
    for name, fmt in modalities.items():
        if folder == name or folder.startswith(f"{name}_"):
            if len(name) > len(best_name):
                best_name = name
                best_fmt = fmt
    if best_fmt:
        return best_fmt
    known = ", ".join(sorted(modalities))
    raise ValueError(
        f"Folder {folder!r} does not match any bank modality (known: {known})"
    )


def format_label_for_bank(bank: str) -> str:
    """Display label (e.g. ``BoS-csv``) for a bank subfolder name."""
    return tx_type_label(format_for_bank(bank))


def default_bank_folder_for_format(fmt: str) -> str:
    normalized = normalize_upload_format(fmt)
    for folder, mapped in bank_modalities().items():
        if mapped == normalized:
            return folder
    return {
        "bos-csv": "BoS",
        "lloyds-csv": "LLOYDS",
        "rbs-csv": "RBS",
        "natwest-csv": "Natwest",
    }.get(normalized, "")


def person_csv_banks(person: str, center: str) -> list[str]:
    """Distinct bank subfolder names configured for ``person`` via upload grants."""
    from app.upload_acl import load_grants

    banks: list[str] = []
    seen: set[str] = set()
    for grant in load_grants():
        if grant.person != person or grant.center != center:
            continue
        if grant.is_bank_grant():
            for folder in grant.banks:
                if folder not in seen:
                    seen.add(folder)
                    banks.append(folder)
            continue
        if not is_csv_bank_format(grant.format):
            continue
        folder = (grant.bank or default_bank_folder_for_format(grant.format)).strip()
        if not folder or folder in seen:
            continue
        seen.add(folder)
        banks.append(folder)
    return banks


def list_year_bank_folders(year_path: Path) -> list[str]:
    """Bank subfolder names already present under ``YYYY/``."""
    if not year_path.is_dir():
        return []
    return sorted(
        child.name
        for child in year_path.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def person_uses_bank_subfolders(person: str, center: str) -> bool:
    return len(person_csv_banks(person, center)) > 1


def bank_data_dir(person_folder: Path, year: str, *, person: str, center: str, bank: str) -> Path:
    """Directory for CSV + per-bank JSON (subfolder or flat year)."""
    year_path = person_folder / year
    if person_uses_bank_subfolders(person, center):
        return year_path / bank
    return year_path


def list_bank_subdirs(year_path: Path, *, banks: list[str]) -> list[Path]:
    out: list[Path] = []
    for name in banks:
        sub = year_path / name
        if sub.is_dir():
            out.append(sub)
    return out


def consolidate_person_year(
    person_folder: Path,
    *,
    year: str,
    person: str,
    center: str,
    categories_path: Path,
) -> dict[str, Any]:
    """Merge per-bank subfolder JSON into ``YYYY/categorized_transactions.json`` + totals."""
    year_path = person_folder / year
    if not person_uses_bank_subfolders(person, center):
        return {"consolidated": False, "reason": "single bank"}

    bank_names = list_year_bank_folders(year_path)
    subs = list_bank_subdirs(year_path, banks=bank_names)
    if not subs:
        return {"consolidated": False, "reason": "no bank subfolders"}

    all_transactions: list[dict[str, Any]] = []
    all_modifications: list[dict[str, Any]] = []
    all_accounts: list[dict[str, Any]] = []
    sources: list[str] = []

    for sub in subs:
        cat_path = sub / "categorized_transactions.json"
        tot_path = sub / "category_totals.json"
        if cat_path.is_file():
            try:
                cat = json.loads(cat_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cat = {}
            if isinstance(cat, dict):
                txs = cat.get("transactions")
                if isinstance(txs, list):
                    all_transactions.extend(item for item in txs if isinstance(item, dict))
                mods = cat.get("modifications")
                if isinstance(mods, list):
                    all_modifications.extend(item for item in mods if isinstance(item, dict))
        if tot_path.is_file():
            try:
                totals = json.loads(tot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                totals = {}
            if isinstance(totals, dict):
                accounts = totals.get("account_balances")
                if isinstance(accounts, list):
                    all_accounts.extend(item for item in accounts if isinstance(item, dict))
        sources.append(sub.name)

    name_by_code = category_name_map(categories_path)
    consolidated_totals = {
        "categories": build_category_totals(all_transactions, name_by_code),
        "account_balances": all_accounts,
    }
    consolidated_cat = {
        "transactions": all_transactions,
        "modifications": all_modifications,
    }

    cat_out = year_path / "categorized_transactions.json"
    tot_out = year_path / "category_totals.json"
    cat_out.write_text(
        json.dumps(consolidated_cat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tot_out.write_text(
        json.dumps(consolidated_totals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "consolidated": True,
        "banks": sources,
        "transaction_count": len(all_transactions),
        "account_count": len(all_accounts),
    }


def import_bank_csv_dir(
    data_dir: Path,
    *,
    categories_path: Path,
    fmt: str,
) -> dict[str, Any]:
    layout = csv_layout(fmt)
    label = tx_type_label(fmt)
    if layout == "debit_credit":
        from app.core.bos_lloyds_csv_import import import_person_debit_credit_csv

        return import_person_debit_credit_csv(
            data_dir=data_dir, categories_path=categories_path, tx_type=label
        )
    from app.core.natwest_csv_import import import_person_value_balance_csv

    return import_person_value_balance_csv(
        data_dir=data_dir, categories_path=categories_path, tx_type=label
    )


def refresh_bank_csv_year(
    person_folder: Path,
    *,
    year: str,
    person: str,
    center: str,
    categories_path: Path,
) -> dict[str, Any]:
    """Re-import CSV in each bank folder and consolidate when multi-bank."""
    banks = person_csv_banks(person, center)
    if not banks:
        return {"skipped": True, "reason": "no csv bank grants"}

    results: list[dict[str, Any]] = []
    if person_uses_bank_subfolders(person, center):
        year_path = person_folder / year
        for bank in list_year_bank_folders(year_path):
            try:
                fmt = format_for_bank(bank)
            except ValueError:
                continue
            sub = year_path / bank
            from app.core.bos_lloyds_csv_import import list_csv_files as list_dc
            from app.core.natwest_csv_import import list_csv_files as list_vb

            has_csv = bool(list_dc(sub) or list_vb(sub))
            if not has_csv:
                continue
            info = import_bank_csv_dir(sub, categories_path=categories_path, fmt=fmt)
            results.append({"bank": bank, **info})
        consolidation = consolidate_person_year(
            person_folder,
            year=year,
            person=person,
            center=center,
            categories_path=categories_path,
        )
        return {"banks": results, "consolidation": consolidation}

    year_path = person_folder / year
    try:
        fmt = format_for_bank(banks[0])
    except ValueError:
        return {"skipped": True, "reason": "unknown bank format"}
    info = import_bank_csv_dir(year_path, categories_path=categories_path, fmt=fmt)
    return {"banks": [{"bank": banks[0], **info}], "consolidation": {"consolidated": False}}
