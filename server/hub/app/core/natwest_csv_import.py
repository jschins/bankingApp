"""Convert NatWest bank CSV exports into hub JSON (same outputs as Excel import)."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from app.core.excel_import import (
    _latest_iso_date,
    _new_folder_files,
    _public_transaction,
    _recorded_files,
    amount_str,
    build_category_totals,
    category_code,
    category_name_map,
    cell_amount_cents,
    format_amount,
    transaction_id,
)

NATWEST_HEADERS = frozenset(
    {"date", "type", "description", "value", "balance", "account name", "account number"}
)


def _norm_header(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def read_csv_rows(text: str) -> list[dict[str, str]]:
    """Parse NatWest CSV text into row dicts keyed by normalized header names."""
    if not text.strip():
        return []
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        return []
    field_map = {_norm_header(name): name for name in reader.fieldnames if name}
    missing = NATWEST_HEADERS - set(field_map)
    if missing:
        raise ValueError(
            f"Not a NatWest CSV — missing columns: {', '.join(sorted(missing))}"
        )
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(reader, start=2):
        if not isinstance(raw, dict):
            continue
        row = {
            key: str(raw.get(original) or "").strip()
            for key, original in field_map.items()
        }
        if any(row.values()):
            row["_row"] = str(index)
            rows.append(row)
    return rows


def read_csv_rows_bytes(data: bytes) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return read_csv_rows(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV is not valid UTF-8 or Windows-1252 text")


def is_natwest_csv_bytes(data: bytes) -> bool:
    try:
        read_csv_rows_bytes(data)
        return True
    except (ValueError, OSError, UnicodeDecodeError):
        return False


def parse_natwest_date(value: str) -> str | None:
    """NatWest ``14 Aug 2026`` → ``14-08-2026`` (app date format)."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%d-%m-%Y")
        except ValueError:
            continue
    return None


def first_entry_year(rows: list[dict[str, str]]) -> str | None:
    """Calendar year of the first dated row (export is newest-first)."""
    for row in rows:
        parsed = parse_natwest_date(row.get("date", ""))
        if not parsed:
            continue
        parts = parsed.split("-")
        if len(parts) == 3 and parts[2].isdigit():
            return parts[2]
        return None
    return None


def first_entry_year_from_csv_bytes(data: bytes) -> str | None:
    return first_entry_year(read_csv_rows_bytes(data))


def _app_date_key(value: str) -> tuple[int, int, int]:
    parts = str(value or "").split("-")
    if len(parts) != 3:
        return (0, 0, 0)
    try:
        return (int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return (0, 0, 0)


def _latest_balance_cents(rows: list[dict[str, str]]) -> int | None:
    """Balance after the most recent dated row (each row carries its own Balance)."""
    latest: tuple[tuple[int, int, int], int] | None = None
    for row in rows:
        date = parse_natwest_date(row.get("date", ""))
        if not date:
            continue
        balance = cell_amount_cents(row.get("balance"))
        key = _app_date_key(date)
        if latest is None or key > latest[0]:
            latest = (key, balance)
    return latest[1] if latest else None


def _account_holder_from_rows(rows: list[dict[str, str]]) -> tuple[str, str]:
    for row in rows:
        name = str(row.get("account name") or "").strip()
        number = str(row.get("account number") or "").strip().replace("-", "")
        if name or number:
            return name or "onbekend", number or "onbekend"
    return "onbekend", "onbekend"


def _account_balances_from_csv(
    existing_totals: dict[str, Any],
    *,
    folder_files: list[str],
    latest_balance_cents: int | None,
    holder: str,
    account_number: str,
) -> list[dict[str, Any]]:
    accounts = existing_totals.get("account_balances")
    accounts = [dict(item) for item in accounts if isinstance(item, dict)] if isinstance(accounts, list) else []
    if not accounts:
        accounts = [{"iban": account_number, "name": holder, "currency": "GBP", "balance": "0.00", "files": []}]
    account = dict(accounts[0])
    if holder and holder != "onbekend":
        account["name"] = holder
    if account_number and account_number != "onbekend":
        account["iban"] = account_number
    account.setdefault("currency", "GBP")
    account["files"] = list(folder_files)
    if latest_balance_cents is not None:
        account["balance"] = amount_str(latest_balance_cents)
    accounts[0] = account
    return accounts

TX_TYPE_DEFAULT = "Natwest-csv"


def rows_to_transactions(
    rows: list[dict[str, str]],
    *,
    source: str,
    registered: set[int],
    tx_type: str,
) -> list[dict[str, Any]]:
    """Convert every dated row; do not filter by calendar year."""
    transactions: list[dict[str, Any]] = []
    account_number = ""
    currency = "GBP"
    for row in rows:
        date = parse_natwest_date(row.get("date", ""))
        if date is None:
            continue
        if not account_number:
            account_number = str(row.get("account number") or "").strip()
        description = str(row.get("description") or "").strip()
        type_code = str(row.get("type") or "").strip()
        name = f"{type_code}: {description}".strip(": ") if type_code else description
        try:
            row_index = int(row.get("_row") or 0)
        except ValueError:
            row_index = 0
        amount_cents = cell_amount_cents(row.get("value"))
        if amount_cents == 0 and not description:
            continue
        amount = format_amount(amount_cents / 100.0)
        transactions.append(
            {
                "id": transaction_id(source, row_index, "0"),
                "amount": amount,
                "currency": currency,
                "type": tx_type,
                "name": name,
                "iban": account_number.replace("-", ""),
                "description": description,
                "date": date,
                "category": category_code(None, registered),
                "_source_file": Path(source).name,
            }
        )
    return transactions


def list_csv_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.glob("*.csv")
        if path.is_file() and not path.name.startswith(".")
    )


def convert_csv_files(
    folder: Path,
    *,
    categories_path: Path,
    tx_type: str = TX_TYPE_DEFAULT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    files = list_csv_files(folder)
    if not files:
        raise FileNotFoundError(f"No .csv files found in {folder}")

    name_by_code = category_name_map(categories_path)
    registered = set(name_by_code)
    transactions: list[dict[str, Any]] = []
    file_errors: list[str] = []
    imported_names: list[str] = []
    all_rows: list[dict[str, str]] = []
    for path in files:
        try:
            rows = read_csv_rows(path.read_text(encoding="utf-8-sig"))
            parsed = rows_to_transactions(
                rows, source=path.name, registered=registered, tx_type=tx_type
            )
        except (OSError, ValueError) as exc:
            file_errors.append(f"{path.name}: {exc}")
            continue
        imported_names.append(path.name)
        all_rows.extend(rows)
        transactions.extend(parsed)

    if not imported_names:
        detail = "; ".join(file_errors) if file_errors else f"No .csv files found in {folder}"
        raise ValueError(detail)

    existing_totals: dict[str, Any] = {}
    totals_path = folder / "category_totals.json"
    if totals_path.is_file():
        try:
            loaded = json.loads(totals_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_totals = loaded
        except (OSError, json.JSONDecodeError):
            existing_totals = {}

    folder_names = imported_names
    account = None
    accounts = existing_totals.get("account_balances")
    if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict):
        account = accounts[0]
    recorded = _recorded_files(account)
    new_files = _new_folder_files(folder_names, recorded)
    holder, account_number = _account_holder_from_rows(all_rows)
    latest_balance = _latest_balance_cents(all_rows)

    categorized = {
        "transactions": [_public_transaction(item) for item in transactions],
        "modifications": [],
    }
    totals = {
        "categories": build_category_totals(transactions, name_by_code),
        "account_balances": _account_balances_from_csv(
            existing_totals,
            folder_files=folder_names,
            latest_balance_cents=latest_balance,
            holder=holder,
            account_number=account_number,
        ),
    }
    info = {
        "transaction_count": len(transactions),
        "files": folder_names,
        "new_files": new_files,
        "balance_updated": bool(new_files) and latest_balance is not None,
        "balance": totals["account_balances"][0].get("balance"),
        "file_errors": file_errors,
        "last_date": _latest_iso_date(transactions),
    }
    return categorized, totals, info


def write_outputs(
    data: Path,
    *,
    categories_path: Path,
    tx_type: str = TX_TYPE_DEFAULT,
) -> tuple[Path, Path, dict[str, Any]]:
    folder = data
    folder.mkdir(parents=True, exist_ok=True)
    categorized, totals, info = convert_csv_files(
        folder, categories_path=categories_path, tx_type=tx_type
    )
    categorized_path = folder / "categorized_transactions.json"
    totals_path = folder / "category_totals.json"
    categorized_path.write_text(
        json.dumps(categorized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    totals_path.write_text(json.dumps(totals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return categorized_path, totals_path, info


def import_person_value_balance_csv(
    *, data_dir: Path, categories_path: Path, tx_type: str = TX_TYPE_DEFAULT
) -> dict[str, Any]:
    """Hub upload / refresh entry: rewrite JSON from ``*.csv`` (NatWest/RBS layout)."""
    _categorized_path, _totals_path, info = write_outputs(
        data_dir, categories_path=categories_path, tx_type=tx_type
    )
    return info


def import_person_natwest_csv(*, data_dir: Path, categories_path: Path) -> dict[str, Any]:
    """Back-compat alias."""
    return import_person_value_balance_csv(
        data_dir=data_dir, categories_path=categories_path, tx_type="Natwest-csv"
    )
