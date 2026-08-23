"""Enable Banking client for the single-person workflow.

Reads credentials from the configured profile path and private key file.
Returns raw bank JSON to the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse

from shared.enable_banking import EnableBankingClient, EnableBankingError
from shared.enable_banking.transactions import (
    parse_iso_date,
    date_period_chunks,
    dedupe_transactions,
    fetch_transactions_pages,
    fetch_transactions_period,
)

import app.paths as paths

DEFAULT_REDIRECT = "https://deoudegracht.nl/banking-callback.html"

AIB_HISTORICAL_YEARS = 2
AIB_ROLLING_DAYS = 90
AIB_TZ = ZoneInfo("Europe/Dublin")


def _aib_today() -> date:
    """Calendar today in Ireland (AIB consent renewal day)."""
    return datetime.now(AIB_TZ).date()


def _historical_start(today: date | None = None) -> date:
    """Earliest allowed history date: calendar date two years before today."""
    ref = today or _aib_today()
    try:
        return ref.replace(year=ref.year - AIB_HISTORICAL_YEARS)
    except ValueError:
        # 29 Feb → 28 Feb in a non-leap year
        return ref.replace(year=ref.year - AIB_HISTORICAL_YEARS, day=28)


def _is_aspsp_error(exc: EnableBankingError) -> bool:
    return "ASPSP_ERROR" in str(exc)


def _connection_created_today(profile: dict[str, Any]) -> bool:
    record = _load_consent()
    connection = _profile_connection(record, profile)
    if connection is None:
        return False
    created_at = connection.get("created_at")
    if not created_at:
        return False
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(AIB_TZ).date() == _aib_today()


def _resolve_fetch_dates(
    date_from: str | None,
    date_to: str | None,
    *,
    renewal_day: bool,
) -> tuple[str, str, list[str]]:
    """Clamp AIB fetch window: full history only on consent renewal day."""
    warnings: list[str] = []
    today = _aib_today()
    end = parse_iso_date(date_to) if date_to else today
    if end > today:
        end = today
        warnings.append(f"date_to clamped to today ({today.isoformat()}).")

    rolling_start = today - timedelta(days=AIB_ROLLING_DAYS - 1)
    historical_start = _historical_start(today)
    historical_start_s = historical_start.isoformat()

    if date_from:
        start = parse_iso_date(date_from)
    elif renewal_day:
        start = historical_start
        warnings.append(
            f"No date_from provided; using {historical_start_s} on consent renewal day."
        )
    else:
        start = rolling_start
        warnings.append(
            f"No date_from provided; using last {AIB_ROLLING_DAYS} days "
            f"({rolling_start.isoformat()})."
        )

    if renewal_day:
        if start < historical_start:
            warnings.append(
                f"date_from {start.isoformat()} raised to {historical_start_s} "
                "(earliest allowed on renewal day)."
            )
            start = historical_start
    else:
        if start < rolling_start:
            warnings.append(
                f"AIB only allows history before {rolling_start.isoformat()} on the day "
                f"you renew consent (with redirect code). date_from raised to "
                f"{rolling_start.isoformat()}."
            )
            start = rolling_start

    if start > end:
        raise EnableBankingError(
            "No transactions can be fetched for the requested period. "
            f"AIB allows history back to {historical_start_s} only on the day you "
            "renew consent: complete bank login, paste the redirect code, and fetch "
            f"on that same day. Outside renewal day only the last {AIB_ROLLING_DAYS} "
            f"days are available (from {rolling_start.isoformat()})."
        )

    return start.isoformat(), end.isoformat(), warnings


@dataclass
class FetchResult:
    transactions: list[dict[str, Any]]
    date_from: str
    date_to: str
    renewal_day: bool
    warnings: list[str] = field(default_factory=list)
    account_errors: list[str] = field(default_factory=list)


def _profile_bank_pair(profile: dict[str, Any]) -> tuple[str, str]:
    """Bank ASPSP + country from ``connections[]`` (preferred) or legacy top-level keys."""
    connections = profile.get("connections")
    if isinstance(connections, list):
        for conn in connections:
            if not isinstance(conn, dict):
                continue
            aspsp = str(conn.get("aspsp") or "").strip()
            country = str(conn.get("country") or "").strip()
            if aspsp and country:
                return aspsp, country
    return str(profile.get("aspsp") or "").strip(), str(profile.get("country") or "").strip()


class SingleDockerClient(EnableBankingClient):
    """Enable Banking client extended with AIB-aware transaction fetching."""

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> SingleDockerClient:
        app_id = profile_app_id(profile)
        if not app_id:
            raise EnableBankingError("profile.json connections[].app_id is required")
        key_path = profile_pem_path(profile)
        if not key_path.is_file():
            raise EnableBankingError(f"Private key file not found: {key_path}")
        return cls(app_id, key_path.read_bytes())

    def start_authorization(
        self, profile: dict[str, Any], valid_until: str, state: str | None = None
    ) -> dict[str, Any]:
        aspsp, country = _profile_bank_pair(profile)
        if not aspsp or not country:
            raise EnableBankingError(
                "profile.json connections[].aspsp and country are required"
            )
        return super().start_authorization(
            aspsp_name=aspsp,
            country=country,
            redirect_url=DEFAULT_REDIRECT,
            valid_until=valid_until,
            state=state,
        )

    def _fetch_transactions_pages(
        self,
        account_uid: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        return fetch_transactions_pages(self, account_uid, date_from, date_to)

    def _fetch_transactions_period(
        self,
        account_uid: str,
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        return fetch_transactions_period(self, account_uid, date_from, date_to)

    def get_transactions(
        self,
        account_uid: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        if date_from and date_to:
            merged: list[dict[str, Any]] = []
            for chunk_from, chunk_to in date_period_chunks(date_from, date_to):
                merged.extend(self._fetch_transactions_period(account_uid, chunk_from, chunk_to))
            return dedupe_transactions(merged)

        transactions, _truncated = self._fetch_transactions_pages(account_uid, date_from, date_to)
        return transactions


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def profile_app_id(profile: dict[str, Any]) -> str:
    return paths.app_id_from_profile_data(profile)


def profile_pem_path(profile: dict[str, Any]) -> Path:
    app_id = profile_app_id(profile)
    if app_id:
        return paths.PROFILE_PATH.parent / f"{app_id}.pem"
    return paths.PRIVATE_KEY_PATH


def _find_connection_dict(
    connections: list[dict[str, Any]], aspsp: str, country: str
) -> dict[str, Any] | None:
    key = _connection_key(aspsp, country)
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        if _connection_key(str(conn.get("aspsp") or ""), str(conn.get("country") or "")) == key:
            return conn
    return None


def _migrate_profile(raw: dict[str, Any], profile_path: Path) -> tuple[dict[str, Any], bool]:
    profile = dict(raw)
    changed = False
    consent_path = profile_path.parent / "consent.json"
    if consent_path.is_file():
        try:
            consent = _read_json(consent_path)
        except (OSError, json.JSONDecodeError):
            consent = {}
        if isinstance(consent, dict):
            if not profile.get("connections") and isinstance(consent.get("connections"), list):
                profile["connections"] = consent["connections"]
                changed = True
            for key in ("last_redirect_input", "last_redirect_code", "last_redirect_code_at"):
                if key in consent and key not in profile:
                    profile[key] = consent[key]
                    changed = True

    legacy_app_id = str(profile.pop("app_id", "") or "").strip()
    if profile.pop("key_file", None) is not None:
        changed = True
    if profile.pop("redirect_url", None) is not None:
        changed = True

    connections_raw = profile.get("connections")
    connections: list[dict[str, Any]] = [
        dict(item) for item in connections_raw if isinstance(item, dict)
    ] if isinstance(connections_raw, list) else []

    if legacy_app_id:
        aspsp_hint, country_hint = _profile_bank_pair(profile)
        conn = _find_connection_dict(connections, aspsp_hint, country_hint)
        if conn is None:
            connections.append(
                {
                    "app_id": legacy_app_id,
                    "aspsp": aspsp_hint or None,
                    "country": country_hint or None,
                    "accounts": [],
                }
            )
            changed = True
        elif not str(conn.get("app_id") or "").strip():
            conn["app_id"] = legacy_app_id
            changed = True

    top_aspsp = profile.pop("aspsp", None)
    top_country = profile.pop("country", None)
    if profile.pop("account_name", None) is not None:
        changed = True
    if top_aspsp or top_country:
        changed = True
        target = None
        if connections and isinstance(connections[0], dict):
            target = connections[0]
        elif top_aspsp and top_country:
            target = {"aspsp": top_aspsp, "country": top_country, "accounts": []}
            connections.append(target)
        if target is not None:
            if top_aspsp and not target.get("aspsp"):
                target["aspsp"] = top_aspsp
            if top_country and not target.get("country"):
                target["country"] = top_country

    if connections != connections_raw:
        profile["connections"] = connections
        changed = True
    elif "connections" not in profile:
        profile["connections"] = []
        changed = True

    return profile, changed


def load_profile() -> dict[str, Any]:
    if not paths.PROFILE_PATH.exists():
        raise EnableBankingError(f"Profile not found: {paths.PROFILE_PATH}")
    raw = _read_json(paths.PROFILE_PATH)
    if not isinstance(raw, dict):
        raise EnableBankingError(f"Profile is not a JSON object: {paths.PROFILE_PATH}")
    profile, changed = _migrate_profile(raw, paths.PROFILE_PATH)
    if changed:
        _write_json(paths.PROFILE_PATH, profile)
    _, country = _profile_bank_pair(profile)
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


def _connection_expired(connection: dict[str, Any]) -> bool:
    valid_until = connection.get("valid_until")
    if not valid_until:
        return False
    try:
        expires = datetime.fromisoformat(str(valid_until))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < datetime.now(timezone.utc)


def _connection_key(aspsp: str, country: str) -> tuple[str, str]:
    return str(aspsp), str(country)


def _account_id_for_consent(acc: dict[str, Any]) -> str | None:
    """IBAN or masked CPAN for the consent ``iban`` field."""
    stored = acc.get("iban")
    if stored:
        return str(stored)

    for key in ("masked_pan", "maskedPan", "masked_cpan", "maskedCpan"):
        value = acc.get(key)
        if value:
            return str(value)

    account_id = acc.get("account_id")
    if isinstance(account_id, dict):
        iban = account_id.get("iban")
        if iban:
            return str(iban)
        other = account_id.get("other")
        if isinstance(other, dict):
            scheme = str(other.get("scheme_name") or "").upper()
            identification = other.get("identification")
            if scheme == "CPAN" and identification:
                return str(identification)
            masked = (
                other.get("masked_pan")
                or other.get("maskedPan")
                or other.get("masked_cpan")
                or other.get("maskedCpan")
            )
            if masked:
                return str(masked)

    for item in acc.get("all_account_ids") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("scheme_name") or "").upper() == "CPAN" and item.get("identification"):
            return str(item["identification"])

    return None


def _extract_account_balance(acc: dict[str, Any]) -> tuple[str, str]:
    """Best-effort current balance and currency from account payload."""
    balances = acc.get("balances")
    if balances is None:
        balances = acc.get("Balances")
    if isinstance(balances, list):
        preferred_types = (
            # ISO-like short codes returned by AIB / Enable Banking
            "ITAV",  # interim available
            "XPCD",  # closing booked (bank-specific code)
            "OPAV",  # opening available
            # Human-readable variants used by some ASPSPs
            "interimAvailable",
            "closingBooked",
            "expected",
            "openingBooked",
        )
        for balance_type in preferred_types:
            for item in balances:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("balance_type") or item.get("balanceType") or "").strip()
                if item_type != balance_type:
                    continue
                amount = item.get("balance_amount") or item.get("balanceAmount") or {}
                if not isinstance(amount, dict):
                    amount = {}
                value = str(amount.get("amount") or amount.get("Amount") or "").strip()
                currency = str(
                    amount.get("currency") or amount.get("Currency") or item.get("currency") or ""
                ).strip()
                if value:
                    return value, currency
        for item in balances:
            if not isinstance(item, dict):
                continue
            amount = item.get("balance_amount") or item.get("balanceAmount") or {}
            if not isinstance(amount, dict):
                amount = {}
            value = str(amount.get("amount") or amount.get("Amount") or "").strip()
            currency = str(
                amount.get("currency") or amount.get("Currency") or item.get("currency") or ""
            ).strip()
            if value:
                return value, currency
    amount = acc.get("balance_amount") or acc.get("balanceAmount") or {}
    if not isinstance(amount, dict):
        amount = {}
    value = str(amount.get("amount") or amount.get("Amount") or "").strip()
    currency = str(amount.get("currency") or amount.get("Currency") or "").strip()
    if value:
        return value, currency
    stored_balance = str(acc.get("balance") or "").strip()
    stored_currency = str(
        acc.get("balance_currency") or acc.get("balanceCurrency") or acc.get("currency") or ""
    ).strip()
    if stored_balance:
        return stored_balance, stored_currency
    return "", ""


def _normalize_account(acc: dict[str, Any], *, enabled: bool | None = None) -> dict[str, Any]:
    uid = acc.get("uid")
    balance, balance_currency = _extract_account_balance(acc)
    normalized = {
        "uid": uid,
        "iban": _account_id_for_consent(acc),
        "identification_hash": acc.get("identification_hash"),
        "name": acc.get("name"),
        "currency": acc.get("currency"),
        "balance": balance,
        "balance_currency": balance_currency,
        "enabled": bool(acc.get("enabled", True) if enabled is None else enabled),
    }
    return normalized


def _normalize_connection(conn: dict[str, Any]) -> dict[str, Any]:
    accounts_raw = conn.get("accounts")
    accounts: list[dict[str, Any]] = []
    if isinstance(accounts_raw, list):
        for item in accounts_raw:
            if isinstance(item, dict) and item.get("uid"):
                accounts.append(_normalize_account(item))
    return {
        "app_id": conn.get("app_id"),
        "aspsp": conn.get("aspsp"),
        "country": conn.get("country"),
        "session_id": conn.get("session_id"),
        "valid_until": conn.get("valid_until"),
        "created_at": conn.get("created_at"),
        "accounts": accounts,
    }


def _legacy_to_connections(record: dict[str, Any]) -> list[dict[str, Any]]:
    accounts_raw = record.get("accounts")
    if not isinstance(accounts_raw, list):
        accounts_raw = []
    enabled_uids = {
        str(uid)
        for uid in (record.get("enabled_account_uids") or [])
        if uid
    }
    accounts: list[dict[str, Any]] = []
    for item in accounts_raw:
        if not isinstance(item, dict) or not item.get("uid"):
            continue
        uid = str(item.get("uid"))
        default_enabled = uid in enabled_uids if enabled_uids else True
        accounts.append(_normalize_account(item, enabled=default_enabled))
    if not accounts and not record.get("aspsp"):
        return []
    return [
        _normalize_connection(
            {
                "aspsp": record.get("aspsp"),
                "country": record.get("country"),
                "session_id": record.get("session_id"),
                "valid_until": record.get("valid_until"),
                "created_at": record.get("created_at"),
                "accounts": accounts,
            }
        )
    ]


def _normalize_consent(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("connections"), list):
        connections = [
            _normalize_connection(conn)
            for conn in record["connections"]
            if isinstance(conn, dict) and conn.get("aspsp") and conn.get("country")
        ]
    else:
        connections = _legacy_to_connections(record)
    normalized = {
        "person": record.get("person", "unknown"),
        "connections": connections,
    }
    for key in ("last_redirect_input", "last_redirect_code", "last_redirect_code_at"):
        value = record.get(key)
        if value:
            normalized[key] = value
    return normalized


def _consent_subset(profile: dict[str, Any]) -> dict[str, Any]:
    connections = profile.get("connections") if isinstance(profile.get("connections"), list) else []
    payload: dict[str, Any] = {
        "person": profile.get("person", "unknown"),
        "connections": connections,
    }
    for key in ("last_redirect_input", "last_redirect_code", "last_redirect_code_at"):
        value = profile.get(key)
        if value:
            payload[key] = value
    return _normalize_consent(payload)


def _apply_consent_subset(profile: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_consent(record)
    out: dict[str, Any] = {
        "person": profile.get("person") or normalized.get("person", "unknown"),
        "connections": normalized["connections"],
    }
    for key in ("last_redirect_input", "last_redirect_code", "last_redirect_code_at"):
        if key in normalized:
            out[key] = normalized[key]
    return out


def _load_consent() -> dict[str, Any]:
    if not paths.PROFILE_PATH.exists():
        return {"person": "unknown", "connections": []}
    return _consent_subset(load_profile())


def _save_consent(record: dict[str, Any]) -> None:
    profile = load_profile()
    _write_json(paths.PROFILE_PATH, _apply_consent_subset(profile, record))


def _find_connection(record: dict[str, Any], aspsp: str, country: str) -> dict[str, Any] | None:
    key = _connection_key(aspsp, country)
    for conn in record.get("connections", []):
        if not isinstance(conn, dict):
            continue
        if _connection_key(str(conn.get("aspsp", "")), str(conn.get("country", ""))) == key:
            return conn
    return None


def _merge_connection(record: dict[str, Any], connection: dict[str, Any]) -> dict[str, Any]:
    connection = _normalize_connection(connection)
    aspsp = str(connection.get("aspsp") or "")
    country = str(connection.get("country") or "")
    existing = _find_connection(record, aspsp, country)
    if existing:
        enabled_by_hash = {
            str(acc.get("identification_hash")): bool(acc.get("enabled", True))
            for acc in existing.get("accounts", [])
            if isinstance(acc, dict) and acc.get("identification_hash")
        }
        for acc in connection.get("accounts", []):
            hash_key = str(acc.get("identification_hash") or "")
            if hash_key and hash_key in enabled_by_hash:
                acc["enabled"] = enabled_by_hash[hash_key]

    connections = [
        conn
        for conn in record.get("connections", [])
        if isinstance(conn, dict)
        and _connection_key(str(conn.get("aspsp", "")), str(conn.get("country", "")))
        != _connection_key(aspsp, country)
    ]
    connections.append(connection)
    return {"person": record.get("person", "unknown"), "connections": connections}


def _build_connection(profile: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    valid_until = (session.get("access") or {}).get("valid_until")
    if not valid_until:
        valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    accounts = [
        _normalize_account(acc, enabled=True)
        for acc in session.get("accounts", [])
        if isinstance(acc, dict) and acc.get("uid")
    ]
    existing = _profile_connection(_consent_subset(profile), profile)
    app_id = str((existing or {}).get("app_id") or profile_app_id(profile) or "")
    aspsp, country = _profile_bank_pair(profile)
    if existing:
        aspsp = str(existing.get("aspsp") or aspsp)
        country = str(existing.get("country") or country)
    return _normalize_connection(
        {
            "app_id": app_id or None,
            "aspsp": aspsp,
            "country": country,
            "session_id": session.get("session_id"),
            "valid_until": valid_until,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "accounts": accounts,
        }
    )


def _profile_connection(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    aspsp, country = _profile_bank_pair(profile)
    if aspsp and country:
        found = _find_connection(record, aspsp, country)
        if found is not None:
            return found
    app_id = profile_app_id(profile)
    if app_id:
        for conn in record.get("connections", []):
            if isinstance(conn, dict) and str(conn.get("app_id") or "") == app_id:
                return conn
    for conn in record.get("connections", []):
        if isinstance(conn, dict):
            return conn
    return None


def _consent_person_matches(record: dict[str, Any], profile: dict[str, Any]) -> bool:
    """Consent lives in the person folder; ``person`` key is optional."""
    got = str(record.get("person") or "").strip()
    if not got:
        return True
    expected = str(paths.PERSON_SHORT or profile.get("person") or "").strip()
    return got.lower() == expected.lower()


def needs_consent_renewal() -> bool:
    """True when the profile bank has no usable session (first consent or renewal)."""
    if not paths.PROFILE_PATH.exists():
        return True
    try:
        profile = load_profile()
    except EnableBankingError:
        return True
    record = _load_consent()
    if not _consent_person_matches(record, profile):
        return True
    connection = _profile_connection(record, profile)
    if connection is None:
        return True
    if not str(connection.get("session_id") or "").strip():
        return True
    accounts = connection.get("accounts")
    if not isinstance(accounts, list) or not any(
        isinstance(acc, dict) and acc.get("uid") for acc in accounts
    ):
        return True
    return _connection_expired(connection)


def _iter_accounts(record: dict[str, Any], *, active_only: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for conn in record.get("connections", []):
        if not isinstance(conn, dict):
            continue
        if active_only and _connection_expired(conn):
            continue
        aspsp = str(conn.get("aspsp") or "")
        country = str(conn.get("country") or "")
        for acc in conn.get("accounts", []):
            if not isinstance(acc, dict) or not acc.get("uid"):
                continue
            items.append({**acc, "aspsp": aspsp, "country": country})
    return items


def _account_index_by_uid() -> dict[str, int]:
    """Ranking of each account uid as listed in consent (0-based, all defined accounts)."""
    return {
        str(acc.get("uid")): index
        for index, acc in enumerate(_iter_accounts(_load_consent(), active_only=False))
        if acc.get("uid")
    }


def account_index_by_uid() -> dict[str, int]:
    """Public wrapper: consent account uid → 0-based index (matches fetch tagging)."""
    return _account_index_by_uid()


def enabled_bank_accounts() -> list[dict[str, Any]]:
    """Active + enabled accounts from profile (same set as transaction fetch)."""
    return [
        acc
        for acc in list_bank_accounts().get("accounts", [])
        if isinstance(acc, dict) and bool(acc.get("enabled")) and bool(acc.get("active"))
    ]


def _load_stored_accounts() -> list[dict[str, Any]]:
    return _iter_accounts(_load_consent(), active_only=False)


def _load_fetch_accounts() -> list[dict[str, Any]]:
    return [
        acc
        for acc in _iter_accounts(_load_consent(), active_only=True)
        if acc.get("enabled", True)
    ]


def list_bank_accounts() -> dict[str, Any]:
    """Linked accounts from all bank connections with enabled flag for the UI."""
    record = _load_consent()
    items: list[dict[str, Any]] = []
    for acc in _iter_accounts(record, active_only=False):
        uid = acc.get("uid")
        if not uid:
            continue
        conn = _find_connection(record, str(acc.get("aspsp") or ""), str(acc.get("country") or ""))
        active = conn is not None and not _connection_expired(conn)
        items.append(
            {
                "uid": str(uid),
                "iban": str(acc.get("iban") or ""),
                "name": str(acc.get("name") or ""),
                "currency": str(acc.get("currency") or ""),
                "balance": str(acc.get("balance") or ""),
                "balance_currency": str(acc.get("balance_currency") or ""),
                "aspsp": str(acc.get("aspsp") or ""),
                "country": str(acc.get("country") or ""),
                "enabled": bool(acc.get("enabled", True)) and active,
                "active": active,
            }
        )
    return {"accounts": items, "needs_renewal": needs_consent_renewal()}


def _balance_to_cents(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return round(float(text) * 100)
    except ValueError:
        return None


def current_balance_payload() -> dict[str, Any]:
    """Account balances for category_totals.json (no duplicate current_balance fields)."""
    account_balances: list[dict[str, str]] = []
    for acc in list_bank_accounts().get("accounts", []):
        if not isinstance(acc, dict):
            continue
        if not bool(acc.get("active")) or not bool(acc.get("enabled")):
            continue
        account_balances.append(
            {
                "uid": str(acc.get("uid") or ""),
                "iban": str(acc.get("iban") or ""),
                "name": str(acc.get("name") or ""),
                "currency": str(acc.get("balance_currency") or acc.get("currency") or "").strip().upper(),
                "balance": str(acc.get("balance") or "").strip(),
            }
        )
    return {"account_balances": account_balances}


def set_enabled_account_uids(uids: list[str]) -> dict[str, Any]:
    record = _load_consent()
    known = {str(acc.get("uid")) for acc in _iter_accounts(record) if acc.get("uid")}
    if not known:
        raise EnableBankingError("No linked accounts in consent.")
    selected = {str(uid) for uid in uids if str(uid) in known}
    if not selected:
        raise EnableBankingError("At least one account must be enabled.")
    for conn in record.get("connections", []):
        if not isinstance(conn, dict):
            continue
        for acc in conn.get("accounts", []):
            if isinstance(acc, dict) and acc.get("uid"):
                acc["enabled"] = str(acc.get("uid")) in selected
    _save_consent(record)
    return list_bank_accounts()


def _save_session_connection(profile: dict[str, Any], session: dict[str, Any]) -> None:
    record = _load_consent()
    record["person"] = paths.PERSON_SHORT or profile.get("person", record.get("person", "unknown"))
    record = _merge_connection(record, _build_connection(profile, session))
    _save_consent(record)


def _store_last_redirect_code(code_or_url: str) -> None:
    """Persist the latest redirect input/code in consent for troubleshooting/reuse."""
    text = str(code_or_url or "").strip()
    if not text:
        return
    record = _load_consent()
    record["last_redirect_input"] = text
    record["last_redirect_code"] = _extract_code(text)
    record["last_redirect_code_at"] = datetime.now(timezone.utc).isoformat()
    _save_consent(record)


_CREDIT_CARD_LABEL = "Credit Card"


def _apply_credit_card_label_for_empty_iban(record: dict[str, Any]) -> bool:
    """Set ``iban`` to ``Credit Card`` for accounts that still have no identifier."""
    changed = False
    for conn in record.get("connections", []):
        if not isinstance(conn, dict):
            continue
        for acc in conn.get("accounts", []):
            if not isinstance(acc, dict):
                continue
            if not str(acc.get("iban") or "").strip():
                acc["iban"] = _CREDIT_CARD_LABEL
                changed = True
    return changed


def ensure_consent_credit_card_labels() -> None:
    """Persist default labels for non-IBAN accounts in consent."""
    record = _load_consent()
    if _apply_credit_card_label_for_empty_iban(record):
        _save_consent(record)


def _refresh_account_balances(client: SingleDockerClient, account_uids: list[str]) -> None:
    """Refresh balance fields from GET /accounts/{uid}/balances only."""
    if not account_uids:
        return
    record = _load_consent()
    by_uid: dict[str, dict[str, Any]] = {}
    for conn in record.get("connections", []):
        if not isinstance(conn, dict):
            continue
        for acc in conn.get("accounts", []):
            if isinstance(acc, dict) and acc.get("uid"):
                by_uid[str(acc.get("uid"))] = acc

    changed = False
    for uid in account_uids:
        target = by_uid.get(uid)
        if target is None:
            continue
        try:
            balances = client.get_account_balances(uid)
        except EnableBankingError:
            continue
        probe = {"balances": balances}
        balance, balance_currency = _extract_account_balance(probe)
        if balances:
            target["balances"] = balances
        if balance:
            if str(target.get("balance") or "") != balance:
                target["balance"] = balance
                changed = True
            currency = balance_currency or str(target.get("currency") or "EUR")
            if str(target.get("balance_currency") or "") != currency:
                target["balance_currency"] = currency
                changed = True
    if _apply_credit_card_label_for_empty_iban(record):
        changed = True
    if changed:
        _save_consent(record)


def _is_already_authorized_error(exc: EnableBankingError) -> bool:
    return "ALREADY_AUTHORIZED" in str(exc)


def _reject_non_bank_auth_url(url: str) -> None:
    """Ensure Enable Banking returned a bank login URL, not our callback (with or without error)."""
    lower = url.lower()
    if (
        "127.0.0.1" in lower
        or "localhost" in lower
        or "/api/consent/callback" in lower
        or "banking-callback.html" in lower
        or "error=" in lower
        or not lower.startswith("https://")
    ):
        hint = (
            "Check the Enable Banking application for this person: redirect URL must be "
            f"exactly {DEFAULT_REDIRECT!r}, ING (NL) must be linked as personal, "
            "then request a fresh authorization link."
        )
        raise EnableBankingError(
            "Enable Banking did not return a bank authorization URL "
            f"(got {url!r}). {hint}"
        )


def get_authorization_url(
    *,
    workspace: str | None = None,
    person_short: str | None = None,
    folder: str | None = None,
) -> str:
    """Start Enable Banking auth; returns the bank authorization URL (not the local callback)."""
    from app import consent_flow
    from app.runtime import active_workspace

    profile = load_profile()
    client = SingleDockerClient.from_profile(profile)
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    # Do not override Enable Banking ``state`` — keep the auth request as before.
    auth = client.start_authorization(profile, valid_until)
    url = str(auth.get("url") or "").strip()
    if not url:
        raise EnableBankingError("Enable Banking did not return an authorization URL.")
    _reject_non_bank_auth_url(url)

    ws = (workspace or active_workspace() or "").strip()
    short = (person_short or str(profile.get("person") or "").strip() or "").strip()
    fold = (folder or "").strip() or short
    # Bind callback to whatever state Enable Banking put on the auth URL / response.
    state = str(auth.get("state") or "").strip()
    if not state:
        try:
            state = (parse_qs(urlparse(url).query).get("state") or [""])[0].strip()
        except Exception:
            state = ""
    if not state:
        state = str(auth.get("authorization_id") or "").strip()
    if ws and short and state:
        consent_flow.register_pending(
            workspace=ws, short=short, folder=fold, state=state
        )
    elif ws and short:
        consent_flow.register_pending(
            workspace=ws, short=short, folder=fold, state=None
        )
    return url


def _linked_accounts(
    profile: dict[str, Any], client: SingleDockerClient, redirect_code: str | None
) -> tuple[list[dict[str, Any]], bool]:
    """Return linked accounts and whether consent was renewed in this call."""
    renewed = False

    # A fresh redirect code must always create/save a session — do not skip when
    # an older still-fetchable connection exists in profile.json.
    if redirect_code:
        try:
            session = client.create_session(_extract_code(redirect_code))
        except EnableBankingError as exc:
            if _is_already_authorized_error(exc):
                fetchable = _load_fetch_accounts()
                if fetchable:
                    return fetchable, _connection_created_today(profile)
                raise EnableBankingError(
                    "This redirect URL was already used. Restart the app to get a new "
                    "authorization URL, complete bank login again, and paste the new "
                    "redirect URL once."
                ) from exc
            raise
        _store_last_redirect_code(redirect_code)
        accounts = session.get("accounts", [])
        if not accounts:
            raise EnableBankingError("No accounts were linked during authorization.")
        _save_session_connection(profile, session)
        renewed = True

    fetchable = _load_fetch_accounts()
    if fetchable:
        return fetchable, renewed or _connection_created_today(profile)

    if renewed:
        raise EnableBankingError(
            "No accounts enabled for fetch. Enable at least one account in the sidebar."
        )

    if needs_consent_renewal():
        raise EnableBankingError("Redirect code is required to renew bank consent.")

    if _load_stored_accounts():
        raise EnableBankingError(
            "No accounts enabled for fetch. Enable at least one account in the sidebar."
        )

    raise EnableBankingError("No linked accounts available.")


def complete_authorization(redirect_code: str) -> dict[str, Any]:
    """Exchange a redirect code for a session and update profile.json connections."""
    profile = load_profile()
    client = SingleDockerClient.from_profile(profile)
    try:
        session = client.create_session(_extract_code(redirect_code))
    except EnableBankingError as exc:
        if _is_already_authorized_error(exc):
            if _load_fetch_accounts():
                return list_bank_accounts()
            raise EnableBankingError(
                "This redirect URL was already used. Start Authorization URL again."
            ) from exc
        raise
    accounts = session.get("accounts", [])
    if not accounts:
        raise EnableBankingError("No accounts were linked during authorization.")
    _store_last_redirect_code(redirect_code)
    _save_session_connection(profile, session)
    return list_bank_accounts()


def fetch_transactions(
    date_from: str | None = None,
    date_to: str | None = None,
    redirect_code: str | None = None,
) -> FetchResult:
    """Download raw transactions from the bank and return them."""
    profile = load_profile()
    client = SingleDockerClient.from_profile(profile)
    accounts, renewed_session = _linked_accounts(profile, client, redirect_code)
    renewal_day = renewed_session or _connection_created_today(profile)
    resolved_from, resolved_to, warnings = _resolve_fetch_dates(
        date_from, date_to, renewal_day=renewal_day
    )

    index_by_uid = _account_index_by_uid()
    raw_transactions: list[dict[str, Any]] = []
    account_errors: list[str] = []
    for account in accounts:
        account_uid = str(account.get("uid") or "")
        if not account_uid:
            continue
        account_index = index_by_uid.get(account_uid, 0)
        label = str(account.get("iban") or account.get("name") or account_uid)
        try:
            batch = client.get_transactions(account_uid, date_from=resolved_from, date_to=resolved_to)
        except EnableBankingError as exc:
            account_errors.append(f"{label}: {exc}")
            continue
        for tx in batch:
            tagged = dict(tx)
            tagged["_account_index"] = account_index
            tagged["_account_uid"] = account_uid
            raw_transactions.append(tagged)

    if not raw_transactions and account_errors:
        raise EnableBankingError("; ".join(account_errors))

    _refresh_account_balances(
        client,
        [str(account.get("uid")) for account in accounts if account.get("uid")],
    )

    try:
        from app.core.categorize import refresh_category_totals_balances

        refresh_category_totals_balances()
    except Exception:
        pass

    return FetchResult(
        transactions=raw_transactions,
        date_from=resolved_from,
        date_to=resolved_to,
        renewal_day=renewal_day,
        warnings=warnings,
        account_errors=account_errors,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download bank transactions via Enable Banking")
    parser.add_argument("--redirect-code", default=None, help="Redirect URL or code after bank approval")
    parser.add_argument("--date-from", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--date-to", default=None, help="End date YYYY-MM-DD")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        result = fetch_transactions(
            date_from=args.date_from,
            date_to=args.date_to,
            redirect_code=args.redirect_code,
        )
    except EnableBankingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Downloaded {len(result.transactions)} transactions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
