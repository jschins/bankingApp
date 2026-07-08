from __future__ import annotations

import json
from pathlib import Path

from app.runtime import app_root
from app.settings import AppSettings, init_app_settings

DATA_DIR: Path = app_root() / "data"
PERSON_SHORT: str = ""
PROFILE_PATH: Path = Path("profile.json")
PRIVATE_KEY_PATH: Path = Path("key.pem")
CONSENT_PATH: Path = DATA_DIR / "consent.json"
CATEGORIES_PATH: Path = DATA_DIR / "categories.json"
PERSONAL_CATEGORIES_PATH: Path = DATA_DIR / "categories.json"
CATEGORIZED_TRANSACTIONS_PATH: Path = DATA_DIR / "categorized_transactions.json"
RAW_TRANSACTIONS_PATH: Path = DATA_DIR / "downloaded_transactions.json"
CATEGORY_TOTALS_PATH: Path = DATA_DIR / "category_totals.json"

LEGACY_PERSONAL_FILENAMES = frozenset(
    {
        "profile.json",
        "consent.json",
        "categorized_transactions.json",
        "downloaded_transactions.json",
        "category_totals.json",
    }
)


def personal_filename(person: str, stem: str) -> str:
    return f"{person}_{stem}"


def _read_person_short(profile_path: Path) -> str:
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    person = str(data.get("person") or "").strip()
    if not person:
        raise ValueError(f"profile missing 'person': {profile_path}")
    return person


def _reject_legacy_files(directory: Path) -> None:
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith("_config.json") or path.name == "config.json":
            continue
        if path.name in LEGACY_PERSONAL_FILENAMES:
            raise RuntimeError(
                f"Legacy unprefixed file {path.name} in {directory}. "
                f"Use {{person}}_{path.name} instead."
            )


def configure(person_short: str | None = None) -> AppSettings:
    """Resolve person, secret, and data paths from app_root (called once at startup)."""
    global DATA_DIR, PERSON_SHORT, PROFILE_PATH, PRIVATE_KEY_PATH, CONSENT_PATH
    global CATEGORIES_PATH, PERSONAL_CATEGORIES_PATH, CATEGORIZED_TRANSACTIONS_PATH
    global RAW_TRANSACTIONS_PATH, CATEGORY_TOTALS_PATH

    settings = init_app_settings(person_short)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _reject_legacy_files(settings.data_dir)

    DATA_DIR = settings.data_dir
    PROFILE_PATH = settings.profile_path
    PRIVATE_KEY_PATH = settings.private_key_path
    PERSON_SHORT = settings.person_short

    if _read_person_short(PROFILE_PATH) != PERSON_SHORT:
        raise ValueError(
            f"profile.person must match resolved person {settings.person_short!r}"
        )

    CONSENT_PATH = DATA_DIR / personal_filename(PERSON_SHORT, "consent.json")
    CATEGORIES_PATH = DATA_DIR / "categories.json"
    PERSONAL_CATEGORIES_PATH = DATA_DIR / personal_filename(PERSON_SHORT, "categories.json")
    CATEGORIZED_TRANSACTIONS_PATH = DATA_DIR / personal_filename(
        PERSON_SHORT, "categorized_transactions.json"
    )
    RAW_TRANSACTIONS_PATH = DATA_DIR / personal_filename(
        PERSON_SHORT, "downloaded_transactions.json"
    )
    CATEGORY_TOTALS_PATH = DATA_DIR / personal_filename(PERSON_SHORT, "category_totals.json")
    return settings
