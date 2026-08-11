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


class SingleDockerClient(EnableBankingClient):
    """Enable Banking client extended with AIB-aware transaction fetching."""

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> SingleDockerClient:
        app_id = str(profile.get("app_id") or "")
        if not paths.PRIVATE_KEY_PATH.exists():
            raise EnableBankingError(f"Private key file not found: {paths.PRIVATE_KEY_PATH}")
        return cls(app_id, paths.PRIVATE_KEY_PATH.read_bytes())

    def start_authorization(self, profile: dict[str, Any], valid_until: str) -> dict[str, Any]:
        return super().start_authorization(
            aspsp_name=profile["aspsp"],
            country=profile["country"],
            redirect_url=profile["redirect_url"],
            valid_until=valid_until,
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


def load_profile() -> dict[str, Any]:
    if not paths.PROFILE_PATH.exists():
        raise EnableBankingError(f"Profile not found: {paths.PROFILE_PATH}")
    profile = _read_json(paths.PROFILE_PATH)
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


def _load_consent() -> dict[str, Any]:
    if not paths.CONSENT_PATH.exists():
        return {"person": "unknown", "connections": []}
    raw = _read_json(paths.CONSENT_PATH)
    record = _normalize_consent(raw)
    if "connections" not in raw or "enabled_account_uids" in raw or raw.get("aspsp"):
        _save_consent(record)
    return record


def _save_consent(record: dict[str, Any]) -> None:
    _write_json(paths.CONSENT_PATH, _normalize_consent(record))


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
    return _normalize_connection(
        {
            "aspsp": profile["aspsp"],
            "country": profile["country"],
            "session_id": session.get("session_id"),
            "valid_until": valid_until,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "accounts": accounts,
        }
    )


def _profile_connection(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    return _find_connection(record, str(profile.get("aspsp") or ""), str(profile.get("country") or ""))


def _consent_person_matches(record: dict[str, Any], profile: dict[str, Any]) -> bool:
    return str(record.get("person") or "") == str(profile.get("person") or "")


def needs_consent_renewal() -> bool:
    """True when the profile bank has no valid connection (for the consent banner)."""
    if not paths.CONSENT_PATH.exists():
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
    """Aggregate enabled active account balances for exports/status output."""
    by_currency: dict[str, int] = {}
    account_balances: list[dict[str, str]] = []
    for acc in list_bank_accounts().get("accounts", []):
        if not isinstance(acc, dict):
            continue
        if not bool(acc.get("active")) or not bool(acc.get("enabled")):
            continue
        account_entry = {
            "uid": str(acc.get("uid") or ""),
            "iban": str(acc.get("iban") or ""),
            "name": str(acc.get("name") or ""),
            "currency": str(acc.get("balance_currency") or acc.get("currency") or "").strip().upper(),
            "balance": str(acc.get("balance") or "").strip(),
        }
        account_balances.append(account_entry)
        cents = _balance_to_cents(acc.get("balance"))
        if cents is None:
            continue
        currency = str(acc.get("balance_currency") or acc.get("currency") or "").strip().upper()
        if not currency:
            currency = "EUR"
        by_currency[currency] = by_currency.get(currency, 0) + cents

    balance_by_currency = {
        currency: f"{cents / 100:.2f}" for currency, cents in sorted(by_currency.items())
    }
    if len(balance_by_currency) == 1:
        currency, amount = next(iter(balance_by_currency.items()))
        return {
            "current_balance": amount,
            "current_balance_currency": currency,
            "current_balance_by_currency": balance_by_currency,
            "account_balances": account_balances,
        }
    if not balance_by_currency:
        fallback_currency = "EUR"
        for acc in list_bank_accounts().get("accounts", []):
            if not isinstance(acc, dict):
                continue
            if bool(acc.get("active")) and bool(acc.get("enabled")):
                fallback_currency = str(acc.get("currency") or "EUR").upper() or "EUR"
                break
        return {
            "current_balance": "0.00",
            "current_balance_currency": fallback_currency,
            "current_balance_by_currency": {},
            "account_balances": account_balances,
        }
    return {
        "current_balance": "",
        "current_balance_currency": "",
        "current_balance_by_currency": balance_by_currency,
        "account_balances": account_balances,
    }


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
    record["person"] = profile.get("person", record.get("person", "unknown"))
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


def get_authorization_url() -> str:
    profile = load_profile()
    client = SingleDockerClient.from_profile(profile)
    valid_until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    auth = client.start_authorization(profile, valid_until)
    url = auth.get("url", "")
    if not url:
        raise EnableBankingError("Enable Banking did not return an authorization URL.")
    return url


def _linked_accounts(
    profile: dict[str, Any], client: SingleDockerClient, redirect_code: str | None
) -> tuple[list[dict[str, Any]], bool]:
    """Return linked accounts and whether consent was renewed in this call."""
    renewed = False

    # A fresh redirect code must always create/save a session — do not skip when
    # an older still-fetchable connection exists in consent.json.
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
    """Exchange a redirect code for a session and overwrite consent.json."""
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
