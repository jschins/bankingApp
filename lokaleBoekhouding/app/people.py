"""Discover person packs under the boekhouding deploy folder."""
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


def list_people(root: Path | None = None) -> list[PersonPack]:
    """Return person packs that have both ``data/`` and ``secret/``.

    Short name comes from ``secret/profile.json`` (not the folder name).
    Packs without a readable profile or a single ``.pem`` are skipped.
    """
    base = root if root is not None else app_root()
    packs: list[PersonPack] = []
    if not base.is_dir():
        return packs

    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name in _IGNORE_DIRS or child.name.startswith("."):
            continue
        secret = child / "secret"
        data = child / "data"
        # Acceptance rule: both data/ and secret/ must exist.
        if not data.is_dir() or not secret.is_dir():
            continue
        profile = secret / "profile.json"
        if not profile.is_file():
            continue
        try:
            short = _read_person_short(profile)
            private_key = _resolve_private_key(secret)
        except (OSError, ValueError, FileNotFoundError, json.JSONDecodeError):
            continue
        packs.append(
            PersonPack(
                short=short,
                folder=child.resolve(),
                folder_name=child.name,
                data_dir=data.resolve(),
                secret_dir=secret.resolve(),
                profile_path=profile.resolve(),
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
