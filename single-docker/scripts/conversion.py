#!/usr/bin/env python3
"""Convert ``data/categories.json`` to a column-oriented ``data/categories.csv``."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.runtime import app_root, project_path

DEFAULT_INPUT = project_path("data", "categories.json")
DEFAULT_OUTPUT = project_path("data", "categories.csv")


def categories_to_rows(categories: dict[str, list[str]]) -> tuple[list[str], list[list[str]]]:
    """Return header names and term rows (one row per term index)."""
    headers = list(categories.keys())
    columns = [list(categories[name]) for name in headers]
    max_rows = max((len(column) for column in columns), default=0)
    rows: list[list[str]] = []
    for index in range(max_rows):
        rows.append([column[index] if index < len(column) else "" for column in columns])
    return headers, rows


def convert(input_path: Path, output_path: Path) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    categories = data.get("categories")
    if not isinstance(categories, dict):
        raise ValueError(f"{input_path} must contain a 'categories' object")

    headers, rows = categories_to_rows(
        {str(name): [str(term) for term in (terms or [])] for name, terms in categories.items()}
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"Wrote {output_path} ({len(headers)} categories, {len(rows)} term rows)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Source JSON (default: {DEFAULT_INPUT.relative_to(PROJECT)})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination CSV (default: {DEFAULT_OUTPUT.relative_to(PROJECT)})",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"Input not found: {args.input}")

    convert(args.input.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
