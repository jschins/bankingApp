"""Per-person path binding for categorize / single_client globals."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.runtime import app_root
from app.yearpath import current_year

# Serialize all person-path binds / recalculate (uvicorn runs sync routes in a threadpool).
CALC_LOCK = threading.RLock()

DATA_DIR: Path = Path(current_year())
PERSON_SHORT: str = ""
PROFILE_PATH: Path = Path("profile.json")
PRIVATE_KEY_PATH: Path = Path("key.pem")
CONSENT_PATH: Path = Path("secret") / "consent.json"
CATEGORIES_PATH: Path = Path("categories.json")
PERSONAL_CATEGORIES_PATH: Path = Path("secret") / "personal_categories.json"
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
    year: str

    @property
    def consent_path(self) -> Path:
        return self.secret_dir / "consent.json"

    @property
    def personal_categories_path(self) -> Path:
        return self.secret_dir / "personal_categories.json"

    @property
    def categorized_path(self) -> Path:
        return self.data_dir / "categorized_transactions.json"

    @property
    def totals_path(self) -> Path:
        return self.data_dir / "category_totals.json"

    @property
    def has_secret_folder(self) -> bool:
        """True when Enable Banking credentials are present (a .pem in secret/)."""
        return self.secret_dir.is_dir() and any(self.secret_dir.glob("*.pem"))


def shared_categories_path(root: Path | None = None) -> Path:
    """Shared ``categories.json`` at the hub data root (all workspaces)."""
    if root is not None:
        return (root / "categories.json").resolve()
    from app.runtime import data_root

    return (data_root() / "categories.json").resolve()


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

    with CALC_LOCK:
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
    """Discover person packs under app_root (empty workspace is allowed)."""
    from app.people import list_people

    people = list_people()
    if people:
        apply_person(people[0])
    return people
