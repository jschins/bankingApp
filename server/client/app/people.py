"""Discover person packs under the active workspace (client BFF)."""
from __future__ import annotations

from pathlib import Path

from app.paths import PersonPack, _resolve_private_key
from app.runtime import app_root
from app.yearpath import has_person_layout, parse_year, year_dir

_IGNORE_DIRS = frozenset(
    {
        "app",
        "frontend",
        "dist",
        "build",
        ".venv",
        "venv",
        "scripts",
        "node_modules",
        "__pycache__",
        ".git",
    }
)

_MISSING = Path(".")


def list_people(root: Path | None = None, *, year: str | None = None) -> list[PersonPack]:
    """Person packs with ``secret/`` and/or a ``YYYY/`` year folder. Identity is the folder name."""
    base = root if root is not None else app_root()
    y = parse_year(year)
    packs: list[PersonPack] = []
    if not base.is_dir():
        return packs

    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name in _IGNORE_DIRS or child.name.startswith("."):
            continue
        if not has_person_layout(child):
            continue
        data = year_dir(child, y)
        secret = child / "secret"
        profile = secret / "profile.json"
        private_key = _MISSING
        profile_path = profile.resolve() if profile.is_file() else _MISSING
        secret_dir = secret if secret.is_dir() else (child / "secret")

        if secret.is_dir():
            try:
                private_key = _resolve_private_key(secret)
            except (OSError, FileNotFoundError, ValueError):
                private_key = _MISSING

        packs.append(
            PersonPack(
                short=child.name,
                folder=child.resolve(),
                folder_name=child.name,
                data_dir=data.resolve(),
                secret_dir=secret_dir.resolve() if secret.is_dir() else secret_dir,
                profile_path=profile_path,
                private_key_path=private_key,
                year=y,
            )
        )
    packs.sort(key=lambda p: p.folder_name.lower())
    return packs


def get_person(short: str, root: Path | None = None, *, year: str | None = None) -> PersonPack:
    needle = short.strip().lower()
    for pack in list_people(root, year=year):
        if pack.folder_name.lower() == needle:
            return pack
    raise KeyError(f"Unknown person: {short!r}")
