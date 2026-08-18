"""Year folders replace ``data/``: ``<person>/<YYYY>/`` for JSON and xlsx.

``personal_categories.json`` lives in ``<person>/secret/``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

SECRET_DIRNAME = "secret"
YEAR_MIN = 1990
YEAR_MAX = 2100


def current_year() -> str:
    return str(datetime.now().year)


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
