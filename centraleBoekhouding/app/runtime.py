"""Runtime paths for centraleBoekhouding."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def data_root() -> Path:
    """Directory that contains workspace folders (e.g. ``dkg/``).

    Prefer ``boekhouding/`` next to the exe (frozen) or under the project (dev).
    """
    env = os.environ.get("CENTRALE_DATA_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    project = Path(__file__).resolve().parents[1]
    boekhouding = project / "boekhouding"
    if boekhouding.is_dir():
        return boekhouding
    return project


def project_root() -> Path:
    if is_frozen():
        return data_root()
    return Path(__file__).resolve().parents[1]
