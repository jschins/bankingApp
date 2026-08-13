"""Discover person packs under the active workspace folder (hub)."""
from __future__ import annotations

import json
from pathlib import Path

from app.paths import PersonPack, _read_person_short, _resolve_private_key
from app.runtime import app_root

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


def list_people(root: Path | None = None) -> list[PersonPack]:
    """Person packs with ``data/``. Use ``secret/`` when present (Refresh)."""
    base = root if root is not None else app_root()
    packs: list[PersonPack] = []
    if not base.is_dir():
        return packs

    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name in _IGNORE_DIRS or child.name.startswith("."):
            continue
        data = child / "data"
        if not data.is_dir():
            continue
        secret = child / "secret"
        profile = secret / "profile.json"
        short: str | None = None
        private_key = _MISSING
        profile_path = _MISSING
        secret_dir = secret if secret.is_dir() else (child / "secret")

        if profile.is_file():
            try:
                short = _read_person_short(profile)
                profile_path = profile.resolve()
            except (OSError, ValueError, json.JSONDecodeError):
                short = None
        if short is None:
            short = child.name

        if secret.is_dir():
            try:
                private_key = _resolve_private_key(secret)
            except (OSError, FileNotFoundError, ValueError):
                private_key = _MISSING

        packs.append(
            PersonPack(
                short=short,
                folder=child.resolve(),
                folder_name=child.name,
                data_dir=data.resolve(),
                secret_dir=secret_dir.resolve() if secret.is_dir() else secret_dir,
                profile_path=profile_path,
                private_key_path=private_key,
            )
        )
    packs.sort(key=lambda p: p.short.lower())
    return packs


def get_person(short: str, root: Path | None = None) -> PersonPack:
    needle = short.strip().lower()
    for pack in list_people(root):
        if pack.short.lower() == needle:
            return pack
    raise KeyError(f"Unknown person short name: {short!r}")
