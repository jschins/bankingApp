"""Transaction pagination, date chunking, and deduplication utilities.

These sit on top of :class:`EnableBankingClient` and handle the real-world
complications of PSD2 transaction fetching:

* **Date chunking** — some banks reject large date ranges; split into smaller
  windows with :func:`date_period_chunks`.
* **Pagination with limits** — follow ``continuation_key`` but bail out after a
  configurable page limit to prevent infinite loops.
* **Truncation detection** — detect when a bank returned a full page, meaning
  more data exists and the range should be split.
* **Recursive split-and-merge** — :func:`fetch_transactions_period` recursively
  halves a date range on truncation or ASPSP errors until each sub-range fits.
* **Deduplication** — :func:`dedupe_transactions` removes duplicate entries by
  reference id or full-body hash.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from . import EnableBankingClient, EnableBankingError

TRANSACTIONS_PAGE_LIMIT = 250
DATE_CHUNK_DAYS = 30
MAX_TRANSACTION_PAGES = 500


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def date_period_chunks(
    date_from: str, date_to: str, *, chunk_days: int = DATE_CHUNK_DAYS
) -> list[tuple[str, str]]:
    """Split ``[date_from, date_to]`` into contiguous windows of ``chunk_days``."""
    start = parse_iso_date(date_from)
    end = parse_iso_date(date_to)
    if start > end:
        raise EnableBankingError(f"Invalid transaction period: {date_from} > {date_to}")
    chunks: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def transaction_key(tx: dict[str, Any]) -> str:
    ref = tx.get("entry_reference") or tx.get("transaction_id") or tx.get("id")
    if ref is not None and str(ref).strip():
        return str(ref).strip()
    return json.dumps(tx, sort_keys=True, default=str)


def dedupe_transactions(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for tx in transactions:
        key = transaction_key(tx)
        if key in seen:
            continue
        seen.add(key)
        unique.append(tx)
    return unique


def _is_aspsp_error(exc: EnableBankingError) -> bool:
    return "ASPSP_ERROR" in str(exc)


def fetch_transactions_pages(
    client: EnableBankingClient,
    account_uid: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch one API period, following ``continuation_key`` until exhausted.

    Returns ``(transactions, was_truncated)``.
    """
    all_transactions: list[dict[str, Any]] = []
    continuation_key: str | None = None
    base_params: dict[str, str] = {}
    if date_from:
        base_params["date_from"] = date_from
    if date_to:
        base_params["date_to"] = date_to

    pages = 0
    last_batch_len = 0
    while True:
        pages += 1
        if pages > MAX_TRANSACTION_PAGES:
            raise EnableBankingError(
                f"Transaction pagination exceeded {MAX_TRANSACTION_PAGES} pages "
                f"for account {account_uid} ({date_from} .. {date_to})."
            )
        params = dict(base_params)
        if continuation_key:
            params["continuation_key"] = continuation_key
        resp = client._request("GET", f"/accounts/{account_uid}/transactions", params=params)
        if not isinstance(resp, dict):
            resp = {}
        batch = resp.get("transactions")
        if not isinstance(batch, list):
            batch = []
        all_transactions.extend(item for item in batch if isinstance(item, dict))
        last_batch_len = len(batch)
        continuation_key = resp.get("continuation_key")
        if not continuation_key:
            break

    truncated = (
        last_batch_len >= TRANSACTIONS_PAGE_LIMIT
        and date_from is not None
        and date_to is not None
        and parse_iso_date(date_from) < parse_iso_date(date_to)
    )
    return all_transactions, truncated


def fetch_transactions_period(
    client: EnableBankingClient,
    account_uid: str,
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    """Fetch a date range; split recursively on truncation or ASPSP errors."""
    start = parse_iso_date(date_from)
    end = parse_iso_date(date_to)
    try:
        transactions, truncated = fetch_transactions_pages(client, account_uid, date_from, date_to)
    except EnableBankingError as exc:
        if not _is_aspsp_error(exc) or start >= end:
            raise
        midpoint = start + (end - start) // 2
        left = fetch_transactions_period(client, account_uid, date_from, midpoint.isoformat())
        right = fetch_transactions_period(
            client,
            account_uid,
            (midpoint + timedelta(days=1)).isoformat(),
            date_to,
        )
        return dedupe_transactions(left + right)

    if not truncated:
        return transactions

    if start >= end:
        return transactions

    midpoint = start + (end - start) // 2
    left = fetch_transactions_period(client, account_uid, date_from, midpoint.isoformat())
    right = fetch_transactions_period(
        client,
        account_uid,
        (midpoint + timedelta(days=1)).isoformat(),
        date_to,
    )
    return dedupe_transactions(left + right)
