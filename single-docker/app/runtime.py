"""Runtime layout: dev tree, Docker, or PyInstaller executable."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _has_data_and_secret(root: Path) -> bool:
    return (root / "data").is_dir() and (root / "secret").is_dir()


def app_root() -> Path:
    """Directory that contains ``data/`` and ``secret/``.

    When frozen, that is always the folder that contains the executable. The
    name of that folder does not matter — only that ``data/`` and ``secret/``
    sit beside the ``.exe``.
    """
    if is_frozen():
        exe = Path(sys.executable).resolve()
        candidates: list[Path] = []
        if "Contents" in exe.parts and "MacOS" in exe.parts:
            bundle_parent = Path(*exe.parts[: exe.parts.index("Contents")]).parent
            candidates.extend([bundle_parent, exe.parent])
        else:
            candidates.append(exe.parent)
        for root in candidates:
            if _has_data_and_secret(root):
                return root
        tried = ", ".join(str(path) for path in candidates)
        raise FileNotFoundError(
            "Frozen app requires folders 'data' and 'secret' next to the executable "
            f"(looked in: {tried})."
        )
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """``<app_root>/data``."""
    return app_root() / "data"


def secret_dir() -> Path:
    """``<app_root>/secret``."""
    return app_root() / "secret"


def project_path(*parts: str) -> Path:
    """Resolve a path under :func:`app_root`."""
    return app_root().joinpath(*parts)


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _has_ui(dist: Path) -> bool:
    return (dist / "index.html").is_file()


def frontend_dist_dir() -> Path:
    """Locate the built React UI (bundled in the exe or beside it)."""
    candidates: list[Path] = []
    bundle = bundle_dir()
    if bundle is not None:
        candidates.extend(
            [
                bundle / "frontend" / "dist",
                bundle / "dist",
            ]
        )
    # Dev / optional loose UI next to data+secret; frozen UI normally lives in _MEIPASS.
    root = app_root()
    candidates.extend(
        [
            root / "frontend" / "dist",
            root / "dist",
        ]
    )
    for path in candidates:
        if _has_ui(path):
            return path
    return candidates[0] if candidates else root / "frontend" / "dist"


def frontend_dist_ok() -> bool:
    return _has_ui(frontend_dist_dir())
