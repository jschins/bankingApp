#!/usr/bin/env python3
"""Inspect raw account objects returned by Enable Banking create_session()."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.single_client import EnableBankingClient, _extract_code, load_profile
from app.paths import configure
from app.runtime import app_root


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(token in lowered for token in ("id", "iban", "identification", "uid", "pan")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _compact_account_view(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": account.get("uid"),
        "iban": account.get("iban"),
        "account_id": account.get("account_id"),
        "all_account_ids": account.get("all_account_ids"),
        "masked_pan": account.get("masked_pan"),
        "maskedPan": account.get("maskedPan"),
        "balances": account.get("balances"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redirect-code",
        required=True,
        help="Full redirect URL or bare authorization code from the bank callback.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=app_root() / "data" / "session_accounts_debug.json",
        help="Output JSON path (default: data/session_accounts_debug.json under app root).",
    )
    args = parser.parse_args()

    configure()
    profile = load_profile()
    client = EnableBankingClient.from_profile(profile)
    session = client.create_session(_extract_code(args.redirect_code))

    accounts = session.get("accounts")
    if not isinstance(accounts, list):
        accounts = []

    payload = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "aspsp": profile.get("aspsp"),
        "country": profile.get("country"),
        "person": profile.get("person"),
        "account_count": len(accounts),
        "compact_accounts": [_compact_account_view(acc) for acc in accounts if isinstance(acc, dict)],
        "raw_accounts_redacted": _redact([acc for acc in accounts if isinstance(acc, dict)]),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output.resolve()} with {len(accounts)} account object(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

