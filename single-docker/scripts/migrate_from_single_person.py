#!/usr/bin/env python3
"""Copy single-person input/both/output into single-docker data layout."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def copy_file(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"  {src} -> {dest}")


def read_person(profile_path: Path) -> str:
    person = str(json.loads(profile_path.read_text(encoding="utf-8")).get("person") or "").strip()
    if not person:
        raise ValueError(f"profile missing 'person': {profile_path}")
    return person


def resolve_source_profile(input_dir: Path) -> Path:
    plain = input_dir / "profile.json"
    if plain.is_file():
        return plain
    prefixed = sorted(input_dir.glob("*_profile.json"))
    if len(prefixed) == 1:
        return prefixed[0]
    if prefixed:
        raise SystemExit(f"Multiple profiles in {input_dir}; pass --person.")
    raise SystemExit(f"No profile.json (or {{person}}_profile.json) in {input_dir}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "single-person",
        help="Path to the single-person project (default: ../single-person)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Path to single-docker (default: project root)",
    )
    parser.add_argument(
        "--person",
        help="Person short id (default: read from the source profile)",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    data = target / "data"
    secret = target / "secret"

    input_dir = source / "input"
    both_dir = source / "both"
    output_dir = source / "output"

    profile_src = resolve_source_profile(input_dir)
    person = args.person or read_person(profile_src)

    print(f"Migrating {person!r} from {source}")
    print(f"  secret -> {secret}")
    print(f"  data   -> {data}")

    copy_file(profile_src, secret / "profile.json")
    for pem in sorted(input_dir.glob("*.pem")):
        copy_file(pem, secret / pem.name)

    for consent_src in (
        input_dir / "consent.json",
        input_dir / f"{person}_consent.json",
    ):
        if consent_src.exists():
            copy_file(consent_src, data / "consent.json")
            break

    copy_file(both_dir / "categories.json", data / "categories.json")
    for personal_src in (
        both_dir / "personal_categories.json",
        both_dir / f"{person}_categories.json",
        both_dir / "js_categories.json",
    ):
        if personal_src.exists():
            copy_file(personal_src, data / "personal_categories.json")
            break

    for stem, dest_name in (
        ("categorized_transactions.json", "categorized_transactions.json"),
        ("downloaded_transactions.json", "downloaded_transactions.json"),
        ("category_totals.json", "category_totals.json"),
    ):
        for src in (
            both_dir / stem,
            both_dir / f"{person}_{stem}",
            output_dir / stem,
            output_dir / f"{person}_{stem}",
        ):
            if src.exists():
                copy_file(src, data / dest_name)
                break

    print("Done. Place secret/profile.json and the .pem key beside data/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
