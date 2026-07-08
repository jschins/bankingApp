#!/usr/bin/env python3
"""Send ad-hoc requests to Enable Banking using project credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.paths import configure


def _json_arg(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc}") from exc


def _read_json_file(path: str) -> Any:
    payload_path = Path(path)
    if not payload_path.is_file():
        raise SystemExit(f"JSON file not found: {payload_path}")
    return json.loads(payload_path.read_text(encoding="utf-8"))


def _parse_query(items: list[str]) -> dict[str, str]:
    query: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --query value {item!r}; expected key=value")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Invalid --query value {item!r}; empty key")
        query[key] = value
    return query


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "short",
        help="Person short name (e.g. bog). Defaults from consent/profile when omitted.",
    )
    parser.add_argument("method", help="HTTP method, e.g. GET, POST")
    parser.add_argument("path", help="API path, e.g. /sessions/{session_id}")
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Query parameter (repeatable), e.g. --query date_from=2026-06-01",
    )
    parser.add_argument(
        "--json",
        type=_json_arg,
        default=None,
        help='Inline JSON body, e.g. --json \'{"code":"..."}\'',
    )
    parser.add_argument(
        "--json-file",
        default=None,
        help="Path to JSON body file (alternative to --json)",
    )
    args = parser.parse_args()

    if args.json is not None and args.json_file is not None:
        raise SystemExit("Use either --json or --json-file, not both.")

    configure(args.short)

    # Import after configure() so app.paths constants are resolved for this person.
    from app.core.single_client import EnableBankingClient, load_profile

    profile = load_profile()
    client = EnableBankingClient.from_profile(profile)

    kwargs: dict[str, Any] = {}
    query = _parse_query(args.query)
    if query:
        kwargs["params"] = query
    if args.json is not None:
        kwargs["json"] = args.json
    elif args.json_file is not None:
        kwargs["json"] = _read_json_file(args.json_file)

    result = client._request(args.method.upper(), args.path, **kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

