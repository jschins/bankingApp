from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from paths import (
    CATEGORIES_PATH,
    CATEGORIZED_TRANSACTIONS_PATH,
    CATEGORY_TOTALS_PATH,
    PERSONAL_CATEGORIES_PATH,
    RAW_TRANSACTIONS_PATH,
)

DEFAULT_CATEGORY = 18
CASH_CATEGORY = 8
CASH_TYPE = "geldautomaat"
_HAYSTACK_FIELDS = ("name", "description")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _category_map(data: dict[str, Any]) -> dict[str, list[str]]:
    nested = data.get("categories")
    return nested if isinstance(nested, dict) else data


def _amount(transaction: dict[str, Any]) -> str:
    amount = str((transaction.get("transaction_amount") or {}).get("amount", "")).strip()
    sign = "+" if transaction.get("credit_debit_indicator") == "CRDT" else "-"
    return f"{sign}{amount}" if amount else ""


def _currency(transaction: dict[str, Any]) -> str:
    return str((transaction.get("transaction_amount") or {}).get("currency", "")).strip()


def _type(transaction: dict[str, Any]) -> str:
    return str((transaction.get("bank_transaction_code") or {}).get("description") or "").strip()


def _naam(transaction: dict[str, Any]) -> str:
    party_key = "creditor" if transaction.get("credit_debit_indicator") == "DBIT" else "debtor"
    party = transaction.get(party_key) or {}
    return str(party.get("name") or "").strip()


def _omschrijving(transaction: dict[str, Any]) -> str:
    lines = transaction.get("remittance_information") or []
    return " ".join(str(line).strip() for line in lines if line)


def _iban(transaction: dict[str, Any]) -> str:
    for line in transaction.get("remittance_information") or []:
        if isinstance(line, str) and ":" in line:
            prefix, value = line.split(":", 1)
            if prefix.strip() == "IBAN":
                return value.strip()
    return ""


def _booking_date(transaction: dict[str, Any]) -> str:
    raw = str(transaction.get("booking_date") or "").strip()
    parts = raw.split("-")
    if len(parts) == 3:
        year, month, day = parts
        return f"{day}-{month}-{year}"
    return raw


def _category_code(name: str) -> int | None:
    try:
        return int(str(name)[:2])
    except ValueError:
        return None


def _matches_word(field: str, haystack: str) -> bool:
    return re.search(rf"\b{re.escape(field)}\b", haystack) is not None


def categorize(record: dict[str, Any], *category_groups: dict[str, list[str]]) -> int:
    haystack = " ".join(str(record.get(field, "")) for field in _HAYSTACK_FIELDS).lower()

    category = DEFAULT_CATEGORY
    if str(record.get("type", "")).lower() == CASH_TYPE:
        category = CASH_CATEGORY

    for group in category_groups:
        for name, fields in group.items():
            code = _category_code(name)
            if code is None:
                continue
            for field in fields or []:
                if field and _matches_word(str(field).lower(), haystack):
                    category = code
    return category


def simplify_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": transaction.get("entry_reference"),
        "amount": _amount(transaction),
        "currency": _currency(transaction),
        "type": _type(transaction),
        "name": _naam(transaction),
        "IBAN": _iban(transaction),
        "description": _omschrijving(transaction),
        "date": _booking_date(transaction),
    }


def _tx_sort_key(transaction: Any) -> int:
    tid = transaction.get("id") if isinstance(transaction, dict) else None
    text = str(tid) if tid is not None else ""
    return int(text) if text.isdigit() else -1


def _merge_simplified(existing: dict[str, Any], new_records: list[dict[str, Any]]) -> dict[str, Any]:
    existing_tx = existing.get("transactions")
    existing_tx = existing_tx if isinstance(existing_tx, list) else []
    seen = {t.get("id") for t in existing_tx if isinstance(t, dict)}

    merged = list(existing_tx)
    for record in new_records:
        if record.get("id") not in seen:
            merged.append(record)
            seen.add(record.get("id"))
    merged.sort(key=_tx_sort_key, reverse=True)

    result = dict(existing)
    result["transactions"] = merged
    return result


def _simplify_and_categorize(
    raw_transactions: list[dict[str, Any]],
    general: dict[str, list[str]],
    personal: dict[str, list[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for transaction in raw_transactions:
        record = simplify_transaction(transaction)
        record["category"] = categorize(record, general, personal)
        records.append(record)
    return records


def _amount_to_cents(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return round(float(text) * 100)
    except ValueError:
        return 0


def _amount_str(cents: int) -> str:
    return f"{cents / 100:.2f}"


def build_category_totals(
    transactions_payload: dict[str, Any], general_names: list[str]
) -> dict[str, str]:
    """Per-category signed totals, honoring modifications overlays by id."""
    name_by_code = {
        code: name for name in general_names if (code := _category_code(name)) is not None
    }
    totals: dict[str, int] = {name: 0 for name in general_names}

    modifications = transactions_payload.get("modifications")
    mods_by_id = {
        item.get("id"): item
        for item in (modifications if isinstance(modifications, list) else [])
        if isinstance(item, dict)
    }

    for transaction in transactions_payload.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        effective = mods_by_id.get(transaction.get("id"), transaction)
        code = effective.get("category")
        name = name_by_code.get(code, str(code))
        totals[name] = totals.get(name, 0) + _amount_to_cents(effective.get("amount"))

    return {name: _amount_str(cents) for name, cents in totals.items()}


def _write_category_totals(merged: dict[str, Any], general: dict[str, list[str]]) -> dict[str, str]:
    totals = build_category_totals(merged, list(general.keys()))
    _write_json(CATEGORY_TOTALS_PATH, {"categories": totals})
    return totals


def load_category_totals() -> dict[str, str]:
    data = _load_json_object(CATEGORY_TOTALS_PATH)
    categories = data.get("categories")
    if not isinstance(categories, dict):
        return {}
    return {str(name): str(amount) for name, amount in categories.items()}


def recategorize_transactions() -> dict[str, str]:
    """Re-apply keyword categorisation to distilled transactions and refresh totals.

    User ``modifications`` are left untouched; totals still honor them.
    """
    data = _load_json_object(CATEGORIZED_TRANSACTIONS_PATH)
    transactions = data.get("transactions")
    general = _category_map(_read_json(CATEGORIES_PATH))
    personal = _category_map(_read_json(PERSONAL_CATEGORIES_PATH))

    if isinstance(transactions, list):
        for record in transactions:
            if isinstance(record, dict):
                record["category"] = categorize(record, general, personal)
        data["transactions"] = transactions
        _write_json(CATEGORIZED_TRANSACTIONS_PATH, data)

    return _write_category_totals(data, general)


def transactions_for_category(category_name: str) -> list[dict[str, Any]]:
    """Return effective transactions for a category display name (e.g. ``09 Pension``)."""
    code = _category_code(category_name)
    if code is None:
        return []

    payload = _load_json_object(CATEGORIZED_TRANSACTIONS_PATH)
    modifications = payload.get("modifications")
    mods_by_id = {
        item.get("id"): item
        for item in (modifications if isinstance(modifications, list) else [])
        if isinstance(item, dict)
    }

    transactions: list[dict[str, Any]] = []
    for transaction in payload.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        effective = mods_by_id.get(transaction.get("id"), transaction)
        if effective.get("category") == code:
            transactions.append(effective)
    return transactions


_HIDDEN_TABLE_COLUMNS = frozenset({"id", "currency"})
_DESCRIPTION_COLUMN = "description"
_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def transaction_display_column_keys(transactions: list[dict[str, Any]]) -> list[str]:
    if not transactions:
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for transaction in transactions:
        for key in transaction:
            if key in _HIDDEN_TABLE_COLUMNS or key in seen:
                continue
            seen.add(key)
            keys.append(key)
    if _DESCRIPTION_COLUMN in keys:
        keys.remove(_DESCRIPTION_COLUMN)
        keys.append(_DESCRIPTION_COLUMN)
    return keys


def format_transaction_amount(transaction: dict[str, Any]) -> str:
    amount = str(transaction.get("amount", "")).strip()
    currency = str(transaction.get("currency", "")).strip().upper()
    symbol = _CURRENCY_SYMBOLS.get(currency, f"{currency} " if currency else "€")
    return f"{symbol}{amount}"


def terms_for_category(category_name: str) -> list[str]:
    """General + personal keyword terms for a category display name."""
    general = _category_map(_read_json(CATEGORIES_PATH))
    personal = _category_map(_read_json(PERSONAL_CATEGORIES_PATH))
    return [*general.get(category_name, []), *personal.get(category_name, [])]


def category_name_for_column_key(column_key: Any) -> str | None:
    for name in category_names():
        if _category_column_key(name) == column_key:
            return name
    return None


def _cleaned_terms(terms: list[str]) -> list[str]:
    return [term.strip() for term in terms if isinstance(term, str) and term.strip()]


def _save_general_category_terms(category_name: str, terms: list[str]) -> None:
    data = _read_json(CATEGORIES_PATH)
    categories = data.setdefault("categories", {})
    categories[category_name] = _cleaned_terms(terms)
    _write_json(CATEGORIES_PATH, data)


def _save_personal_category_terms(category_name: str, terms: list[str]) -> None:
    data = _load_json_object(PERSONAL_CATEGORIES_PATH)
    cleaned = _cleaned_terms(terms)
    if cleaned:
        data[category_name] = cleaned
    else:
        data.pop(category_name, None)
    _write_json(PERSONAL_CATEGORIES_PATH, data)


def add_category_term(category_name: str, term: str) -> list[str]:
    """Append a term to the personal keyword list for a category."""
    cleaned_term = term.strip()
    if not cleaned_term:
        return terms_for_category(category_name)
    if cleaned_term in terms_for_category(category_name):
        return terms_for_category(category_name)

    personal = _category_map(_load_json_object(PERSONAL_CATEGORIES_PATH))
    personal_terms = list(personal.get(category_name, []))
    personal_terms.append(cleaned_term)
    _save_personal_category_terms(category_name, personal_terms)
    return terms_for_category(category_name)


def remove_category_term(category_name: str, term: str) -> list[str]:
    """Remove a term from personal keywords, otherwise from general keywords."""
    personal = _category_map(_load_json_object(PERSONAL_CATEGORIES_PATH))
    general = _category_map(_read_json(CATEGORIES_PATH))
    personal_terms = list(personal.get(category_name, []))
    general_terms = list(general.get(category_name, []))

    if term in personal_terms:
        _save_personal_category_terms(
            category_name, [existing for existing in personal_terms if existing != term]
        )
    elif term in general_terms:
        _save_general_category_terms(
            category_name, [existing for existing in general_terms if existing != term]
        )
    return terms_for_category(category_name)


def category_terms_table(extra_rows: int = 0) -> tuple[list[tuple[str, str]], list[list[str]]]:
    """Column (name, key) pairs and term rows for the keywords overview table."""
    general = _category_map(_read_json(CATEGORIES_PATH))
    personal = _category_map(_read_json(PERSONAL_CATEGORIES_PATH))
    category_names = list(general.keys())
    terms_by_category = {
        name: [*general.get(name, []), *personal.get(name, [])] for name in category_names
    }
    max_rows = max((len(terms) for terms in terms_by_category.values()), default=0) + extra_rows
    rows = [
        [
            terms_by_category[name][index] if index < len(terms_by_category[name]) else ""
            for name in category_names
        ]
        for index in range(max_rows)
    ]
    columns = [(name, _category_column_key(name)) for name in category_names]
    return columns, rows


def save_category_terms_column(category_name: str, terms: list[str]) -> list[str]:
    """Persist the merged column term list for a category."""
    cleaned = _cleaned_terms(terms)
    _save_general_category_terms(category_name, cleaned)
    _save_personal_category_terms(category_name, [])
    return terms_for_category(category_name)


def set_category_term_cell(category_name: str, row_index: int, value: str) -> list[str]:
    """Update one CT-table cell and save the column."""
    terms = terms_for_category(category_name)
    cleaned_value = value.strip()

    if row_index < 0:
        return terms

    if row_index < len(terms):
        if cleaned_value:
            terms[row_index] = cleaned_value
        else:
            terms.pop(row_index)
    elif cleaned_value:
        terms.extend([""] * (row_index - len(terms)))
        terms.append(cleaned_value)

    return save_category_terms_column(category_name, terms)


def _category_column_key(name: str) -> str:
    code = _category_code(name)
    return f"cat_{code}" if code is not None else name.replace(" ", "_")


def category_names() -> list[str]:
    general = _category_map(_read_json(CATEGORIES_PATH))
    return list(general.keys())


def _source_transaction_by_id(
    transaction_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return the modification for ``id`` if present, otherwise the base transaction."""
    data = payload if payload is not None else _load_json_object(CATEGORIZED_TRANSACTIONS_PATH)

    modifications = data.get("modifications")
    if isinstance(modifications, list):
        for transaction in modifications:
            if isinstance(transaction, dict) and str(transaction.get("id", "")) == transaction_id:
                return dict(transaction)

    for transaction in data.get("transactions", []):
        if isinstance(transaction, dict) and str(transaction.get("id", "")) == transaction_id:
            return dict(transaction)
    return None


def record_modification(transaction: dict[str, Any]) -> dict[str, Any]:
    """Store a full modified transaction under ``modifications``, keyed by ``id``.

    If an entry with the same ``id`` already exists, it is replaced in place.
    """
    data = _load_json_object(CATEGORIZED_TRANSACTIONS_PATH)
    if not data:
        data = {"transactions": []}

    modifications = data.get("modifications")
    if not isinstance(modifications, list):
        modifications = []

    modified = dict(transaction)
    transaction_id = str(modified.get("id", ""))
    if not transaction_id:
        raise ValueError("Transaction id is required for a modification")

    for index, existing in enumerate(modifications):
        if isinstance(existing, dict) and str(existing.get("id", "")) == transaction_id:
            modifications[index] = modified
            break
    else:
        modifications.append(modified)

    data["modifications"] = modifications
    _write_json(CATEGORIZED_TRANSACTIONS_PATH, data)
    general = _category_map(_read_json(CATEGORIES_PATH))
    _write_category_totals(data, general)
    return modified


def record_category_change(transaction: dict[str, Any], category_name: str) -> dict[str, Any]:
    """Copy the source transaction into ``modifications`` with a new category code."""
    code = _category_code(category_name)
    if code is None:
        raise ValueError(f"Unknown category: {category_name!r}")

    transaction_id = str(transaction.get("id", ""))
    data = _load_json_object(CATEGORIZED_TRANSACTIONS_PATH)
    source = _source_transaction_by_id(transaction_id, data) or dict(transaction)
    modified = dict(source)
    modified["category"] = code
    return record_modification(modified)


def process_transactions(raw_transactions: list[dict[str, Any]], new_year: bool) -> dict[str, str]:
    """Distill raw bank JSON into ``both/categorized_transactions.json``.

    When *new_year* is false, append any transaction whose ``id`` is not already
    present and keep existing ``modifications``. When *new_year* is true,
    replace the file with only this fetch (no merge, no modifications).
    """
    general = _category_map(_read_json(CATEGORIES_PATH))
    personal = _category_map(_read_json(PERSONAL_CATEGORIES_PATH))
    new_records = _simplify_and_categorize(raw_transactions, general, personal)

    if new_year:
        merged = _merge_simplified({"transactions": []}, new_records)
    else:
        existing = _load_json_object(CATEGORIZED_TRANSACTIONS_PATH)
        if not isinstance(existing.get("transactions"), list):
            existing = {"transactions": []}
        merged = _merge_simplified(existing, new_records)
        modifications = existing.get("modifications")
        if isinstance(modifications, list):
            merged["modifications"] = modifications

    _write_json(RAW_TRANSACTIONS_PATH, raw_transactions)
    _write_json(CATEGORIZED_TRANSACTIONS_PATH, merged)
    return recategorize_transactions()
