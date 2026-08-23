"""Derive login access mode from user-store fields (person + workspace only)."""
from __future__ import annotations

from typing import Any

ACCESS_PERSONAL = "personal"
ACCESS_LOCAL = "local"
ACCESS_REGIONAL_ADMIN = "regional_admin"


def parse_workspaces(raw: str | None) -> list[str]:
    """Split ``workspace`` field: ``dkg,jl`` → ``['dkg', 'jl']``.

    ``NULL``, missing, and empty string all yield ``[]`` (all hub folders when
    access is regional_admin).
    """
    if raw is None:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def deduce_access(*, person: str, workspaces: list[str]) -> str:
    """Return ``personal``, ``local``, or ``regional_admin``.

    - ``person`` set → personal (one person only)
    - ``person`` empty, multiple workspaces → regional_admin (subset switcher)
    - ``person`` empty, one workspace → local (whole workspace, all persons)
    - ``person`` empty, workspace NULL/empty → regional_admin (all hub folders)
    """
    if str(person or "").strip():
        return ACCESS_PERSONAL
    if len(workspaces) > 1:
        return ACCESS_REGIONAL_ADMIN
    if len(workspaces) == 1:
        return ACCESS_LOCAL
    return ACCESS_REGIONAL_ADMIN


def enrich_user_record(user: dict[str, Any]) -> dict[str, Any]:
    """Add derived ``access`` and parsed ``workspaces`` list to a user dict."""
    person = str(user.get("person") or "").strip()
    raw_ws = user.get("workspace")
    workspaces = parse_workspaces(None if raw_ws is None else str(raw_ws))
    access = deduce_access(person=person, workspaces=workspaces)
    return {
        "username": str(user.get("username") or "").strip(),
        "title": str(user.get("title") or "").strip(),
        "access": access,
        "workspace": "" if raw_ws is None else str(raw_ws).strip(),
        "workspaces": workspaces,
        "person": person,
        "selected_workspace": str(user.get("selected_workspace") or "").strip(),
    }
