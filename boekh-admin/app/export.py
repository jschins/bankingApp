"""Build simplified per-person transaction files from the raw bank JSON.

The storage server delivers verbose bank-API transactions
(``storage/<person>/transactions*.json``). This module distills each one into a
compact record and writes ``storage/<person>/<person>_transactions.json``:

    {
      "transactions": [
        {
          "id": "010305213421590480000000",
          "amount": "+2521.80",
          "currency": "EUR",
          "type": "Overschrijving",
          "Naam": "Knowledge Systems Consulting B.V",
          "IBAN": "NL35RABO0110341678",
          "Omschrijving": "loon juli 2026",
          "booking_date": "24-06-2026"
        }
      ]
    }
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Project root (parent of the ``app`` package), so paths resolve regardless of
# the working directory the server was started from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GENERAL_CATEGORIES = _PROJECT_ROOT / "categories" / "bank_categories.json"

# Fallback category for transactions that match no keyword (bankingApp-editor's
# "18 Overige uitgaven").
DEFAULT_CATEGORY = 18

# Cash withdrawals are forced to "08 Naar kas" by transaction type, copying
# bankingApp-editor's rule (it keyed off type == "geldautomaat").
CASH_CATEGORY = 8
CASH_TYPE = "geldautomaat"

# Fields a keyword may match against, copying bankingApp-editor's caption/description
# haystack but using this data's equivalent fields.
_HAYSTACK_FIELDS = ("name", "description")


def _amount(transaction: dict[str, Any]) -> str:
    """``transaction_amount.amount`` signed by ``credit_debit_indicator``.

    CRDT (incoming) -> ``+``, DBIT (outgoing) -> ``-``.
    """
    amount = str((transaction.get("transaction_amount") or {}).get("amount", "")).strip()
    sign = "+" if transaction.get("credit_debit_indicator") == "CRDT" else "-"
    return f"{sign}{amount}" if amount else ""


def _currency(transaction: dict[str, Any]) -> str:
    return str((transaction.get("transaction_amount") or {}).get("currency", "")).strip()


def _type(transaction: dict[str, Any]) -> str:
    return str((transaction.get("bank_transaction_code") or {}).get("description") or "").strip()


def _naam(transaction: dict[str, Any]) -> str:
    """Counterparty name: the ``creditor`` for outgoing (DBIT) payments and the
    ``debtor`` for incoming (CRDT) ones."""
    party_key = "creditor" if transaction.get("credit_debit_indicator") == "DBIT" else "debtor"
    party = transaction.get(party_key) or {}
    return str(party.get("name") or "").strip()


def _omschrijving(transaction: dict[str, Any]) -> str:
    """All ``remittance_information`` lines concatenated into one string."""
    lines = transaction.get("remittance_information") or []
    return " ".join(str(line).strip() for line in lines if line)


def _iban(transaction: dict[str, Any]) -> str:
    """The ``IBAN:`` line value from ``remittance_information``, if present."""
    for line in transaction.get("remittance_information") or []:
        if isinstance(line, str) and ":" in line:
            prefix, value = line.split(":", 1)
            if prefix.strip() == "IBAN":
                return value.strip()
    return ""


def _booking_date(transaction: dict[str, Any]) -> str:
    """``"2026-06-24"`` (ISO) -> ``"24-06-2026"`` (day-first)."""
    raw = str(transaction.get("booking_date") or "").strip()
    parts = raw.split("-")
    if len(parts) == 3:
        year, month, day = parts
        return f"{day}-{month}-{year}"
    return raw


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object from ``path``, or ``{}`` if absent/invalid."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _category_map(path: Path) -> dict[str, list[str]]:
    """Return the ``name -> keyword list`` mapping from a category file.

    Handles both the general file's nested shape (categories under a
    ``"categories"`` key, alongside e.g. ``"abbreviations"``) and the flat
    personal files (``<short>_categories.json``).
    """
    data = _load_json(Path(path))
    nested = data.get("categories")
    return nested if isinstance(nested, dict) else data


def general_category_names(
    path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> list[str]:
    """The category names defined in the general ``bank_categories.json``."""
    return list(_category_map(Path(path)).keys())


def general_abbreviations(
    path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> dict[str, str]:
    """The transaction-type abbreviations from the general categories file."""
    abbreviations = _load_json(Path(path)).get("abbreviations")
    return abbreviations if isinstance(abbreviations, dict) else {}


def general_widths(
    path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> dict[str, float]:
    """P-table column widths (fractions of panel width) from the categories file."""
    widths = _load_json(Path(path)).get("widths")
    return widths if isinstance(widths, dict) else {}


def _amount_to_cents(value: Any) -> int:
    """Parse a signed euro amount string (``"+2521.80"``) into integer cents."""
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return round(float(text) * 100)
    except ValueError:
        return 0


def _amount_str(cents: int) -> str:
    """Render integer cents as a euro string with two decimals (``25.00``)."""
    return f"{cents / 100:.2f}"


def build_consolidation(
    person_dir: Path, general_names: list[str]
) -> dict[str, int]:
    """Per-category signed totals (in cents) for one person.

    Mirrors bankingApp-editor: every general category starts at 0, then each
    transaction's signed amount is added to its category's running total. The
    transaction's integer ``category`` code is mapped back to the full category
    name via the leading two-digit code.
    """
    name_by_code = {
        code: name for name in general_names if (code := _category_code(name)) is not None
    }
    totals: dict[str, int] = {name: 0 for name in general_names}

    tx_path = person_dir / f"{person_dir.name}_transactions.json"
    try:
        payload = json.loads(tx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return totals

    # A modified transaction (matched by id) overrides the original, so its
    # corrected category/amount is what counts towards the totals.
    modifications = payload.get("modifications")
    mods_by_id = {
        m.get("id"): m
        for m in (modifications if isinstance(modifications, list) else [])
        if isinstance(m, dict)
    }

    for transaction in payload.get("transactions", []):
        effective = mods_by_id.get(transaction.get("id"), transaction)
        code = effective.get("category")
        name = name_by_code.get(code, str(code))
        totals[name] = totals.get(name, 0) + _amount_to_cents(effective.get("amount"))
    return totals


def _person_dirs_with_transactions(storage_dir: Path) -> list[Path]:
    return [
        person_dir
        for person_dir in sorted(p for p in storage_dir.iterdir() if p.is_dir())
        if (person_dir / f"{person_dir.name}_transactions.json").is_file()
    ]


def build_h_table(
    storage_dir: Path | str = _PROJECT_ROOT / "storage",
    general_categories_path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> dict[str, Any]:
    """Build the consolidation (H) table, exactly like bankingApp-editor.

    - ``headers``: ``"Category"`` followed by every person's short.
    - ``rows``: one per category (general categories unioned with any extra
      ones appearing in the data, sorted by their zero-padded code), holding
      that category's name and each short's signed total as a euro string.
    """
    storage_dir = Path(storage_dir)
    general_names = general_category_names(general_categories_path)

    person_dirs = _person_dirs_with_transactions(storage_dir)
    shorts = [p.name for p in person_dirs]
    consolidations = {
        p.name: build_consolidation(p, general_names) for p in person_dirs
    }

    names = set(general_names)
    for totals in consolidations.values():
        names.update(totals)
    categories = sorted(names)

    headers = ["Categorie", *shorts]
    rows = [
        [category, *(_amount_str(consolidations[s].get(category, 0)) for s in shorts)]
        for category in categories
    ]
    return {
        "shorts": shorts,
        "categories": categories,
        "headers": headers,
        "rows": rows,
    }


def build_settings(
    storage_dir: Path | str = _PROJECT_ROOT / "storage",
    general_categories_path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> dict[str, Any]:
    """Data for the S(ettings) table: the keyword lists per category.

    - ``categories``: the general category names (rows).
    - ``general``: ``name -> keyword list`` from the general file (Algemeen).
    - ``personal``: ``short -> {name -> keyword list}`` from each person's
      ``<short>_categories.json``.
    """
    storage_dir = Path(storage_dir)
    general = _category_map(Path(general_categories_path))

    person_dirs = _person_dirs_with_transactions(storage_dir)
    shorts = [p.name for p in person_dirs]
    personal = {
        p.name: _category_map(p / f"{p.name}_categories.json") for p in person_dirs
    }

    return {
        "categories": list(general.keys()),
        "shorts": shorts,
        "general": general,
        "personal": personal,
    }


def keywords_for_category(
    short: str,
    code: int,
    storage_dir: Path | str = _PROJECT_ROOT / "storage",
    general_categories_path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> list[str]:
    """Keywords (general + this person's) for the category with the given code."""
    general = _category_map(Path(general_categories_path))
    personal = _category_map(Path(storage_dir) / short / f"{short}_categories.json")

    name = next((n for n in general if _category_code(n) == code), None)
    if name is None:
        return []
    return [*general.get(name, []), *personal.get(name, [])]


def record_modification(
    short: str,
    transaction: dict[str, Any],
    storage_dir: Path | str = _PROJECT_ROOT / "storage",
) -> dict[str, Any]:
    """Store a full modified transaction under ``modifications`` in the file.

    Entries are keyed by ``id``: a later edit of the same transaction replaces
    the earlier entry rather than appending a duplicate.
    """
    path = Path(storage_dir) / short / f"{short}_transactions.json"
    data = _load_json(path)
    modifications = data.get("modifications")
    if not isinstance(modifications, list):
        modifications = []

    tid = transaction.get("id")
    for index, existing in enumerate(modifications):
        if isinstance(existing, dict) and existing.get("id") == tid:
            modifications[index] = transaction
            break
    else:
        modifications.append(transaction)

    data["modifications"] = modifications
    _write_json(path, data)
    return transaction


def update_category_terms(
    group: str,
    category: str,
    terms: list[str],
    storage_dir: Path | str = _PROJECT_ROOT / "storage",
    general_categories_path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> list[str]:
    """Persist a category's keyword list for one group, returning the saved list.

    ``group`` is ``"general"`` (the Algemeen list, stored under ``categories``
    in ``bank_categories.json``) or a person's short (stored flat in
    ``<short>_categories.json``). Terms are trimmed and blanks dropped, so
    clearing a term deletes it. For a person, an empty list removes the
    category key entirely.
    """
    cleaned = [t.strip() for t in terms if isinstance(t, str) and t.strip()]

    if group == "general":
        path = Path(general_categories_path)
        data = _load_json(path)
        data.setdefault("categories", {})[category] = cleaned
        _write_json(path, data)
    else:
        path = Path(storage_dir) / group / f"{group}_categories.json"
        data = _load_json(path)
        if cleaned:
            data[category] = cleaned
        else:
            data.pop(category, None)
        _write_json(path, data)

    return cleaned


def _category_code(name: str) -> int | None:
    """Leading two-digit code of a category name, e.g. ``"20 Werk"`` -> ``20``."""
    try:
        return int(str(name)[:2])
    except ValueError:
        return None


def categorize(
    record: dict[str, Any], *category_groups: dict[str, list[str]]
) -> int:
    """Deduce the integer category for a simplified record.

    Copies bankingApp-editor's systematics: cash withdrawals (``type`` ==
    ``geldautomaat``) start as ``08``; then every keyword from each category
    group is matched against the ``Naam``/``Omschrijving`` haystack, with the
    last match winning; unmatched records fall back to ``18``.
    """
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


def _matches_word(field: str, haystack: str) -> bool:
    """True if ``field`` occurs in ``haystack`` on full word boundaries.

    So ``"ns"`` matches ``"ns groep"`` but not ``"transaction"``.
    """
    return re.search(rf"\b{re.escape(field)}\b", haystack) is not None


def simplify_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    """Distill one raw bank transaction into the compact record shape."""
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


def simplify_transactions(
    raw: list[dict[str, Any]], *category_groups: dict[str, list[str]]
) -> dict[str, Any]:
    records = []
    for transaction in raw:
        record = simplify_transaction(transaction)
        record["category"] = categorize(record, *category_groups)
        records.append(record)
    return {"transactions": records}


# Raw bank files delivered by the storage server, named ``<kind>_<uid>.json``.
# These are distilled into ``<person>_transactions.json`` and then removed.
_RAW_SOURCE_PREFIXES = ("transactions_", "balances_", "details_")


def _find_raw_transactions_files(person_dir: Path) -> list[Path]:
    """All raw bank transactions files in a person's folder (oldest uid first).

    Matches ``transactions*.json`` (the js/as files differ only by an
    underscore and uid) while ignoring our own ``*_transactions.json`` output.
    A re-authorization mints a new account uid, so several may be present; we
    import them all rather than guessing which one is current.
    """
    return [
        path
        for path in sorted(person_dir.glob("transactions*.json"))
        if not path.name.endswith("_transactions.json")
    ]


def _find_raw_transactions_file(person_dir: Path) -> Path | None:
    """First raw transactions file, or ``None`` (used to detect a person dir)."""
    files = _find_raw_transactions_files(person_dir)
    return files[0] if files else None


def _raw_source_files(person_dir: Path) -> list[Path]:
    """Raw bank files to delete after distilling (transactions/balances/details).

    Excludes our own ``<person>_transactions.json`` output and non-raw files
    such as ``<person>_categories.json`` and ``consent.json``.
    """
    return [
        path
        for path in sorted(person_dir.glob("*.json"))
        if not path.name.endswith("_transactions.json")
        and path.name.startswith(_RAW_SOURCE_PREFIXES)
    ]


def _tx_sort_key(transaction: Any) -> int:
    """Descending numeric ``id`` order; missing/non-numeric ids sort last."""
    tid = transaction.get("id") if isinstance(transaction, dict) else None
    text = str(tid) if tid is not None else ""
    return int(text) if text.isdigit() else -1


def _merge_simplified(
    existing: dict[str, Any], new_records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge freshly distilled ``new_records`` into the ``existing`` file.

    Transactions already present (matched by ``id``) are kept untouched; new
    ids are appended; the list is sorted newest-first. Other keys of the
    existing file (notably ``modifications``) are carried over by the caller.
    """
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


def build_person_transactions(
    person_dir: Path,
    general_categories_path: Path | str = DEFAULT_GENERAL_CATEGORIES,
    delete_sources: bool = True,
) -> Path | None:
    """Distill a person's raw bank files into ``<person>_transactions.json``.

    Imports **all** raw ``transactions*.json`` files, simplifies and categorises
    each record, and *merges* them into the existing simplified file (new ids
    appended; existing transactions and the ``modifications`` list kept). When
    ``delete_sources`` is true (the default) the raw source files
    (``transactions_*``, ``balances_*``, ``details_*``) are deleted afterwards —
    the simplified file is the durable record. Pass ``delete_sources=False`` to
    keep them on disk (debugging). Returns the output path, or ``None`` if no
    raw file is present.
    """
    person_dir = Path(person_dir)
    sources = _find_raw_transactions_files(person_dir)
    if not sources:
        return None

    general = _category_map(Path(general_categories_path))
    personal = _category_map(person_dir / f"{person_dir.name}_categories.json")

    new_records: list[dict[str, Any]] = []
    for source in sources:
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        new_records.extend(simplify_transactions(raw, general, personal)["transactions"])

    out_path = person_dir / f"{person_dir.name}_transactions.json"
    existing = _load_json(out_path)
    merged = _merge_simplified(existing, new_records)
    if isinstance(existing.get("modifications"), list):
        merged["modifications"] = existing["modifications"]
    _write_json(out_path, merged)

    # The raw bank files have been distilled into the simplified file; remove
    # them so they do not accumulate (unless kept for debugging).
    if delete_sources:
        for path in _raw_source_files(person_dir):
            try:
                path.unlink()
            except OSError:
                pass
    return out_path


def build_all(
    storage_dir: Path | str = "./storage", delete_sources: bool = True
) -> list[Path]:
    """Distill raw bank files into the simplified file for every person folder."""
    storage_dir = Path(storage_dir)
    written: list[Path] = []
    for person_dir in sorted(p for p in storage_dir.iterdir() if p.is_dir()):
        out_path = build_person_transactions(person_dir, delete_sources=delete_sources)
        if out_path is not None:
            written.append(out_path)
    return written


def recategorize_person(
    person_dir: Path,
    general_categories_path: Path | str = DEFAULT_GENERAL_CATEGORIES,
) -> Path | None:
    """Re-apply categorisation to an already-distilled ``<person>_transactions``.

    Works off the simplified file alone (the raw bank files are gone after
    import), so editing keyword lists and recalculating still updates every
    transaction's ``category``. User ``modifications`` are left untouched.
    Returns the output path, or ``None`` if there is no simplified file.
    """
    person_dir = Path(person_dir)
    out_path = person_dir / f"{person_dir.name}_transactions.json"
    data = _load_json(out_path)
    transactions = data.get("transactions")
    if not isinstance(transactions, list):
        return None

    general = _category_map(Path(general_categories_path))
    personal = _category_map(person_dir / f"{person_dir.name}_categories.json")
    for record in transactions:
        if isinstance(record, dict):
            record["category"] = categorize(record, general, personal)

    data["transactions"] = transactions
    _write_json(out_path, data)
    return out_path


def recategorize_all(storage_dir: Path | str = "./storage") -> list[Path]:
    """Re-categorise the distilled transactions file for every person folder."""
    storage_dir = Path(storage_dir)
    written: list[Path] = []
    for person_dir in sorted(p for p in storage_dir.iterdir() if p.is_dir()):
        out_path = recategorize_person(person_dir)
        if out_path is not None:
            written.append(out_path)
    return written


if __name__ == "__main__":
    import sys

    target_path = Path(sys.argv[1] if len(sys.argv) > 1 else "./storage")
    # A folder that directly holds a raw transactions file is one person;
    # otherwise it is the storage root holding many person folders.
    if _find_raw_transactions_file(target_path) is not None:
        result = build_person_transactions(target_path)
        results = [result] if result else []
    else:
        results = build_all(target_path)
    for path in results:
        print(f"wrote {path}")
