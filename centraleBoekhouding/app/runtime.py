"""Runtime paths for centraleBoekhouding."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def data_root() -> Path:
    """Directory that contains workspace folders (e.g. ``dkg/``)."""
    env = os.environ.get("CENTRALE_DATA_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    if is_frozen():
        return data_root()
    return Path(__file__).resolve().parents[1]
