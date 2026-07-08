#!/usr/bin/env python3
"""Merge ``data/categories.csv`` term columns into ``data/categories.json``."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.runtime import project_path

DEFAULT_JSON = project_path("data", "categories.json")
DEFAULT_CSV = project_path("data", "categories.csv")


def rows_to_categories(headers: list[str], rows: list[list[str]]) -> dict[str, list[str]]:
    """Build a category-name → terms map from CSV headers and data rows."""
    categories: dict[str, list[str]] = {name: [] for name in headers}
    for row in rows:
        for index, name in enumerate(headers):
            if index >= len(row):
                continue
            term = row[index].strip()
            if term:
                categories[name].append(term)
    return categories


def convert(json_path: Path, csv_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    abbreviations = data.get("abbreviations")
    if not isinstance(abbreviations, dict):
        abbreviations = {}

    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{csv_path} is empty") from exc
        rows = list(reader)

    headers = [name.strip() for name in headers if name.strip()]
    if not headers:
        raise ValueError(f"{csv_path} has no category columns")

    categories = rows_to_categories(headers, rows)

    output = {
        "abbreviations": abbreviations,
        "categories": categories,
    }
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    term_count = sum(len(terms) for terms in categories.values())
    print(f"Wrote {json_path} ({len(categories)} categories, {term_count} terms, abbreviations kept)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
        help=f"Categories JSON to update (default: {DEFAULT_JSON.relative_to(PROJECT)})",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Source CSV (default: {DEFAULT_CSV.relative_to(PROJECT)})",
    )
    args = parser.parse_args()

    if not args.json.is_file():
        raise SystemExit(f"JSON not found: {args.json}")
    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")

    convert(args.json.resolve(), args.csv.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
