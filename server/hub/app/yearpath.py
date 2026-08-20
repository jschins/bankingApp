"""Year folders replace ``data/``: ``<person>/<YYYY>/`` for JSON and xlsx.

``personal_categories.json`` lives in ``<person>/secret/``.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

SECRET_DIRNAME = "secret"
YEAR_MIN = 1990
YEAR_MAX = 2100
CATEGORIZED_FILENAME = "categorized_transactions.json"
CATEGORY_TOTALS_FILENAME = "category_totals.json"
DOWNLOADED_FILENAME = "downloaded_transactions.json"


def current_year() -> str:
    return str(datetime.now().year)


def default_upload_year() -> str:
    """Current year, except in January when uploads typically belong to the previous year."""
    now = datetime.now()
    return str(now.year - 1 if now.month == 1 else now.year)


def is_year_name(name: str) -> bool:
    return name.isdigit() and len(name) == 4 and YEAR_MIN <= int(name) <= YEAR_MAX


def parse_year(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return current_year()
    if not is_year_name(text):
        raise ValueError(f"Invalid year: {raw!r}")
    return text


def list_year_names(person_folder: Path) -> list[str]:
    if not person_folder.is_dir():
        return []
    names = [child.name for child in person_folder.iterdir() if child.is_dir() and is_year_name(child.name)]
    return sorted(names)


def year_dir(person_folder: Path, year: str | None = None) -> Path:
    return person_folder / parse_year(year)


def has_person_layout(person_folder: Path) -> bool:
    if not person_folder.is_dir():
        return False
    if (person_folder / SECRET_DIRNAME).is_dir():
        return True
    return bool(list_year_names(person_folder))


def personal_categories_path(person_folder: Path) -> Path:
    return person_folder / SECRET_DIRNAME / "personal_categories.json"


def previous_year_name(person_folder: Path, year: str | None = None) -> str | None:
    """Latest existing year folder strictly older than ``year``."""
    y = parse_year(year)
    older = [name for name in list_year_names(person_folder) if name < y]
    return older[-1] if older else None


def _zero_categories(categories_path: Path, prev_totals: dict[str, Any]) -> dict[str, str]:
    names: list[str] = []
    if categories_path.is_file():
        try:
            payload = json.loads(categories_path.read_text(encoding="utf-8"))
            categories = payload.get("categories") if isinstance(payload, dict) else None
            if isinstance(categories, dict):
                names = [str(name) for name in categories]
        except (OSError, json.JSONDecodeError):
            names = []
    if not names:
        existing = prev_totals.get("categories")
        if isinstance(existing, dict):
            names = [str(name) for name in existing]
    return {name: "0.00" for name in names}


def _opening_accounts(prev_totals: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = prev_totals.get("account_balances")
    if not isinstance(accounts, list) or not accounts:
        return [
            {
                "iban": "onbekend",
                "name": "onbekend",
                "currency": "EUR",
                "balance": "0.00",
                "files": [],
            }
        ]
    out: list[dict[str, Any]] = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "iban": str(item.get("iban") or "onbekend"),
                "name": str(item.get("name") or "onbekend"),
                "currency": str(item.get("currency") or "EUR"),
                "balance": str(item.get("balance") or "0.00"),
                "files": [],
            }
        )
    return out or [
        {
            "iban": "onbekend",
            "name": "onbekend",
            "currency": "EUR",
            "balance": "0.00",
            "files": [],
        }
    ]


def _load_totals(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sync_category_names(totals_path: Path, categories_path: Path) -> None:
    """Add any category names present in categories.json but missing from totals."""
    totals = _load_totals(totals_path)
    existing = totals.get("categories")
    if not isinstance(existing, dict):
        return
    all_names = _zero_categories(categories_path, totals)
    missing = {k: v for k, v in all_names.items() if k not in existing}
    if not missing:
        return
    existing.update(missing)
    totals_path.write_text(
        json.dumps(totals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def ensure_year_folder(
    person_folder: Path,
    year: str | None = None,
    *,
    categories_path: Path,
    include_downloaded: bool = True,
) -> Path:
    """Create ``person/Y`` with empty books and the previous year's closing balance.

    Creates the person folder and year folder when missing. Never creates the
    parent workspace folder — that must already exist on disk.

    Idempotent: if the year directory already exists, it is left unchanged.
    """
    y = parse_year(year)
    folder = person_folder / y
    if folder.is_dir():
        _sync_category_names(folder / CATEGORY_TOTALS_FILENAME, categories_path)
        return folder

    workspace = person_folder.parent
    if not workspace.is_dir():
        raise FileNotFoundError(
            f"Workspace folder does not exist: {workspace}. "
            "The hub does not create workspace folders; only person packs inside them."
        )

    prev = previous_year_name(person_folder, y)
    prev_totals = _load_totals(person_folder / prev / CATEGORY_TOTALS_FILENAME) if prev else {}
    totals = {
        "categories": _zero_categories(categories_path, prev_totals),
        "account_balances": _opening_accounts(prev_totals),
    }
    categorized = {"transactions": [], "modifications": []}

    person_folder.mkdir(exist_ok=True)
    folder.mkdir(exist_ok=True)
    if include_downloaded:
        (folder / DOWNLOADED_FILENAME).write_text("[]\n", encoding="utf-8")
    (folder / CATEGORIZED_FILENAME).write_text(
        json.dumps(categorized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (folder / CATEGORY_TOTALS_FILENAME).write_text(
        json.dumps(totals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return folder
