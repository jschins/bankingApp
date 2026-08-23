"""boekhouding-client package."""
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_shared_package() -> None:
    """Prefer monorepo ``shared/`` when the venv install is missing or stale."""
    repo_shared = Path(__file__).resolve().parents[3] / "shared"
    if not repo_shared.is_dir():
        return
    try:
        import shared.passwords  # noqa: F401
    except ModuleNotFoundError:
        sys.path.insert(0, str(repo_shared))


_ensure_shared_package()
