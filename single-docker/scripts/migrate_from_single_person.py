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


def personal_filename(person: str, stem: str) -> str:
    return f"{person}_{stem}"


def resolve_source_profile(input_dir: Path) -> Path:
    prefixed = sorted(input_dir.glob("*_profile.json"))
    if len(prefixed) == 1:
        return prefixed[0]
    if prefixed:
        raise SystemExit(f"Multiple profiles in {input_dir}; pass --person.")
    raise SystemExit(
        f"No {{person}}_profile.json in {input_dir}. Legacy profile.json is not supported."
    )


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

    copy_file(profile_src, secret / personal_filename(person, "profile.json"))
    for pem in sorted(input_dir.glob("*.pem")):
        copy_file(pem, secret / pem.name)

    prefixed_consent = input_dir / personal_filename(person, "consent.json")
    if prefixed_consent.exists():
        copy_file(prefixed_consent, data / personal_filename(person, "consent.json"))

    copy_file(both_dir / "categories.json", data / "categories.json")
    copy_file(
        both_dir / personal_filename(person, "categories.json"),
        data / personal_filename(person, "categories.json"),
    )
    copy_file(
        both_dir / personal_filename(person, "categorized_transactions.json"),
        data / personal_filename(person, "categorized_transactions.json"),
    )
    copy_file(
        output_dir / personal_filename(person, "downloaded_transactions.json"),
        data / personal_filename(person, "downloaded_transactions.json"),
    )
    copy_file(
        output_dir / personal_filename(person, "category_totals.json"),
        data / personal_filename(person, "category_totals.json"),
    )

    print(f"Done. Add secret/{person}_profile.json and the .pem key beside data/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
