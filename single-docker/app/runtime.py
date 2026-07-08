"""Runtime layout: dev tree, Docker, or PyInstaller executable."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    """Directory beside which ``data/`` and ``secret/`` are expected when frozen."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        candidates: list[Path] = []
        if "Contents" in exe.parts and "MacOS" in exe.parts:
            bundle_parent = Path(*exe.parts[: exe.parts.index("Contents")]).parent
            candidates.extend([bundle_parent, exe.parent])
        else:
            candidates.append(exe.parent)
        for root in candidates:
            if (root / "data").is_dir():
                return root
        return candidates[0]
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
