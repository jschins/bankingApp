"""Build bankingApp-export.zip for moving the project to another laptop.

Creates a clean archive of the whole bankingApp project next to this script, omitting
folders that are rebuilt on the target machine (virtual envs, node_modules,
build artefacts) and the legacy bankingApp-editor, while keeping the git-ignored
files that the new machine actually needs (.env files, packaging/server.json,
packaging/profiles/* with their .pem keys, and the storage/ trees).

Run:  python "export zip.py"
"""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "bankingApp-export.zip"

# Directory names skipped anywhere in the tree.
SKIP_DIRS = {
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "bankingApp-editor",  # legacy PDF-conversion app, not part of the current stack
}

# File suffixes skipped anywhere in the tree.
SKIP_SUFFIXES = {".zip", ".pyc"}


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def main() -> None:
    if OUTPUT.exists():
        OUTPUT.unlink()

    count = 0
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            rel_parts = path.relative_to(ROOT).parts

            # Skip anything living under an excluded directory.
            if any(_skip_dir(part) for part in rel_parts[:-1]):
                continue
            if path.is_dir():
                if _skip_dir(path.name):
                    continue
                continue  # directories are created implicitly from file entries

            # if path == OUTPUT or path == Path(__file__).resolve():
            #     continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue

            zf.write(path, path.relative_to(ROOT).as_posix())
            count += 1

    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"ZIP READY: {OUTPUT}")
    print(f"{count} files, {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
