"""Build category × person matrix and orchestrate multi-person operations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.paths import PersonPack, bind_person
from app.people import get_person, list_people
from app.settings import get_people, refresh_people

BANK_SALDO_CATEGORY = "banksaldo"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _category_map(data: dict[str, Any]) -> dict[str, list[str]]:
    nested = data.get("categories")
    return nested if isinstance(nested, dict) else data


def general_categories_source(people: list[PersonPack] | None = None) -> Path:
    from app.paths import shared_categories_path

    path = shared_categories_path()
    if path.is_file():
        return path
    raise FileNotFoundError(
        f"No categories.json found beside the admin root ({path})."
    )


def category_names(people: list[PersonPack] | None = None) -> list[str]:
    path = general_categories_source(people)
    general = _category_map(_read_json(path))
    return list(general.keys())


def sync_general_categories(payload: dict[str, Any], people: list[PersonPack] | None = None) -> None:
    """Write the shared ``categories.json`` at the boekhouding deploy root."""
    from app.paths import shared_categories_path

    _write_json(shared_categories_path(), payload)


def load_general_file(people: list[PersonPack] | None = None) -> dict[str, Any]:
    path = general_categories_source(people)
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def person_totals(pack: PersonPack) -> dict[str, str]:
    from app.core.categorize import load_category_totals, recategorize_transactions

    with bind_person(pack):
        totals = load_category_totals()
        if not totals:
            totals = recategorize_transactions()
        return totals


def person_current_balance(pack: PersonPack) -> str | None:
    """Sum of ``account_balances`` in category_totals.json, or None if absent."""
    from app.core.categorize import _load_json_object
    import app.paths as paths

    with bind_person(pack):
        data = _load_json_object(paths.CATEGORY_TOTALS_PATH)
    accounts = data.get("account_balances")
    if not isinstance(accounts, list) or not accounts:
        return None
    cents = 0
    found = False
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        text = str(acc.get("balance") or "").strip()
        if not text:
            continue
        try:
            cents += round(float(text) * 100)
        except ValueError:
            continue
        found = True
    if not found:
        return None
    return f"{cents / 100:.2f}"


def build_matrix(
    people: list[PersonPack] | None = None,
    *,
    year: str | None = None,
) -> dict[str, Any]:
    from app.runtime import active_workspace

    if people is not None:
        packs = people
    elif year is not None:
        # Year-specific matrix: include only persons with that year folder.
        packs = list_people(year=year)
    else:
        packs = get_people()
    categories = category_names(packs)
    columns = [{"short": p.short, "folder": p.folder_name} for p in packs]
    cells: dict[str, dict[str, str]] = {name: {} for name in categories}
    for pack in packs:
        totals = person_totals(pack)
        for name in categories:
            cells[name][pack.short] = str(totals.get(name, "0.00"))
        # Include any unexpected keys from totals (should not happen)
        for name, amount in totals.items():
            if name not in cells:
                cells[name] = {p.short: "0.00" for p in packs}
                cells[name][pack.short] = str(amount)
    category_list = list(cells.keys()) if cells else list(categories)
    balance_row: dict[str, str] = {}
    has_balance = False
    for pack in packs:
        balance = person_current_balance(pack)
        if balance is not None:
            has_balance = True
            balance_row[pack.short] = balance
        else:
            balance_row[pack.short] = ""
    if has_balance:
        cells[BANK_SALDO_CATEGORY] = balance_row
        if BANK_SALDO_CATEGORY not in category_list:
            category_list.append(BANK_SALDO_CATEGORY)
    payload: dict[str, Any] = {
        "categories": category_list,
        "people": columns,
        "cells": cells,
    }
    ws = active_workspace()
    if ws:
        payload["workspace"] = ws
    return payload


def recalculate_all(person_folders: list[str] | None = None) -> dict[str, Any]:
    """Recategorize people; when ``person_folders`` is set, only those packs are rewritten."""
    from app.core.categorize import recategorize_transactions
    from app.paths import CALC_LOCK

    with CALC_LOCK:
        packs = refresh_people()
        if person_folders:
            wanted = {Path(name).name for name in person_folders}
            to_run = [p for p in packs if p.folder_name in wanted]
        else:
            to_run = packs
        for pack in to_run:
            if not pack.has_secret_folder:
                continue
            with bind_person(pack):
                recategorize_transactions()
        return build_matrix(packs)


def _excel_refresh_result(pack: PersonPack) -> dict[str, Any]:
    from app.core.excel_import import import_person_excel, list_xlsx_files
    from app.core.natwest_csv_import import import_person_natwest_csv, list_csv_files
    from app.paths import shared_categories_path

    categories_path = shared_categories_path()
    if list_csv_files(pack.data_dir):
        info = import_person_natwest_csv(data_dir=pack.data_dir, categories_path=categories_path)
        return {
            "short": pack.short,
            "folder": pack.folder_name,
            "skipped": False,
            "source": "natwest-csv",
            "transaction_count": info.get("transaction_count", 0),
            "files": info.get("files") or [],
            "new_files": info.get("new_files") or [],
            "balance_updated": bool(info.get("balance_updated")),
            "balance": info.get("balance"),
            "file_errors": info.get("file_errors") or [],
        }
    if not list_xlsx_files(pack.data_dir):
        return {
            "short": pack.short,
            "folder": pack.folder_name,
            "skipped": True,
            "source": "excel",
            "reason": "no xlsx or csv files",
        }
    info = import_person_excel(data_dir=pack.data_dir, categories_path=categories_path)
    return {
        "short": pack.short,
        "folder": pack.folder_name,
        "skipped": False,
        "source": "excel",
        "transaction_count": info.get("transaction_count", 0),
        "files": info.get("files") or [],
        "new_files": info.get("new_files") or [],
        "balance_updated": bool(info.get("balance_updated")),
        "balance": info.get("balance"),
        "file_errors": info.get("file_errors") or [],
    }


def _bank_refresh_one(
    pack: PersonPack,
    *,
    date_from: str | None,
    date_to: str | None,
    new_year: bool,
) -> tuple[dict[str, Any], list[str]]:
    from app.core.categorize import process_transactions
    from app.core.single_client import (
        fetch_transactions,
        get_authorization_url,
        needs_consent_renewal,
    )
    from app.runtime import active_workspace

    warnings: list[str] = []
    if needs_consent_renewal():
        auth_url: str | None = None
        try:
            auth_url = get_authorization_url(
                workspace=active_workspace(),
                person_short=pack.short,
                folder=pack.folder_name,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"{pack.short} ({pack.folder_name}): "
                f"consent renewal required — could not get authorization URL ({exc})"
            )
        else:
            warnings.append(
                f"{pack.short} ({pack.folder_name}): consent renewal required — skipped"
            )
        return (
            {
                "short": pack.short,
                "folder": pack.folder_name,
                "skipped": True,
                "reason": "needs_consent_renewal",
                "authorization_url": auth_url,
            },
            warnings,
        )

    fetched = fetch_transactions(date_from=date_from, date_to=date_to)
    process_transactions(fetched.transactions, new_year=bool(new_year))
    if fetched.warnings:
        for w in fetched.warnings:
            warnings.append(f"{pack.short}: {w}")
    if fetched.account_errors:
        for err in fetched.account_errors:
            warnings.append(f"{pack.short}: {err}")
    result: dict[str, Any] = {
        "short": pack.short,
        "folder": pack.folder_name,
        "skipped": False,
        "source": "bank",
        "transaction_count": len(fetched.transactions),
        "date_from": fetched.date_from,
        "date_to": fetched.date_to,
        "warnings": fetched.warnings,
        "account_errors": fetched.account_errors,
    }
    if new_year:
        result["new_year"] = True
    return result, warnings


def _refresh_one_person(
    pack: PersonPack,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    new_year: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    from app.core.single_client import EnableBankingError

    try:
        if pack.has_secret_folder:
            return _bank_refresh_one(
                pack, date_from=date_from, date_to=date_to, new_year=new_year
            )
        excel = _excel_refresh_result(pack)
        extra = [
            f"{pack.short} ({pack.folder_name}): {err}"
            for err in (excel.get("file_errors") or [])
            if str(err).strip()
        ]
        return excel, extra
    except EnableBankingError as exc:
        return (
            {
                "short": pack.short,
                "folder": pack.folder_name,
                "skipped": True,
                "reason": str(exc),
            },
            [f"{pack.short} ({pack.folder_name}): {exc}"],
        )
    except Exception as exc:
        return (
            {
                "short": pack.short,
                "folder": pack.folder_name,
                "skipped": True,
                "reason": str(exc),
            },
            [f"{pack.short} ({pack.folder_name}): {exc}"],
        )


def refresh_all(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Bank fetch for secret packs; Excel conversion for everyone else."""
    from app.paths import CALC_LOCK

    with CALC_LOCK:
        packs = refresh_people()
        warnings: list[str] = []
        results: list[dict[str, Any]] = []

        for pack in packs:
            with bind_person(pack):
                result, extra = _refresh_one_person(
                    pack, date_from=date_from, date_to=date_to, new_year=False
                )
                results.append(result)
                warnings.extend(extra)

        matrix = build_matrix(packs)
        return {"matrix": matrix, "results": results, "warnings": warnings}


def refresh_person(
    short: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    new_year: bool = False,
) -> dict[str, Any]:
    """Refresh one person (bank fetch or Excel conversion)."""
    from app.paths import CALC_LOCK

    with CALC_LOCK:
        packs = refresh_people()
        pack = get_person(short)
        warnings: list[str] = []
        results: list[dict[str, Any]] = []

        with bind_person(pack):
            result, extra = _refresh_one_person(
                pack, date_from=date_from, date_to=date_to, new_year=new_year
            )
            results.append(result)
            warnings.extend(extra)

        matrix = build_matrix(packs)
        return {"matrix": matrix, "results": results, "warnings": warnings}


def save_general_terms(category_name: str, terms: list[str]) -> list[str]:
    from app.core.categorize import _cleaned_terms, _category_map

    packs = get_people()
    original = _read_json(general_categories_source(packs))
    if not isinstance(original, dict):
        original = {}
    cleaned = _cleaned_terms(terms)
    if isinstance(original.get("categories"), dict):
        original["categories"][category_name] = cleaned
    else:
        categories = _category_map(original)
        categories[category_name] = cleaned
        original = categories
    sync_general_categories(original, packs)
    return cleaned


def save_personal_terms(short: str, category_name: str, terms: list[str]) -> list[str]:
    from app.core.categorize import _cleaned_terms, _save_personal_category_terms

    pack = get_person(short)
    with bind_person(pack):
        cleaned = _cleaned_terms(terms)
        _save_personal_category_terms(category_name, cleaned)
        return cleaned
