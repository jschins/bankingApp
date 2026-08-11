"""Admin app settings — no single-person AppSettings; people are discovered."""
from __future__ import annotations

from app.paths import PersonPack, configure as configure_paths
from app.people import list_people

_people: list[PersonPack] | None = None


def init_app() -> list[PersonPack]:
    global _people
    _people = configure_paths()
    return _people


def get_people() -> list[PersonPack]:
    if _people is None:
        return list_people()
    return list(_people)


def refresh_people() -> list[PersonPack]:
    global _people
    _people = list_people()
    if not _people:
        from app.runtime import app_root

        raise FileNotFoundError(
            f"No person packs found under {app_root()}."
        )
    return list(_people)
