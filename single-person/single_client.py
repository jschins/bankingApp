"""Enable Banking client for the single-person workflow.

Reads credentials from input/{person}_profile.json and input/*.pem.
Returns raw bank JSON to the caller.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import requests

from paths import CONSENT_PATH, INPUT_DIR, PROFILE_PATH

BASE_URL = "https://api.enablebanking.com"


class EnableBankingError(RuntimeError):
    """Raised when the Enable Banking API returns an error response."""


class EnableBankingClient:
    def __init__(self, application_id: str, private_key: bytes, timeout: float = 30) -> None:
        if not application_id or not private_key:
            raise EnableBankingError("Missing Enable Banking application id or private key.")
        self._app_id = application_id
        self._private_key = private_key
        self._timeout = timeout

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> EnableBankingClient:
        app_id = str(profile.get("app_id") or "")
        key_file = str(profile.get("key_file") or "")
        key_path = INPUT_DIR / key_file
        if not key_path.exists():
            raise EnableBankingError(f"Private key file not found: {key_path}")
        return cls(app_id, key_path.read_bytes())

    def _jwt(self) -> str:
        now = int(time.time())
        headers = {"typ": "JWT", "alg": "RS256", "kid": self._app_id}
        payload = {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + 3600,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256", headers=headers)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = requests.request(
            method,
            f"{BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {self._jwt()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
            **kwargs,
        )
        if not response.ok:
            raise EnableBankingError(f"{method} {path} failed: {response.status_code} {response.text}")
        return response.json() if response.content else None

    def start_authorization(self, profile: dict[str, Any], valid_until: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/auth",
            json={
                "access": {"valid_until": valid_until},
                "aspsp": {"name": profile["aspsp"], "country": profile["country"]},
                "redirect_url": profile["redirect_url"],
                "psu_type": "personal",
                "state": uuid.uuid4().hex,
            },
        )

    def create_session(self, code: str) -> dict[str, Any]:
        return self._request("POST", "/sessions", json={"code": code})

    def get_transactions(
        self,
        account_uid: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        all_transactions: list[dict[str, Any]] = []
        continuation_key: str | None = None
        while True:
            params: dict[str, str] = {}
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            if continuation_key:
                params["continuation_key"] = continuation_key
            resp = self._request("GET", f"/accounts/{account_uid}/transactions", params=params)
            all_transactions.extend(resp.get("transactions", []))
            continuation_key = resp.get("continuation_key")
            if not continuation_key:
                break
        return all_transactions


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        raise EnableBankingError(f"Profile not found: {PROFILE_PATH}")
    profile = _read_json(PROFILE_PATH)
    country = str(profile.get("country") or "")
    if country and len(country) != 2:
        raise EnableBankingError(
            f"profile.json country must be ISO 3166-1 alpha-2 (e.g. IE, NL), not {country!r}"
        )
    return profile


def _extract_code(code_or_url: str) -> str:
    if code_or_url.startswith("http"):
        codes = parse_qs(urlparse(code_or_url).query).get("code")
        if not codes:
            raise EnableBankingError(f"No 'code' parameter found in URL: {code_or_url}")
        return codes[0]
    return code_or_url


def _build_consent_record(profile: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    valid_until = (session.get("access") or {}).get("valid_until")
    if not valid_until:
        valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    return {
        "person": profile.get("person", "unknown"),
        "aspsp": profile["aspsp"],
        "country": profile["country"],
        "session_id": session.get("session_id"),
        "valid_until": valid_until,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "accounts": [
            {
                "uid": acc.get("uid"),
                "iban": (acc.get("account_id") or {}).get("iban"),
                "identification_hash": acc.get("identification_hash"),
                "name": acc.get("name"),
                "currency": acc.get("currency"),
            }
            for acc in session.get("accounts", [])
        ],
    }


def _consent_matches_profile(record: dict[str, Any], profile: dict[str, Any]) -> bool:
    return (
        record.get("person") == profile.get("person")
        and record.get("aspsp") == profile.get("aspsp")
        and record.get("country") == profile.get("country")
    )


def needs_consent_renewal() -> bool:
    """True when consent is missing, expired, or for a different profile."""
    if not CONSENT_PATH.exists():
        return True
    record = _read_json(CONSENT_PATH)
    try:
        profile = load_profile()
    except EnableBankingError:
        return True
    if not _consent_matches_profile(record, profile):
        return True
    valid_until = record.get("valid_until")
    if not valid_until:
        return False
    try:
        expires = datetime.fromisoformat(valid_until)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < datetime.now(timezone.utc)


def _load_stored_accounts() -> list[dict[str, Any]]:
    if not CONSENT_PATH.exists() or needs_consent_renewal():
        return []
    accounts = _read_json(CONSENT_PATH).get("accounts", [])
    return accounts if isinstance(accounts, list) else []


def _is_already_authorized_error(exc: EnableBankingError) -> bool:
    return "ALREADY_AUTHORIZED" in str(exc)


def get_authorization_url() -> str:
    profile = load_profile()
    client = EnableBankingClient.from_profile(profile)
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    auth = client.start_authorization(profile, valid_until)
    url = auth.get("url", "")
    if not url:
        raise EnableBankingError("Enable Banking did not return an authorization URL.")
    return url


def _linked_accounts(profile: dict[str, Any], client: EnableBankingClient, redirect_code: str | None) -> list[dict[str, Any]]:
    stored_accounts = _load_stored_accounts()
    if stored_accounts:
        return stored_accounts

    if redirect_code:
        try:
            session = client.create_session(_extract_code(redirect_code))
        except EnableBankingError as exc:
            if _is_already_authorized_error(exc):
                stored_accounts = _load_stored_accounts()
                if stored_accounts:
                    return stored_accounts
                raise EnableBankingError(
                    "This redirect URL was already used. Restart the app to get a new "
                    "authorization URL, complete bank login again, and paste the new "
                    "redirect URL once."
                ) from exc
            raise
        accounts = session.get("accounts", [])
        if not accounts:
            raise EnableBankingError("No accounts were linked during authorization.")
        _write_json(CONSENT_PATH, _build_consent_record(profile, session))
        return accounts

    if needs_consent_renewal():
        raise EnableBankingError("Redirect code is required to renew bank consent.")

    raise EnableBankingError("No linked accounts available.")


def fetch_transactions(
    date_from: str | None = None,
    date_to: str | None = None,
    redirect_code: str | None = None,
) -> list[dict[str, Any]]:
    """Download raw transactions from the bank and return them."""
    profile = load_profile()
    client = EnableBankingClient.from_profile(profile)
    accounts = _linked_accounts(profile, client, redirect_code)

    raw_transactions: list[dict[str, Any]] = []
    for account in accounts:
        account_uid = account.get("uid")
        if not account_uid:
            continue
        raw_transactions.extend(client.get_transactions(account_uid, date_from=date_from, date_to=date_to))

    return raw_transactions


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download bank transactions via Enable Banking")
    parser.add_argument("--redirect-code", default=None, help="Redirect URL or code after bank approval")
    parser.add_argument("--date-from", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--date-to", default=None, help="End date YYYY-MM-DD")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        transactions = fetch_transactions(
            date_from=args.date_from,
            date_to=args.date_to,
            redirect_code=args.redirect_code,
        )
    except EnableBankingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Downloaded {len(transactions)} transactions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
