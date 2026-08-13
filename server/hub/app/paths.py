"""Per-person path binding for categorize / single_client globals."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.runtime import app_root

DATA_DIR: Path = Path("data")
PERSON_SHORT: str = ""
PROFILE_PATH: Path = Path("profile.json")
PRIVATE_KEY_PATH: Path = Path("key.pem")
CONSENT_PATH: Path = Path("secret") / "consent.json"
CATEGORIES_PATH: Path = Path("categories.json")
PERSONAL_CATEGORIES_PATH: Path = DATA_DIR / "personal_categories.json"
CATEGORIZED_TRANSACTIONS_PATH: Path = DATA_DIR / "categorized_transactions.json"
RAW_TRANSACTIONS_PATH: Path = DATA_DIR / "downloaded_transactions.json"
CATEGORY_TOTALS_PATH: Path = DATA_DIR / "category_totals.json"


@dataclass(frozen=True)
class PersonPack:
    short: str
    folder: Path
    folder_name: str
    data_dir: Path
    secret_dir: Path
    profile_path: Path
    private_key_path: Path

    @property
    def consent_path(self) -> Path:
        return self.secret_dir / "consent.json"

    @property
    def personal_categories_path(self) -> Path:
        return self.data_dir / "personal_categories.json"

    @property
    def categorized_path(self) -> Path:
        return self.data_dir / "categorized_transactions.json"

    @property
    def totals_path(self) -> Path:
        return self.data_dir / "category_totals.json"


def shared_categories_path(root: Path | None = None) -> Path:
    """``categories.json`` beside ``boekhouding.exe`` (admin deploy root)."""
    base = root if root is not None else app_root()
    return (base / "categories.json").resolve()


def _read_person_short(profile_path: Path) -> str:
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    person = str(data.get("person") or "").strip()
    if not person:
        raise ValueError(f"profile missing 'person': {profile_path}")
    return person


def _resolve_private_key(secret_dir: Path) -> Path:
    pem_files = sorted(secret_dir.glob("*.pem"))
    if len(pem_files) == 1:
        return pem_files[0].resolve()
    if not pem_files:
        raise FileNotFoundError(f"No .pem private key file found in {secret_dir}.")
    names = ", ".join(path.name for path in pem_files)
    raise FileNotFoundError(
        f"Expected exactly one .pem file in {secret_dir}, found: {names}."
    )


def apply_person(pack: PersonPack) -> None:
    """Point module-level paths at one person pack (used by categorize/single_client)."""
    global DATA_DIR, PERSON_SHORT, PROFILE_PATH, PRIVATE_KEY_PATH, CONSENT_PATH
    global CATEGORIES_PATH, PERSONAL_CATEGORIES_PATH, CATEGORIZED_TRANSACTIONS_PATH
    global RAW_TRANSACTIONS_PATH, CATEGORY_TOTALS_PATH

    DATA_DIR = pack.data_dir
    PERSON_SHORT = pack.short
    PROFILE_PATH = pack.profile_path
    PRIVATE_KEY_PATH = pack.private_key_path
    CONSENT_PATH = pack.consent_path
    CATEGORIES_PATH = shared_categories_path()
    PERSONAL_CATEGORIES_PATH = pack.personal_categories_path
    CATEGORIZED_TRANSACTIONS_PATH = pack.categorized_path
    RAW_TRANSACTIONS_PATH = pack.data_dir / "downloaded_transactions.json"
    CATEGORY_TOTALS_PATH = pack.totals_path


@contextmanager
def bind_person(pack: PersonPack) -> Iterator[PersonPack]:
    """Temporarily bind path globals to ``pack``, then restore previous values."""
    global DATA_DIR, PERSON_SHORT, PROFILE_PATH, PRIVATE_KEY_PATH, CONSENT_PATH
    global CATEGORIES_PATH, PERSONAL_CATEGORIES_PATH, CATEGORIZED_TRANSACTIONS_PATH
    global RAW_TRANSACTIONS_PATH, CATEGORY_TOTALS_PATH

    snapshot = {
        "DATA_DIR": DATA_DIR,
        "PERSON_SHORT": PERSON_SHORT,
        "PROFILE_PATH": PROFILE_PATH,
        "PRIVATE_KEY_PATH": PRIVATE_KEY_PATH,
        "CONSENT_PATH": CONSENT_PATH,
        "CATEGORIES_PATH": CATEGORIES_PATH,
        "PERSONAL_CATEGORIES_PATH": PERSONAL_CATEGORIES_PATH,
        "CATEGORIZED_TRANSACTIONS_PATH": CATEGORIZED_TRANSACTIONS_PATH,
        "RAW_TRANSACTIONS_PATH": RAW_TRANSACTIONS_PATH,
        "CATEGORY_TOTALS_PATH": CATEGORY_TOTALS_PATH,
    }
    apply_person(pack)
    try:
        yield pack
    finally:
        DATA_DIR = snapshot["DATA_DIR"]
        PERSON_SHORT = snapshot["PERSON_SHORT"]
        PROFILE_PATH = snapshot["PROFILE_PATH"]
        PRIVATE_KEY_PATH = snapshot["PRIVATE_KEY_PATH"]
        CONSENT_PATH = snapshot["CONSENT_PATH"]
        CATEGORIES_PATH = snapshot["CATEGORIES_PATH"]
        PERSONAL_CATEGORIES_PATH = snapshot["PERSONAL_CATEGORIES_PATH"]
        CATEGORIZED_TRANSACTIONS_PATH = snapshot["CATEGORIZED_TRANSACTIONS_PATH"]
        RAW_TRANSACTIONS_PATH = snapshot["RAW_TRANSACTIONS_PATH"]
        CATEGORY_TOTALS_PATH = snapshot["CATEGORY_TOTALS_PATH"]


def configure() -> list[PersonPack]:
    """Discover person packs under app_root."""
    from app.people import list_people
    from app.runtime import app_root

    people = list_people()
    if not people:
        raise FileNotFoundError(
            "No person packs found under "
            f"{app_root()}. Each pack needs a data/ folder."
        )
    apply_person(people[0])
    return people
