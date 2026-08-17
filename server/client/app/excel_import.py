"""Convert no-secret person Excel files into hub JSON (both spreadsheet layouts)."""

from __future__ import annotations

import json
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EXCEL_EPOCH = datetime(1899, 12, 30)
DEFAULT_CATEGORY = 18

DATE_ALIASES = ("datum",)
DESC_ALIASES = ("beschrijving", "row labels", "row label")
INCOME_ALIASES = ("inkomsten", "totaal inkomsten")
EXPENSE_ALIASES = ("uitgaven", "totaal uitgaven")
CATEGORY_ALIASES = ("kolom", "categorie")


def _norm_header(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _cell_text(cell: ET.Element, strings: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "s":
        value_node = cell.find("m:v", NS)
        if value_node is None or value_node.text is None:
            return ""
        try:
            return strings[int(value_node.text)]
        except (IndexError, ValueError):
            return value_node.text
    if cell_type == "inlineStr":
        inline = cell.find("m:is", NS)
        if inline is None:
            return ""
        text = inline.find("m:t", NS)
        if text is not None and text.text is not None:
            return text.text
        return "".join(node.text or "" for node in inline.findall(".//m:t", NS))
    value_node = cell.find("m:v", NS)
    if value_node is not None and value_node.text is not None:
        return value_node.text
    return ""


def read_xlsx_rows(path: Path) -> list[dict[str, str]]:
    """Return sheet rows as ``{column_letter: value, "_row": n}``."""
    with zipfile.ZipFile(path) as zf:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                text = si.find("m:t", NS)
                if text is not None and text.text is not None:
                    strings.append(text.text)
                else:
                    strings.append("".join(node.text or "" for node in si.findall(".//m:t", NS)))

        sheet_name = next(
            (name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
            "xl/worksheets/sheet1.xml",
        )
        sheet = ET.fromstring(zf.read(sheet_name))
        rows: list[dict[str, str]] = []
        for row in sheet.findall(".//m:row", NS):
            cells: dict[str, str] = {}
            for cell in row.findall("m:c", NS):
                ref = cell.get("r", "")
                col = re.sub(r"[^A-Z]", "", ref.upper())
                raw = _cell_text(cell, strings)
                if col and raw != "":
                    cells[col] = raw
            if cells:
                cells["_row"] = str(row.get("r") or len(rows) + 1)
                rows.append(cells)
        return rows


def parse_excel_date(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        serial = float(text.replace(",", "."))
    except ValueError:
        serial = None
    if serial is not None and serial > 0:
        try:
            return (EXCEL_EPOCH + timedelta(days=serial)).strftime("%d-%m-%Y")
        except OverflowError:
            pass
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> float | None:
    text = str(value or "").strip().replace("\xa0", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount == 0:
        return None
    return amount


def category_code(value: str | None, registered: set[int]) -> int:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_CATEGORY
    try:
        code = int(float(text.replace(",", ".")))
    except ValueError:
        return DEFAULT_CATEGORY
    if code not in registered:
        return DEFAULT_CATEGORY
    return code


def category_name_map(categories_path: Path) -> dict[int, str]:
    if not categories_path.is_file():
        return {}
    payload = json.loads(categories_path.read_text(encoding="utf-8"))
    categories = payload.get("categories", {})
    mapping: dict[int, str] = {}
    if not isinstance(categories, dict):
        return mapping
    for name in categories:
        try:
            mapping[int(str(name)[:2])] = str(name)
        except ValueError:
            continue
    return mapping


def _header_map(row: dict[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col, value in row.items():
        if col.startswith("_"):
            continue
        key = _norm_header(value)
        if key and key not in mapping:
            mapping[key] = col
    return mapping


def _column_for(headers: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in headers:
            return headers[alias]
    return None


def find_columns(rows: list[dict[str, str]]) -> tuple[int, dict[str, str]] | None:
    """Return (header_row_index, role → column letter) or None."""
    for index, row in enumerate(rows[:30]):
        headers = _header_map(row)
        date_col = _column_for(headers, DATE_ALIASES)
        if date_col is None:
            continue
        columns = {
            "date": date_col,
            "description": _column_for(headers, DESC_ALIASES) or "",
            "income": _column_for(headers, INCOME_ALIASES) or "",
            "expense": _column_for(headers, EXPENSE_ALIASES) or "",
            "category": _column_for(headers, CATEGORY_ALIASES) or "",
        }
        if columns["income"] or columns["expense"] or columns["category"]:
            return index, columns
    return None


def format_amount(amount: float) -> str:
    sign = "+" if amount > 0 else "-"
    return f"{sign}{abs(amount):.2f}"


def amount_to_cents(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return round(float(text) * 100)
    except ValueError:
        return 0


def amount_str(cents: int) -> str:
    return f"{cents / 100:.2f}"


def transaction_id(source: str, row_index: int, suffix: str) -> str:
    stem = Path(source).stem
    return f"{stem}_{row_index}_{suffix}"


def rows_to_transactions(
    rows: list[dict[str, str]],
    *,
    source: str,
    registered: set[int],
) -> list[dict[str, Any]]:
    located = find_columns(rows)
    if located is None:
        raise ValueError(f"{source}: no Datum header row found")
    header_index, columns = located
    transactions: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        date = parse_excel_date(row.get(columns["date"], ""))
        if date is None:
            continue
        description = str(row.get(columns["description"], "") or "").strip() if columns["description"] else ""
        category = category_code(row.get(columns["category"]) if columns["category"] else None, registered)
        try:
            row_index = int(row.get("_row") or 0)
        except ValueError:
            row_index = 0
        income = parse_amount(row.get(columns["income"]) if columns["income"] else None)
        expense = parse_amount(row.get(columns["expense"]) if columns["expense"] else None)
        entries: list[float] = []
        if income is not None:
            entries.append(income)
        if expense is not None:
            entries.append(-abs(expense))
        if not entries:
            continue
        for entry_index, amount in enumerate(entries):
            suffix = "0" if len(entries) == 1 else str(entry_index)
            transactions.append(
                {
                    "id": transaction_id(source, row_index, suffix),
                    "amount": format_amount(amount),
                    "currency": "EUR",
                    "type": "Excel",
                    "name": description,
                    "iban": "",
                    "description": description,
                    "date": date,
                    "category": category,
                    "_source_file": Path(source).name,
                }
            )
    return transactions


def build_category_totals(transactions: list[dict[str, Any]], name_by_code: dict[int, str]) -> dict[str, str]:
    totals: dict[str, int] = {name: 0 for name in name_by_code.values()}
    for transaction in transactions:
        code = transaction.get("category")
        try:
            code_int = int(code)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            code_int = None
        name = name_by_code.get(code_int, str(code)) if code_int is not None else str(code)
        totals[name] = totals.get(name, 0) + amount_to_cents(transaction.get("amount"))
    return {name: amount_str(cents) for name, cents in totals.items()}


def list_xlsx_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.glob("*.xlsx")
        if path.is_file() and not path.name.startswith("~$")
    )


def _public_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in transaction.items() if not str(key).startswith("_")}


def _recorded_files(account: dict[str, Any] | None) -> list[str] | None:
    if not isinstance(account, dict):
        return None
    files = account.get("files")
    if not isinstance(files, list):
        return None
    return [str(name) for name in files if str(name).strip()]


def _new_folder_files(folder_files: list[str], recorded: list[str] | None) -> list[str]:
    known = set(recorded or [])
    return [name for name in folder_files if name not in known]


def update_account_balances(
    existing: dict[str, Any],
    *,
    folder_files: list[str],
    new_net_cents: int,
) -> list[dict[str, Any]]:
    """Keep the stored balance unless the folder has xlsx files not yet in ``files``."""
    accounts = existing.get("account_balances")
    accounts = [dict(item) for item in accounts if isinstance(item, dict)] if isinstance(accounts, list) else []
    if not accounts:
        accounts = [{"iban": "onbekend", "name": "onbekend", "currency": "EUR", "balance": "0.00"}]

    account = dict(accounts[0])
    recorded = _recorded_files(account) or []
    new_files = _new_folder_files(folder_files, recorded)
    if new_files:
        account["balance"] = amount_str(amount_to_cents(account.get("balance")) + new_net_cents)
        account["files"] = recorded + new_files
    else:
        account["files"] = list(recorded)
    accounts[0] = account
    return accounts


def convert_excel_files(
    folder: Path,
    *,
    categories_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    files = list_xlsx_files(folder)
    if not files:
        raise FileNotFoundError(f"No .xlsx files found in {folder}")

    name_by_code = category_name_map(categories_path)
    registered = set(name_by_code)
    transactions: list[dict[str, Any]] = []
    net_by_file: dict[str, int] = {}
    for path in files:
        rows = rows_to_transactions(read_xlsx_rows(path), source=path.name, registered=registered)
        net_by_file[path.name] = sum(amount_to_cents(item.get("amount")) for item in rows)
        transactions.extend(rows)

    existing_totals: dict[str, Any] = {}
    totals_path = folder / "category_totals.json"
    if totals_path.is_file():
        try:
            loaded = json.loads(totals_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_totals = loaded
        except (OSError, json.JSONDecodeError):
            existing_totals = {}

    folder_names = [path.name for path in files]
    account = None
    accounts = existing_totals.get("account_balances")
    if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict):
        account = accounts[0]
    recorded = _recorded_files(account)
    new_files = _new_folder_files(folder_names, recorded)
    new_net = sum(net_by_file.get(name, 0) for name in new_files)

    categorized = {
        "transactions": [_public_transaction(item) for item in transactions],
        "modifications": [],
    }
    totals = {
        "categories": build_category_totals(transactions, name_by_code),
        "account_balances": update_account_balances(
            existing_totals,
            folder_files=folder_names,
            new_net_cents=new_net,
        ),
    }
    info = {
        "transaction_count": len(transactions),
        "files": folder_names,
        "new_files": new_files,
        "balance_updated": bool(new_files),
        "balance": totals["account_balances"][0].get("balance"),
    }
    return categorized, totals, info


def write_outputs(
    data: Path,
    *,
    categories_path: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    folder = data
    folder.mkdir(parents=True, exist_ok=True)
    categorized, totals, info = convert_excel_files(folder, categories_path=categories_path)
    categorized_path = folder / "categorized_transactions.json"
    totals_path = folder / "category_totals.json"
    categorized_path.write_text(json.dumps(categorized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    totals_path.write_text(json.dumps(totals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return categorized_path, totals_path, info


def import_person_excel(*, data_dir: Path, categories_path: Path) -> dict[str, Any]:
    """Hub RefreshAll entry: rewrite JSON from ``data/*.xlsx``."""
    _categorized_path, _totals_path, info = write_outputs(data_dir, categories_path=categories_path)
    return info


def _default_paths() -> tuple[Path, Path]:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path.cwd()
    data = root / "data" if (root / "data").is_dir() else root
    categories = data.parents[2] / "categories.json" if len(data.parents) >= 2 else data.parent / "categories.json"
    return data, categories


def main() -> int:
    data, categories = _default_paths()
    categorized_path, totals_path, info = write_outputs(data, categories_path=categories)
    print(f"Wrote {categorized_path}")
    print(f"Wrote {totals_path}")
    if info.get("new_files"):
        print(f"Balance updated from new files: {', '.join(info['new_files'])}")
    else:
        print("Balance unchanged (all xlsx files already listed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
