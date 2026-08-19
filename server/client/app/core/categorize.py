from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import app.paths as paths

DEFAULT_CATEGORY = 18
CATEGORIZE_LOGIC_VERSION = "2026-07-09-typerules-highest-priority"
_TERM_AND_SEP = " && "
_ACCOUNT_INDEX_FIELD = "_account_index"


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


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)


def _is_date_or_datetime(value: str) -> bool:
    value = value.strip()
    return bool(_DATE_ONLY.fullmatch(value) or _DATETIME.fullmatch(value))


def _parse_brace_key_values(block: str) -> dict[str, str]:
    inner = block.strip()
    if inner.startswith("{"):
        inner = inner[1:].lstrip()
    if inner.endswith("}"):
        inner = inner[:-1].rstrip()
    pairs: dict[str, str] = {}
    for part in inner.split(","):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            pairs[key] = value
    return pairs


def _format_brace_key_values(pairs: dict[str, str]) -> str:
    if not pairs:
        return ""
    inner = " , ".join(f"{key} : {value}" for key, value in pairs.items())
    return f"{{ {inner} }}"


def _split_bracketed_remittance(lines: list[str]) -> tuple[str, str] | None:
    if not lines:
        return None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            prefix = " ".join(
                part.strip() for i, part in enumerate(lines) if i != index and part.strip()
            )
            return prefix, stripped
    if len(lines) == 1:
        match = re.match(r"^(.*?)(\{[^{}]*\})\s*$", lines[0], re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return None


def _remittance_iban_from_lines(lines: list[str]) -> str:
    for line in lines:
        if ":" in line:
            prefix, value = line.split(":", 1)
            if prefix.strip() == "IBAN":
                return value.strip()
    return ""


def _pop_brace_key(pairs: dict[str, str], key_name: str) -> str:
    target = key_name.lower()
    for key in list(pairs):
        if key.lower() == target:
            return pairs.pop(key)
    return ""


def _remittance_lines(transaction: dict[str, Any]) -> list[str]:
    raw = transaction.get("remittance_information") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(line).strip() for line in raw if line]


def _debug_remittance_log(payload: dict[str, Any]) -> None:
    """Append one JSON line to ``data/debug.log`` (best-effort)."""
    try:
        path = paths.DATA_DIR / "debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _structured_remittance_fields(transaction: dict[str, Any]) -> dict[str, str]:
    lines = _remittance_lines(transaction)
    split = _split_bracketed_remittance(lines)
    bank_type = _type(transaction)
    iban_from_lines = _remittance_iban_from_lines(lines)
    tx_id = transaction.get("entry_reference") or transaction.get("id")

    if split is None:
        result = {
            "type": bank_type,
            "iban": iban_from_lines,
            "description": " ".join(lines),
        }
        # _debug_remittance_log(
        #     {
        #         "fn": "_structured_remittance_fields",
        #         "tx_id": tx_id,
        #         "lines": lines,
        #         "split": None,
        #         "bank_type": bank_type,
        #         "iban_from_lines": iban_from_lines,
        #         "result": result,
        #     }
        # )
        return result

    prefix, block = split
    pairs = _parse_brace_key_values(block)
    pairs_raw = dict(pairs)

    tx_type = _pop_brace_key(pairs, "TransactionSubType")
    iban = _pop_brace_key(pairs, "MandateId")
    pairs_after_extract = dict(pairs)
    pairs = {key: value for key, value in pairs.items() if not _is_date_or_datetime(value)}
    pairs_after_date_filter = dict(pairs)

    remainder = _format_brace_key_values(pairs)
    if prefix and remainder:
        description = f"{prefix} {remainder}"
    elif remainder:
        description = remainder
    else:
        description = prefix

    result = {
        "type": tx_type or bank_type,
        "iban": iban or iban_from_lines,
        "description": description,
    }
    _debug_remittance_log(
        {
            "fn": "_structured_remittance_fields",
            "tx_id": tx_id,
            "lines": lines,
            "split": {"prefix": prefix, "block": block},
            "pairs_raw": pairs_raw,
            "tx_type": tx_type,
            "mandate_id": iban,
            "pairs_after_extract": pairs_after_extract,
            "pairs_after_date_filter": pairs_after_date_filter,
            "remainder": remainder,
            "bank_type": bank_type,
            "iban_from_lines": iban_from_lines,
            "description": description,
            "result": result,
        }
    )
    return result


def _naam(transaction: dict[str, Any]) -> str:
    party_key = "creditor" if transaction.get("credit_debit_indicator") == "DBIT" else "debtor"
    party = transaction.get(party_key) or {}
    return str(party.get("name") or "").strip()


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


_HASH_WILDCARD = "[a-z.]*"


def _term_body_pattern(term: str) -> str:
    """Regex body for a keyword; each ``#`` matches zero or more letters or dots (not spaces)."""
    parts: list[str] = []
    for ch in term:
        if ch == "#":
            if not parts or parts[-1] != _HASH_WILDCARD:
                parts.append(_HASH_WILDCARD)
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _letters_only(word: str) -> str:
    return re.sub(r"[^a-z.]", "", word.lower())


def _matches_hash_word(term: str, haystack: str) -> bool:
    body = _term_body_pattern(term)
    pattern = re.compile(body)
    for token in haystack.lower().split():
        for candidate in {token, _letters_only(token)}:
            if candidate and pattern.fullmatch(candidate):
                return True
    return False


def _matches_phrase(term: str, haystack: str) -> bool:
    """Match one phrase (no `` && ``) against the haystack."""
    if "#" not in term:
        return re.search(rf"\b{re.escape(term)}\b", haystack) is not None

    if " " in term:
        body = _term_body_pattern(term)
        return re.search(rf"\b{body}\b", haystack) is not None

    return _matches_hash_word(term, haystack)


def _matches_and_group(group: str, haystack: str) -> bool:
    if _TERM_AND_SEP in group:
        return all(
            _matches_phrase(part.strip(), haystack)
            for part in group.split(_TERM_AND_SEP)
            if part.strip()
        )
    return _matches_phrase(group, haystack)


def _matches_word(field: str, haystack: str) -> bool:
    term = field.lower().strip()
    if not term:
        return False
    return _matches_and_group(term, haystack)


def _haystack_for_categorization(record: dict[str, Any]) -> str:
    """Keyword haystack from processed remittance fields (name + description only)."""
    if _remittance_lines(record):
        remittance = _structured_remittance_fields(record)
        name = _naam(record)
        description = remittance["description"]
    else:
        name = str(record.get("name") or "")
        description = str(record.get("description") or "")
    return f"{name} {description}".lower()


def _categories_file() -> dict[str, Any]:
    if not paths.CATEGORIES_PATH.exists():
        return {}
    data = _read_json(paths.CATEGORIES_PATH)
    return data if isinstance(data, dict) else {}


def type_rules_payload() -> list[dict[str, str]]:
    """Validated ``typerules`` from ``categories.json`` (for API / UI legend)."""
    raw = _categories_file().get("typerules")
    if not isinstance(raw, list):
        return []
    rules: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule_type = str(item.get("type") or "").strip()
        category_name = str(item.get("category") or "").strip()
        if not rule_type or not category_name:
            continue
        if _category_code(category_name) is None:
            continue
        rules.append({"type": rule_type, "category": category_name})
    return rules


def _type_rule_category_map() -> dict[str, int]:
    """Map lowercased bank ``type`` strings to category codes."""
    mapping: dict[str, int] = {}
    for rule in type_rules_payload():
        code = _category_code(rule["category"])
        if code is not None:
            mapping[rule["type"].lower()] = code
    return mapping


def _category_from_type_rules(record: dict[str, Any]) -> int | None:
    tx_type = _transaction_type_for_categorization(record).lower()
    if not tx_type:
        return None
    return _type_rule_category_map().get(tx_type)


def _transaction_type_for_categorization(record: dict[str, Any]) -> str:
    if _remittance_lines(record):
        return _structured_remittance_fields(record)["type"]
    return str(record.get("type") or "")


def _keyword_match_rank(term: str, *, personal: bool) -> tuple[int, int, int]:
    """Sort key for competing keyword hits when no typerule applies (higher wins).

    1. ``&&`` terms (tier 2) beat single-phrase terms (tier 1).
    2. Among single-phrase terms, longer string wins.
    3. Within the same tier (and same length for single-phrase), personal beats general.
    """
    if _TERM_AND_SEP in term:
        return (2, 0, 1 if personal else 0)
    return (1, len(term), 1 if personal else 0)


def _best_keyword_category(
    haystack: str,
    general: dict[str, list[str]],
    personal: dict[str, list[str]],
) -> int | None:
    best_rank = (-1, -1, -1)
    best_code: int | None = None

    for is_personal, group in ((False, general), (True, personal)):
        for name, fields in group.items():
            code = _category_code(name)
            if code is None:
                continue
            for field in fields or []:
                if not field:
                    continue
                term = str(field).lower()
                if not _matches_word(term, haystack):
                    continue
                rank = _keyword_match_rank(term, personal=is_personal)
                if rank > best_rank:
                    best_rank = rank
                    best_code = code
    return best_code


def categorize(
    record: dict[str, Any],
    general: dict[str, list[str]],
    personal: dict[str, list[str]],
) -> int:
    type_match = _category_from_type_rules(record)
    if type_match is not None:
        return type_match

    haystack = _haystack_for_categorization(record)
    keyword_match = _best_keyword_category(haystack, general, personal)
    if keyword_match is not None:
        return keyword_match

    return DEFAULT_CATEGORY


def _account_index(transaction: dict[str, Any]) -> int:
    raw = transaction.get(_ACCOUNT_INDEX_FIELD, 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _categorized_transaction_id(transaction: dict[str, Any]) -> str:
    ref = transaction.get("entry_reference")
    if ref is not None and str(ref).strip():
        return f"{str(ref).strip()}_{_account_index(transaction)}"
    tid = transaction.get("id")
    return str(tid).strip() if tid is not None else ""


def _tx_sort_key(transaction: Any) -> int:
    tid = transaction.get("id") if isinstance(transaction, dict) else None
    text = str(tid) if tid is not None else ""
    if "_" in text:
        base, suffix = text.rsplit("_", 1)
        if suffix.isdigit():
            text = base
    return int(text) if text.isdigit() else -1


def simplify_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    remittance = _structured_remittance_fields(transaction)
    return {
        "id": _categorized_transaction_id(transaction),
        "amount": _amount(transaction),
        "currency": _currency(transaction),
        "type": remittance["type"],
        "name": _naam(transaction),
        "iban": remittance["iban"],
        "description": remittance["description"],
        "date": _booking_date(transaction),
    }


def _canonical_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    record = dict(transaction)
    legacy_iban = record.pop("IBAN", None)
    if legacy_iban is not None and not record.get("iban"):
        record["iban"] = legacy_iban
    record.pop("category_locked", None)
    return record


_MODIFICATION_FIELDS = frozenset({"category", "description"})


def _as_category_code(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _modifications_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modifications = payload.get("modifications")
    if not isinstance(modifications, list):
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in modifications:
        if isinstance(item, dict) and item.get("id") is not None:
            by_id[str(item.get("id"))] = item
    return by_id


def _effective_transaction(
    base: dict[str, Any],
    mods_by_id: dict[str, dict[str, Any]] | None = None,
    *,
    modification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Base transaction with sparse modification overlays (category / description)."""
    effective = _canonical_transaction(base)
    mod = modification
    if mod is None and mods_by_id is not None and base.get("id") is not None:
        mod = mods_by_id.get(str(base.get("id")))
    if not isinstance(mod, dict):
        return effective
    for key in _MODIFICATION_FIELDS:
        if key in mod:
            effective[key] = mod[key]
    return effective


def _values_equal(key: str, left: Any, right: Any) -> bool:
    if key == "category":
        a = _as_category_code(left)
        b = _as_category_code(right)
        if a is not None and b is not None:
            return a == b
    return str(left if left is not None else "") == str(right if right is not None else "")


def _sparse_modification_from_full(
    full: dict[str, Any], base: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Build ``{id, …changed fields}`` from a full or partial modification vs base."""
    transaction_id = str(full.get("id", ""))
    if not transaction_id:
        return None
    patch: dict[str, Any] = {"id": full.get("id")}
    for key in _MODIFICATION_FIELDS:
        if key not in full:
            continue
        value = full[key]
        if key == "category" and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass
        if base is None or not _values_equal(key, value, base.get(key)):
            patch[key] = value
    if len(patch) == 1:
        return None
    return patch


def _migrate_modifications(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize modifications to sparse overlays; drop ``category_locked`` everywhere."""
    bases = {
        str(item.get("id")): _canonical_transaction(item)
        for item in data.get("transactions", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    for item in data.get("transactions", []):
        if isinstance(item, dict):
            item.pop("category_locked", None)

    modifications = data.get("modifications")
    if not isinstance(modifications, list):
        data.pop("modifications", None)
        return data

    sparse: list[dict[str, Any]] = []
    for item in modifications:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        tid = str(item.get("id"))
        base = bases.get(tid)
        patch = _sparse_modification_from_full(item, base)
        if patch is not None:
            sparse.append(patch)

    if sparse:
        data["modifications"] = sparse
    else:
        data.pop("modifications", None)
    return data


def _migrate_categorized_store(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("transactions", "modifications"):
        items = data.get(key)
        if isinstance(items, list):
            data[key] = [
                _canonical_transaction(item) if isinstance(item, dict) else item
                for item in items
            ]
    return _migrate_modifications(data)


def _merge_simplified(existing: dict[str, Any], new_records: list[dict[str, Any]]) -> dict[str, Any]:
    existing_tx = existing.get("transactions")
    existing_tx = existing_tx if isinstance(existing_tx, list) else []
    by_id: dict[Any, dict[str, Any]] = {
        item.get("id"): _canonical_transaction(item)
        for item in existing_tx
        if isinstance(item, dict) and item.get("id") is not None
    }
    for record in new_records:
        record_id = record.get("id")
        if record_id is not None:
            by_id[record_id] = _canonical_transaction(record)
    merged = sorted(by_id.values(), key=_tx_sort_key, reverse=True)

    result = dict(existing)
    result["transactions"] = merged
    return result


_SIMPLIFIED_FIELDS = ("amount", "currency", "type", "name", "iban", "description", "date")


def _fill_transaction_fields(
    record: dict[str, Any], simplified: dict[str, Any]
) -> dict[str, Any]:
    """Copy distilled bank fields onto a stored transaction (does not set category)."""
    filled = dict(record)
    for field in _SIMPLIFIED_FIELDS:
        if field in simplified:
            filled[field] = simplified[field]
    return filled


def _categorize_transactions(
    records: list[dict[str, Any]],
    general: dict[str, list[str]],
    personal: dict[str, list[str]],
    *,
    match_sources: dict[Any, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    categorized: list[dict[str, Any]] = []
    for record in records:
        updated = dict(record)
        updated.pop("category_locked", None)
        source = record
        if match_sources is not None:
            source = match_sources.get(record.get("id"), record)
        updated["category"] = categorize(source, general, personal)
        categorized.append(updated)
    return categorized


def _simplify_and_categorize(
    raw_transactions: list[dict[str, Any]],
    general: dict[str, list[str]],
    personal: dict[str, list[str]],
) -> list[dict[str, Any]]:
    categorized: list[dict[str, Any]] = []
    for transaction in raw_transactions:
        record = simplify_transaction(transaction)
        record["category"] = categorize(transaction, general, personal)
        categorized.append(record)
    return categorized


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
    """Per-category signed totals, honoring sparse modification overlays by id."""
    name_by_code = {
        code: name for name in general_names if (code := _category_code(name)) is not None
    }
    totals: dict[str, int] = {name: 0 for name in general_names}
    mods_by_id = _modifications_by_id(transactions_payload)

    for transaction in transactions_payload.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        effective = _effective_transaction(transaction, mods_by_id)
        code = _as_category_code(effective.get("category"))
        name = name_by_code.get(code, str(code)) if code is not None else str(effective.get("category"))
        totals[name] = totals.get(name, 0) + _amount_to_cents(effective.get("amount"))

    return {name: _amount_str(cents) for name, cents in totals.items()}


def _write_category_totals(merged: dict[str, Any], general: dict[str, list[str]]) -> dict[str, str]:
    totals = build_category_totals(merged, list(general.keys()))
    try:
        from app.core.single_client import current_balance_payload, ensure_consent_credit_card_labels

        ensure_consent_credit_card_labels()

        payload = {
            "categories": totals,
            **current_balance_payload(),
        }
    except Exception:
        payload = {"categories": totals}
    _write_json(paths.CATEGORY_TOTALS_PATH, payload)
    return totals


def refresh_category_totals_balances() -> dict[str, str]:
    """Update balance fields in the category totals file from consent (no recategorization)."""
    data = _load_json_object(paths.CATEGORY_TOTALS_PATH)
    categories = data.get("categories")
    if not isinstance(categories, dict):
        general = _category_map(_read_json(paths.CATEGORIES_PATH))
        merged = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
        categories = build_category_totals(merged, list(general.keys()))
    try:
        from app.core.single_client import current_balance_payload, ensure_consent_credit_card_labels

        ensure_consent_credit_card_labels()
        payload = {"categories": categories, **current_balance_payload()}
    except Exception:
        payload = {"categories": categories}
    _write_json(paths.CATEGORY_TOTALS_PATH, payload)
    return {str(name): str(amount) for name, amount in categories.items()}


def load_category_totals() -> dict[str, str]:
    data = _load_json_object(paths.CATEGORY_TOTALS_PATH)
    categories = data.get("categories")
    if not isinstance(categories, dict):
        return {}
    return {str(name): str(amount) for name, amount in categories.items()}


def _load_raw_transactions() -> list[dict[str, Any]]:
    if not paths.RAW_TRANSACTIONS_PATH.exists():
        return []
    try:
        raw = _read_json(paths.RAW_TRANSACTIONS_PATH)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        items = raw.get("transactions")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _raw_bank_by_id() -> dict[Any, dict[str, Any]]:
    by_id: dict[Any, dict[str, Any]] = {}
    for raw in _load_raw_transactions():
        record_id = _categorized_transaction_id(raw)
        if record_id:
            by_id[record_id] = raw
    return by_id


def _raw_simplified_by_id() -> dict[Any, dict[str, Any]]:
    by_id: dict[Any, dict[str, Any]] = {}
    for raw in _load_raw_transactions():
        record = simplify_transaction(raw)
        record_id = record.get("id")
        if record_id is not None:
            by_id[record_id] = record
    return by_id


def recategorize_transactions() -> dict[str, str]:
    """Re-categorize every base row under ``transactions``.

    Keyword matching uses each row's fields, with a sparse ``description``
    modification applied when present. Calculated categories are always written
    onto ``transactions``. Sparse ``modifications`` are kept (not re-scored);
    a ``category`` overlay that matches the newly calculated value is dropped.
    """
    general = _category_map(_read_json(paths.CATEGORIES_PATH))
    personal = _category_map(_read_json(paths.PERSONAL_CATEGORIES_PATH))
    data = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
    data = _migrate_categorized_store(data)
    mods_by_id = _modifications_by_id(data)

    existing_tx = data.get("transactions")
    existing_tx = existing_tx if isinstance(existing_tx, list) else []
    records = [
        _canonical_transaction(item)
        for item in existing_tx
        if isinstance(item, dict) and item.get("id") is not None
    ]

    match_sources: dict[Any, dict[str, Any]] = {}
    for record in records:
        tid = record.get("id")
        if tid is None:
            continue
        mod = mods_by_id.get(str(tid))
        if isinstance(mod, dict) and "description" in mod:
            match_sources[tid] = _effective_transaction(record, modification=mod)
            match_sources[str(tid)] = match_sources[tid]

    categorized = _categorize_transactions(
        records, general, personal, match_sources=match_sources or None
    )

    # Drop category overlays that now equal the calculated category.
    bases_by_id = {str(item.get("id")): item for item in categorized if item.get("id") is not None}
    pruned_mods: list[dict[str, Any]] = []
    for mod in mods_by_id.values():
        tid = str(mod.get("id"))
        base = bases_by_id.get(tid)
        if base is None:
            continue
        patch = dict(mod)
        if "category" in patch and _values_equal("category", patch.get("category"), base.get("category")):
            patch.pop("category", None)
        if set(patch.keys()) <= {"id"}:
            continue
        pruned_mods.append(patch)

    result = dict(data) if data else {}
    result["transactions"] = sorted(categorized, key=_tx_sort_key, reverse=True)
    if pruned_mods:
        result["modifications"] = pruned_mods
    else:
        result.pop("modifications", None)

    result = _migrate_categorized_store(result)
    _write_json(paths.CATEGORIZED_TRANSACTIONS_PATH, result)
    return _write_category_totals(result, general)


def transactions_for_category(category_name: str) -> list[dict[str, Any]]:
    """Return effective transactions for a category display name (e.g. ``09 Pension``)."""
    from app.category_table_log import log

    code = _category_code(category_name)
    log(
        "filter.start",
        category_name=category_name,
        parsed_code=code,
        store=paths.CATEGORIZED_TRANSACTIONS_PATH,
        store_exists=paths.CATEGORIZED_TRANSACTIONS_PATH.exists(),
    )
    if code is None:
        log("filter.abort", reason="category_name_has_no_numeric_prefix")
        return []

    payload = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
    raw_list = payload.get("transactions")
    raw_count = len(raw_list) if isinstance(raw_list, list) else 0
    mods_by_id = _modifications_by_id(payload)
    mod_count = len(mods_by_id)

    code_counts: dict[int | str, int] = {}
    transactions: list[dict[str, Any]] = []
    for transaction in raw_list if isinstance(raw_list, list) else []:
        if not isinstance(transaction, dict):
            continue
        effective = _effective_transaction(transaction, mods_by_id)
        effective_code = _as_category_code(effective.get("category"))
        bucket: int | str = effective_code if effective_code is not None else repr(effective.get("category"))
        code_counts[bucket] = code_counts.get(bucket, 0) + 1
        if effective_code == code:
            transactions.append(_public_transaction(effective))

    log(
        "filter.done",
        category_name=category_name,
        parsed_code=code,
        raw_transactions=raw_count,
        modifications=mod_count,
        matched=len(transactions),
        code_histogram=dict(sorted(code_counts.items(), key=lambda item: str(item[0]))),
    )
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


def _public_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    return _canonical_transaction(transaction)


def modification_style_ids(payload: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    """Return (description_modified_ids, category_modified_ids) from sparse mods."""
    data = payload if payload is not None else _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
    description_ids: list[str] = []
    category_ids: list[str] = []
    for mod in _modifications_by_id(data).values():
        tid = str(mod.get("id"))
        if "description" in mod:
            description_ids.append(tid)
        if "category" in mod:
            category_ids.append(tid)
    return description_ids, category_ids


def format_transaction_amount(transaction: dict[str, Any]) -> str:
    amount = str(transaction.get("amount", "")).strip()
    currency = str(transaction.get("currency", "")).strip().upper()
    symbol = _CURRENCY_SYMBOLS.get(currency, f"{currency} " if currency else "€")
    return f"{symbol}{amount}"


def terms_for_category(category_name: str) -> list[str]:
    """General + personal keyword terms for a category display name."""
    general = _category_map(_read_json(paths.CATEGORIES_PATH))
    personal = _category_map(_read_json(paths.PERSONAL_CATEGORIES_PATH))
    return [*general.get(category_name, []), *personal.get(category_name, [])]


def category_name_for_column_key(column_key: Any) -> str | None:
    for name in category_names():
        if _category_column_key(name) == column_key:
            return name
    return None


def _normalize_term(term: str) -> str:
    return term.strip().lower()


def _cleaned_terms(terms: list[str]) -> list[str]:
    return [_normalize_term(term) for term in terms if isinstance(term, str) and term.strip()]


def _save_general_category_terms(category_name: str, terms: list[str]) -> None:
    data = _read_json(paths.CATEGORIES_PATH)
    categories = data.setdefault("categories", {})
    categories[category_name] = _cleaned_terms(terms)
    _write_json(paths.CATEGORIES_PATH, data)


def _save_personal_category_terms(category_name: str, terms: list[str]) -> None:
    data = _load_json_object(paths.PERSONAL_CATEGORIES_PATH)
    cleaned = _cleaned_terms(terms)
    if cleaned:
        data[category_name] = cleaned
    else:
        data.pop(category_name, None)
    _write_json(paths.PERSONAL_CATEGORIES_PATH, data)


def append_category_term(
    category_name: str,
    term: str,
    *,
    group: str,
    person: str,
) -> list[str]:
    """Append one keyword to general (categories.json) or personal (personal_categories.json)."""
    cleaned = _normalize_term(term)
    if not cleaned:
        raise ValueError("term must not be empty")
    if category_name not in category_names():
        raise ValueError(f"Unknown category: {category_name!r}")
    code = _category_code(category_name)
    if code is None or code == DEFAULT_CATEGORY:
        raise ValueError(f"Cannot add terms to category {category_name!r}")

    if group == "general":
        general = _category_map(_read_json(paths.CATEGORIES_PATH))
        terms = list(general.get(category_name, []))
        if cleaned not in _cleaned_terms(terms):
            terms.append(cleaned)
        _save_general_category_terms(category_name, terms)
    elif group == person:
        personal = _category_map(_load_json_object(paths.PERSONAL_CATEGORIES_PATH))
        terms = list(personal.get(category_name, []))
        if cleaned not in _cleaned_terms(terms):
            terms.append(cleaned)
        _save_personal_category_terms(category_name, terms)
    else:
        raise ValueError(f"Unknown settings group: {group!r}")
    return terms_for_category(category_name)


def add_category_term(category_name: str, term: str) -> list[str]:
    """Append a term to the personal keyword list for a category."""
    cleaned_term = _normalize_term(term)
    if not cleaned_term:
        return terms_for_category(category_name)
    if cleaned_term in _cleaned_terms(terms_for_category(category_name)):
        return terms_for_category(category_name)

    personal = _category_map(_load_json_object(paths.PERSONAL_CATEGORIES_PATH))
    personal_terms = list(personal.get(category_name, []))
    personal_terms.append(cleaned_term)
    _save_personal_category_terms(category_name, personal_terms)
    return terms_for_category(category_name)


def remove_category_term(category_name: str, term: str) -> list[str]:
    """Remove a term from personal keywords, otherwise from general keywords."""
    needle = _normalize_term(term)
    personal = _category_map(_load_json_object(paths.PERSONAL_CATEGORIES_PATH))
    general = _category_map(_read_json(paths.CATEGORIES_PATH))
    personal_terms = list(personal.get(category_name, []))
    general_terms = list(general.get(category_name, []))

    if any(_normalize_term(existing) == needle for existing in personal_terms):
        _save_personal_category_terms(
            category_name,
            [existing for existing in personal_terms if _normalize_term(existing) != needle],
        )
    elif any(_normalize_term(existing) == needle for existing in general_terms):
        _save_general_category_terms(
            category_name,
            [existing for existing in general_terms if _normalize_term(existing) != needle],
        )
    return terms_for_category(category_name)


def category_terms_table(extra_rows: int = 0) -> tuple[list[tuple[str, str]], list[list[str]]]:
    """Column (name, key) pairs and term rows for the keywords overview table."""
    general = _category_map(_read_json(paths.CATEGORIES_PATH))
    personal = _category_map(_read_json(paths.PERSONAL_CATEGORIES_PATH))
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
    cleaned_value = _normalize_term(value) if value.strip() else ""

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
    general = _category_map(_categories_file())
    return list(general.keys())


def category_code_set() -> frozenset[int]:
    """Category numbers defined as keys in ``categories.json``."""
    return frozenset(
        code for name in category_names() if (code := _category_code(name)) is not None
    )


def remainder_category_name() -> str:
    """Display name of the default / unmatched category (``DEFAULT_CATEGORY``)."""
    for name in category_names():
        if _category_code(name) == DEFAULT_CATEGORY:
            return name
    raise ValueError(
        f"No category with code {DEFAULT_CATEGORY} found in {paths.CATEGORIES_PATH.name}"
    )


def _validate_category_code(code: Any) -> int:
    try:
        numeric = int(code)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid category code: {code!r}") from exc
    if numeric not in category_code_set():
        known = ", ".join(str(c) for c in sorted(category_code_set()))
        raise ValueError(f"Unknown category code {numeric}; known codes: {known}")
    return numeric


def _source_transaction_by_id(
    transaction_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return the effective transaction (base + sparse modification) for ``id``."""
    data = payload if payload is not None else _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
    mods_by_id = _modifications_by_id(data)
    for transaction in data.get("transactions", []):
        if isinstance(transaction, dict) and str(transaction.get("id", "")) == transaction_id:
            return _effective_transaction(transaction, mods_by_id)
    return None


def record_modification(transaction: dict[str, Any]) -> dict[str, Any]:
    """Store a sparse modification: ``id`` plus changed ``category`` / ``description`` only.

    ``transaction`` is the edited effective row from the UI. Diffed against the
    base row in ``transactions``. Replaces any existing modification for the same id.
    """
    data = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
    if not data:
        data = {"transactions": []}

    submitted = _canonical_transaction(transaction)
    transaction_id = str(submitted.get("id", ""))
    if not transaction_id:
        raise ValueError("Transaction id is required for a modification")

    if "category" in submitted and submitted.get("category") is not None:
        submitted["category"] = _validate_category_code(submitted.get("category"))

    base: dict[str, Any] | None = None
    for item in data.get("transactions", []):
        if isinstance(item, dict) and str(item.get("id", "")) == transaction_id:
            base = _canonical_transaction(item)
            break
    if base is None:
        raise ValueError(f"Transaction not found: {transaction_id}")

    # Only consider category / description vs the base row.
    candidate = {"id": submitted.get("id")}
    for key in _MODIFICATION_FIELDS:
        if key in submitted:
            candidate[key] = submitted[key]

    patch = _sparse_modification_from_full(candidate, base)

    modifications = [
        item
        for item in (data.get("modifications") if isinstance(data.get("modifications"), list) else [])
        if isinstance(item, dict) and str(item.get("id", "")) != transaction_id
    ]
    if patch is not None:
        modifications.append(patch)

    if modifications:
        data["modifications"] = modifications
    else:
        data.pop("modifications", None)

    data = _migrate_categorized_store(data)
    _write_json(paths.CATEGORIZED_TRANSACTIONS_PATH, data)
    general = _category_map(_read_json(paths.CATEGORIES_PATH))
    _write_category_totals(data, general)
    return _public_transaction(_effective_transaction(base, _modifications_by_id(data)))


def record_category_change(transaction: dict[str, Any], category_name: str) -> dict[str, Any]:
    """Record a sparse category override for this transaction id."""
    code = _category_code(category_name)
    if code is None:
        raise ValueError(f"Unknown category: {category_name!r}")

    transaction_id = str(transaction.get("id", ""))
    data = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
    effective = _source_transaction_by_id(transaction_id, data) or dict(transaction)
    modified = dict(effective)
    modified["category"] = code
    return record_modification(modified)


def process_transactions(raw_transactions: list[dict[str, Any]], new_year: bool) -> dict[str, str]:
    """Distill raw bank JSON into ``categorized_transactions.json``.

    When *new_year* is false, append any transaction whose ``id`` is not already
    present and keep existing ``modifications``. When *new_year* is true,
    replace **this year's** file with only this fetch (no merge, no
    modifications). That is a same-year redo / PEM bootstrap; it does not
    open a new calendar year and must not wipe previous year folders.
    """
    general = _category_map(_read_json(paths.CATEGORIES_PATH))
    personal = _category_map(_read_json(paths.PERSONAL_CATEGORIES_PATH))
    new_records = _simplify_and_categorize(raw_transactions, general, personal)

    if new_year:
        merged = _merge_simplified({"transactions": []}, new_records)
    else:
        existing = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
        if not isinstance(existing.get("transactions"), list):
            existing = {"transactions": []}
        merged = _merge_simplified(existing, new_records)
        modifications = existing.get("modifications")
        if isinstance(modifications, list):
            merged["modifications"] = modifications

    merged = _migrate_categorized_store(merged)
    _write_json(paths.RAW_TRANSACTIONS_PATH, raw_transactions)
    _write_json(paths.CATEGORIZED_TRANSACTIONS_PATH, merged)
    return _write_category_totals(merged, general)
