from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(".")
INPUT_DIR = Path("input")
BOTH_DIR = Path("both")
OUTPUT_DIR = Path("output")
PERSON_SHORT = ""
PROFILE_PATH = Path("profile.json")
CONSENT_PATH = Path("consent.json")
CATEGORIES_PATH = Path("categories.json")
PERSONAL_CATEGORIES_PATH = Path("categories.json")
CATEGORIZED_TRANSACTIONS_PATH = Path("categorized_transactions.json")
RAW_TRANSACTIONS_PATH = Path("downloaded_transactions.json")
CATEGORY_TOTALS_PATH = Path("category_totals.json")

LEGACY_PERSONAL_FILENAMES = frozenset(
    {
        "profile.json",
        "consent.json",
        "categorized_transactions.json",
        "downloaded_transactions.json",
        "category_totals.json",
    }
)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def personal_filename(person: str, stem: str) -> str:
    return f"{person}_{stem}"


def _read_person_short(profile_path: Path) -> str:
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    person = str(data.get("person") or "").strip()
    if not person:
        raise ValueError(f"profile missing 'person': {profile_path}")
    return person


def _reject_legacy_files(*directories: Path) -> None:
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.name in LEGACY_PERSONAL_FILENAMES:
                raise RuntimeError(
                    f"Legacy unprefixed file {path.name} in {directory}. "
                    f"Use {{person}}_{path.name} instead."
                )


def _resolve_profile_path(input_dir: Path) -> Path:
    override = os.environ.get("bankingApp_PERSON", "").strip()
    if override:
        path = input_dir / personal_filename(override, "profile.json")
        if path.exists():
            return path
        raise FileNotFoundError(
            f"Profile not found for bankingApp_PERSON={override!r}: {path}"
        )

    prefixed = sorted(input_dir.glob("*_profile.json"))
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) > 1:
        names = ", ".join(p.name for p in prefixed)
        raise FileNotFoundError(
            f"Multiple profiles in {input_dir}: {names}. Set bankingApp_PERSON."
        )

    raise FileNotFoundError(
        f"No {{person}}_profile.json found in {input_dir}. "
        "Legacy profile.json is not supported."
    )


def init_paths() -> None:
    """Resolve layout directories and person-prefixed file paths."""
    global ROOT_DIR, INPUT_DIR, BOTH_DIR, OUTPUT_DIR, PERSON_SHORT
    global PROFILE_PATH, CONSENT_PATH, CATEGORIES_PATH, PERSONAL_CATEGORIES_PATH
    global CATEGORIZED_TRANSACTIONS_PATH, RAW_TRANSACTIONS_PATH, CATEGORY_TOTALS_PATH

    ROOT_DIR = app_root()
    INPUT_DIR = ROOT_DIR / "input"
    BOTH_DIR = ROOT_DIR / "both"
    OUTPUT_DIR = ROOT_DIR / "output"

    _reject_legacy_files(INPUT_DIR, BOTH_DIR, OUTPUT_DIR)

    PROFILE_PATH = _resolve_profile_path(INPUT_DIR)
    PERSON_SHORT = _read_person_short(PROFILE_PATH)

    CONSENT_PATH = INPUT_DIR / personal_filename(PERSON_SHORT, "consent.json")
    CATEGORIES_PATH = BOTH_DIR / "categories.json"
    PERSONAL_CATEGORIES_PATH = BOTH_DIR / personal_filename(PERSON_SHORT, "categories.json")
    CATEGORIZED_TRANSACTIONS_PATH = BOTH_DIR / personal_filename(
        PERSON_SHORT, "categorized_transactions.json"
    )
    RAW_TRANSACTIONS_PATH = OUTPUT_DIR / personal_filename(
        PERSON_SHORT, "downloaded_transactions.json"
    )
    CATEGORY_TOTALS_PATH = OUTPUT_DIR / personal_filename(
        PERSON_SHORT, "category_totals.json"
    )
