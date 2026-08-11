"""Build category × person matrix and orchestrate multi-person operations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.paths import PersonPack, bind_person
from app.people import get_person, list_people
from app.settings import get_people, refresh_people


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


def build_matrix(people: list[PersonPack] | None = None) -> dict[str, Any]:
    packs = people if people is not None else get_people()
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
    return {
        "categories": list(cells.keys()) if cells else categories,
        "people": columns,
        "cells": cells,
    }


def recalculate_all() -> dict[str, Any]:
    from app.core.categorize import recategorize_transactions

    packs = refresh_people()
    for pack in packs:
        with bind_person(pack):
            recategorize_transactions()
    return build_matrix(packs)


def refresh_all(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Fetch bank data for every person that does not need consent renewal."""
    from app.core.categorize import process_transactions
    from app.core.single_client import (
        EnableBankingError,
        fetch_transactions,
        needs_consent_renewal,
    )

    packs = refresh_people()
    warnings: list[str] = []
    results: list[dict[str, Any]] = []

    for pack in packs:
        with bind_person(pack):
            try:
                if needs_consent_renewal():
                    warnings.append(
                        f"{pack.short} ({pack.folder_name}): consent renewal required — skipped"
                    )
                    results.append(
                        {
                            "short": pack.short,
                            "skipped": True,
                            "reason": "needs_consent_renewal",
                        }
                    )
                    continue
                fetched = fetch_transactions(date_from=date_from, date_to=date_to)
                totals = process_transactions(fetched.transactions, new_year=False)
                results.append(
                    {
                        "short": pack.short,
                        "skipped": False,
                        "transaction_count": len(fetched.transactions),
                        "date_from": fetched.date_from,
                        "date_to": fetched.date_to,
                        "warnings": fetched.warnings,
                        "account_errors": fetched.account_errors,
                    }
                )
                if fetched.warnings:
                    for w in fetched.warnings:
                        warnings.append(f"{pack.short}: {w}")
                if fetched.account_errors:
                    for err in fetched.account_errors:
                        warnings.append(f"{pack.short}: {err}")
                _ = totals
            except EnableBankingError as exc:
                warnings.append(f"{pack.short} ({pack.folder_name}): {exc}")
                results.append(
                    {
                        "short": pack.short,
                        "skipped": True,
                        "reason": str(exc),
                    }
                )
            except Exception as exc:
                warnings.append(f"{pack.short} ({pack.folder_name}): {exc}")
                results.append(
                    {
                        "short": pack.short,
                        "skipped": True,
                        "reason": str(exc),
                    }
                )

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
