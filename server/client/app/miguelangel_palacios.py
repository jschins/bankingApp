"""Convert Miguel Angel Palacios monthly Excel files to hub JSON format."""

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


def person_root() -> Path:
    """Directory containing miguelangel_palacios.exe (or this module in dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2] / "workspaces" / "dkg" / "miguelangel_palacios"


def data_dir() -> Path:
    return person_root() / "data"


def categories_path() -> Path:
    return person_root().parents[1] / "categories.json"


def _read_xlsx_rows(path: Path) -> list[dict[str, str]]:
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

        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        rows: list[dict[str, str]] = []
        for row in sheet.findall(".//m:row", NS):
            cells: dict[str, str] = {}
            for cell in row.findall("m:c", NS):
                ref = cell.get("r", "")
                col = re.sub(r"[^A-Z]", "", ref.upper())
                value_node = cell.find("m:v", NS)
                if value_node is None or value_node.text is None:
                    continue
                raw = value_node.text
                if cell.get("t") == "s":
                    raw = strings[int(raw)]
                cells[col] = raw
            if cells:
                rows.append(cells)
        return rows


def _parse_excel_date(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        serial = float(text)
    except ValueError:
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
            except ValueError:
                continue
        return None
    if serial <= 0:
        return None
    return (EXCEL_EPOCH + timedelta(days=serial)).strftime("%d-%m-%Y")


def _parse_amount(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount == 0:
        return None
    return amount


def _category_code(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _format_amount(amount: float) -> str:
    sign = "+" if amount > 0 else "-"
    return f"{sign}{abs(amount):.2f}"


def _category_name_map() -> dict[int, str]:
    path = categories_path()
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    categories = payload.get("categories", {})
    mapping: dict[int, str] = {}
    for name in categories:
        try:
            mapping[int(str(name)[:2])] = name
        except ValueError:
            continue
    return mapping


def _amount_to_cents(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return round(float(text) * 100)
    except ValueError:
        return 0


def _amount_str(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _transaction_id(source: str, row_index: int, suffix: str) -> str:
    stem = Path(source).stem
    return f"{stem}_{row_index}_{suffix}"


def rows_to_transactions(rows: list[dict[str, str]], *, source: str) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        date = _parse_excel_date(row.get("A", ""))
        if date is None:
            continue

        description = str(row.get("C", "") or "").strip()
        category = _category_code(row.get("G"))
        income = _parse_amount(row.get("D"))
        expense = _parse_amount(row.get("E"))

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
                    "id": _transaction_id(source, row_index, suffix),
                    "amount": _format_amount(amount),
                    "currency": "EUR",
                    "type": "Excel",
                    "name": description,
                    "iban": "",
                    "description": description,
                    "date": date,
                    "category": category if category is not None else 18,
                }
            )
    return transactions


def build_category_totals(transactions: list[dict[str, Any]]) -> dict[str, str]:
    name_by_code = _category_name_map()
    general_names = list(name_by_code.values())
    totals: dict[str, int] = {name: 0 for name in general_names}

    for transaction in transactions:
        code = transaction.get("category")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = None
        name = name_by_code.get(code_int, str(code)) if code_int is not None else str(code)
        totals[name] = totals.get(name, 0) + _amount_to_cents(transaction.get("amount"))

    return {name: _amount_str(cents) for name, cents in totals.items()}


def convert_excel_files(data: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    folder = data or data_dir()
    files = sorted(
        path
        for path in folder.glob("*.xlsx")
        if path.is_file() and not path.name.startswith("~$")
    )
    if not files:
        raise FileNotFoundError(f"No .xlsx files found in {folder}")

    transactions: list[dict[str, Any]] = []
    for path in files:
        transactions.extend(rows_to_transactions(_read_xlsx_rows(path), source=path.name))

    categorized = {"transactions": transactions, "modifications": []}
    totals = {"categories": build_category_totals(transactions)}
    return categorized, totals


def write_outputs(data: Path | None = None) -> tuple[Path, Path]:
    folder = data or data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    categorized, totals = convert_excel_files(folder)
    categorized_path = folder / "categorized_transactions.json"
    totals_path = folder / "category_totals.json"
    categorized_path.write_text(json.dumps(categorized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    totals_path.write_text(json.dumps(totals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return categorized_path, totals_path


def main() -> int:
    categorized_path, totals_path = write_outputs()
    print(f"Wrote {categorized_path}")
    print(f"Wrote {totals_path}")
    return 0
