"""Runtime layout: boekh-multiperson source vs boekhouding deploy folder."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    """Source tree (``boekh-multiperson/``): app/, frontend/, scripts/, …"""
    if is_frozen():
        # Bundled UI lives under _MEIPASS; project_root is unused for people data.
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def app_root() -> Path:
    """Folder that contains the admin exe and the person packs.

    Deploy layout::

        boekh-multiperson/
          app/ frontend/ …          # source (dev)
          boekhouding/              # deploy / runtime root
            boekhouding.exe
            anton_schins/data+secret
            juleon_schins/…

    Frozen: directory of the executable.
    Dev: ``<project>/boekhouding/`` (person data), not the source root.
    """
    if is_frozen():
        exe = Path(sys.executable).resolve()
        if "Contents" in exe.parts and "MacOS" in exe.parts:
            return Path(*exe.parts[: exe.parts.index("Contents")]).parent
        return exe.parent
    return project_root() / "boekhouding"


def project_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _has_ui(dist: Path) -> bool:
    return (dist / "index.html").is_file()


def frontend_dist_dir() -> Path:
    candidates: list[Path] = []
    bundle = bundle_dir()
    if bundle is not None:
        candidates.extend(
            [
                bundle / "frontend" / "dist",
                bundle / "dist",
            ]
        )
    root = project_root()
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
