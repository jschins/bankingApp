"""Textual input window for the packaged single-person executable.

Layout and styling follow bankingApp-editor's EventApp (orange rounded borders,
Header / Input fields / buttons / docked status).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import partial
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from single_client import EnableBankingError, get_authorization_url, needs_consent_renewal
from categorize import (
    category_name_for_column_key,
    category_names,
    category_terms_table,
    format_transaction_amount,
    recategorize_transactions,
    record_category_change,
    set_category_term_cell,
    transaction_display_column_keys,
    transactions_for_category,
)

CT_EXTRA_ROWS = 5


class CellEditorInput(Input):
    """Input that commits the active CT cell on Enter."""

    def action_submit(self) -> None:
        screen = self.screen
        if isinstance(screen, CategoriesTermsScreen):
            screen.commit_cell(self.value)
            return
        super().action_submit()


class NewYearToggle(Static):
    """Clickable red cross / green checkmark toggle."""

    DEFAULT_CSS = """
    NewYearToggle {
        width: 5;
        content-align: center middle;
    }
    NewYearToggle.unchecked {
        color: red;
    }
    NewYearToggle.checked {
        color: green;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("✗", **kwargs)
        self._checked = False
        self.add_class("unchecked")

    @property
    def value(self) -> bool:
        return self._checked

    def on_click(self) -> None:
        self._checked = not self._checked
        if self._checked:
            self.update("✓")
            self.remove_class("unchecked")
            self.add_class("checked")
            return
        self.update("✗")
        self.remove_class("checked")
        self.add_class("unchecked")


def previous_month_range() -> tuple[str, str]:
    first_of_this_month = date.today().replace(day=1)
    last_of_previous_month = first_of_this_month - timedelta(days=1)
    first_of_previous_month = last_of_previous_month.replace(day=1)
    return first_of_previous_month.isoformat(), last_of_previous_month.isoformat()


class FetchInputApp(App[dict[str, str | None] | None]):
    TITLE = "Single-person fetch"
    SUB_TITLE = "Bank transaction download"

    BINDINGS = [
        Binding("r", "submit", "Run", show=False),
        Binding("ctrl+r", "submit", "Run", show=False),
        Binding("q", "quit", "Quit", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    CSS = """
    #form {
        height: auto;
        border: round orange;
        padding: 1 2;
        margin: 1 2;
    }
    .field-label {
        margin-top: 1;
    }
    .field-input {
        height: 3;
        border: round orange;
        margin-bottom: 1;
    }
    #actions {
        height: auto;
        margin: 0 2 1 2;
    }
    #actions Button {
        margin-right: 1;
        border: round orange;
    }
    #status {
        dock: bottom;
        height: auto;
        padding: 1 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="form"):
            yield Static("date-from", classes="field-label")
            yield Input(id="date-from", classes="field-input")
            yield Static("date-to", classes="field-label")
            yield Input(id="date-to", classes="field-input")
            with Vertical(id="redirect-section"):
                yield Static("redirect-code", classes="field-label")
                yield Input(
                    id="redirect-code",
                    classes="field-input",
                    placeholder="Paste redirect URL after bank approval",
                )
            yield Static("new year", classes="field-label")
            yield NewYearToggle(id="new-year", classes="field-input")
        with Horizontal(id="actions"):
            yield Button("Run", id="run-button", variant="primary")
            yield Button("Quit", id="quit-button")
        yield Static("", id="status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-button":
            self.action_submit()
        elif event.button.id == "quit-button":
            self.exit(None)

    def action_quit(self) -> None:
        self.exit(None)

    def on_mount(self) -> None:
        date_from, date_to = previous_month_range()
        self.query_one("#date-from", Input).value = date_from
        self.query_one("#date-to", Input).value = date_to

        renewal = needs_consent_renewal()
        self.query_one("#redirect-section").display = renewal
        status = self.query_one("#status", Static)
        if renewal:
            try:
                url = get_authorization_url()
            except EnableBankingError as exc:
                status.update(f"Bank consent must be renewed, but authorization failed: {exc}")
                return
            status.update(
                "Bank consent must be renewed. Open this URL in your browser, approve in "
                f"your bank app, then paste the redirect URL into redirect-code.\n{url}"
            )
            self.query_one("#redirect-code", Input).focus()
            return
        self.query_one("#date-from", Input).focus()

    def action_submit(self) -> None:
        date_from = self.query_one("#date-from", Input).value.strip()
        date_to = self.query_one("#date-to", Input).value.strip()
        redirect_code = self.query_one("#redirect-code", Input).value.strip() or None
        new_year = self.query_one("#new-year", NewYearToggle).value
        if needs_consent_renewal() and not redirect_code:
            self.query_one("#status", Static).update(
                "redirect-code is required while bank consent must be renewed."
            )
            return
        self.exit(
            {
                "date_from": date_from,
                "date_to": date_to,
                "redirect_code": redirect_code,
                "new_year": new_year,
            }
        )


def prompt_fetch_parameters() -> dict[str, Any] | None:
    return FetchInputApp().run()


class ResultApp(App[None]):
    """Simple status window shown after the pipeline finishes."""

    BINDINGS = [("q", "quit", "Quit"), ("enter", "quit", "Close")]

    CSS = """
    #message {
        border: round orange;
        padding: 1 2;
        margin: 1 2;
        height: auto;
    }
    #status {
        dock: bottom;
        height: auto;
        padding: 1 1;
    }
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._message, id="message")
        yield Static("Press Enter or q to close.", id="status")
        yield Footer()


def show_result(message: str) -> None:
    ResultApp(message).run()


class CategoryTotalsApp(App[None]):
    """O-table style category totals after a successful run."""

    TITLE = "Category totals"
    SUB_TITLE = "O-table"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("alt+t", "show_ct_table", "Terms table", show=False),
    ]

    CSS = """
    #totals-table {
        height: 1fr;
        border: round orange;
        margin: 1 2;
    }
    #totals-table .datatable--column-bedrag {
        content-align: right middle;
    }
    #status {
        dock: bottom;
        height: auto;
        padding: 1 1;
    }
    """

    def __init__(self, totals: dict[str, str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._totals = totals

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(
            id="totals-table",
            cursor_type="cell",
            show_row_labels=False,
            cursor_background_priority="css",
        )
        yield Static("Click an amount for details. Alt+T terms table. Press q to close.", id="status")

    def action_show_ct_table(self) -> None:
        self.push_screen(CategoriesTermsScreen())

    def _refresh_totals_from_keywords(self) -> None:
        self._totals = recategorize_transactions()

    def _populate_totals_table(self) -> None:
        table = self.query_one("#totals-table", DataTable)
        table.clear(columns=True)
        table.add_columns(
            ("Categorie", "categorie"),
            ("Bedrag", "bedrag"),
        )
        for category, amount in self._totals.items():
            table.add_row(
                category,
                Text(amount, justify="right", no_wrap=True),
                key=category,
            )

    def on_mount(self) -> None:
        self._refresh_totals_from_keywords()
        self._populate_totals_table()

    def _on_detail_closed(self, _result: object | None) -> None:
        refresh_o_table_from_keywords(self.app)

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.cell_key.column_key != "bedrag":
            return
        category = event.cell_key.row_key.value or str(event.cell_key.row_key)
        self.push_screen(CategoryDetailScreen(category), self._on_detail_closed)


class CategoryPickerScreen(ModalScreen[str]):
    """Second-level drill-down: pick a category name and return it."""

    BINDINGS = [
        Binding("q", "close", "Close", show=False),
        Binding("ctrl+q", "close", "Close", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    CSS = """
    CategoryPickerScreen {
        align: center middle;
    }
    #picker-panel {
        width: 80%;
        height: 80%;
        border: round orange;
        layout: vertical;
        padding: 0 1;
        background: $surface;
    }
    #picker-table {
        height: 1fr;
        border: round orange;
    }
    #status {
        dock: bottom;
        height: auto;
        padding: 1 1;
        text-align: center;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-panel"):
            yield Static("Select category", id="picker-title")
            yield DataTable(id="picker-table", cursor_type="row", zebra_stripes=True)
        yield Static("Click a category or press q to cancel.", id="status")

    def on_mount(self) -> None:
        table = self.query_one("#picker-table", DataTable)
        table.add_columns(("Categorie", "categorie"))
        for name in category_names():
            table.add_row(name, key=name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.control.id != "picker-table":
            return
        category = event.row_key.value or str(event.row_key)
        self.dismiss(category)

    def action_close(self) -> None:
        self.dismiss()


class CategoriesTermsScreen(ModalScreen[None]):
    """Full-screen categories-terms table with spreadsheet-style cell editing."""

    BINDINGS = [
        Binding("q", "close", "Close", show=False),
        Binding("ctrl+q", "close", "Close", show=False),
        Binding("escape", "close", "Close", show=False),
        Binding("alt+t", "close", "Close", show=False),
        Binding("ctrl+s", "save_cell", "Save cell", show=False),
    ]

    CSS = """
    CategoriesTermsScreen {
        layout: vertical;
        width: 100%;
        height: 100%;
        padding: 0 1;
    }
    #ct-table {
        height: 1fr;
        border: round orange;
        margin: 1 0 0 0;
    }
    #cell-editor {
        height: 3;
        border: round orange;
        margin: 1 0 0 0;
    }
    #status {
        height: auto;
        padding: 1 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._selected_cell: DataTable.CellKey | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(
            id="ct-table",
            cursor_type="cell",
            show_row_labels=False,
            zebra_stripes=True,
        )
        yield CellEditorInput(placeholder="Cell value — Enter to save", id="cell-editor")
        yield Static(
            "Select a cell | edit below | Enter or Ctrl+S saves | Alt+T or q closes",
            id="status",
        )

    def on_mount(self) -> None:
        self._populate_table()
        self.query_one("#ct-table", DataTable).focus()

    def _populate_table(self) -> None:
        table = self.query_one("#ct-table", DataTable)
        table.clear(columns=True)
        columns, rows = category_terms_table(extra_rows=CT_EXTRA_ROWS)
        if not columns:
            table.add_columns(("—", "empty"))
            table.add_row("No categories.")
            return

        table.add_columns(*columns)
        for index, row in enumerate(rows):
            table.add_row(*row, key=str(index))

    def _sync_editor_from_cell(self, cell_key: DataTable.CellKey, value: Any) -> None:
        self._selected_cell = cell_key
        editor = self.query_one("#cell-editor", CellEditorInput)
        editor.value = str(value or "")

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if event.control.id != "ct-table":
            return
        self._sync_editor_from_cell(event.cell_key, event.value)

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.control.id != "ct-table":
            return
        self._sync_editor_from_cell(event.cell_key, event.value)
        self.query_one("#cell-editor", CellEditorInput).focus()

    def action_save_cell(self) -> None:
        self.commit_cell(self.query_one("#cell-editor", CellEditorInput).value)

    def commit_cell(self, value: str) -> None:
        if self._selected_cell is None:
            return

        category = category_name_for_column_key(self._selected_cell.column_key)
        if not category:
            return

        row_index = int(self._selected_cell.row_key.value or self._selected_cell.row_key)
        selected = self._selected_cell
        set_category_term_cell(category, row_index, value)
        refresh_o_table_from_keywords(self.app)
        self._populate_table()

        table = self.query_one("#ct-table", DataTable)
        table.focus()
        try:
            coordinate = table.get_cell_coordinate(selected.row_key, selected.column_key)
            table.move_cursor(row=coordinate.row, column=coordinate.column)
        except Exception:
            pass

        try:
            cell_value = table.get_cell(selected.row_key, selected.column_key)
        except Exception:
            cell_value = value
        self._sync_editor_from_cell(selected, cell_value)

    def action_close(self) -> None:
        refresh_o_table_from_keywords(self.app)
        self.dismiss()


class CategoryDetailScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("q", "close", "Close", show=False),
        Binding("ctrl+q", "close", "Close", show=False),
        Binding("alt+t", "show_ct_table", "Terms table", show=False),
    ]

    CSS = """
    CategoryDetailScreen {
        layout: vertical;
        width: 100%;
        height: 100%;
        padding: 0 1;
    }
    #transactions-table {
        height: 1fr;
        border: round orange;
        margin: 1 0 0 0;
    }
    #transactions-table .datatable--column-amount {
        content-align: right middle;
    }
    #status {
        dock: bottom;
        height: auto;
        padding: 1 1;
    }
    """

    def __init__(self, category: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._category = category
        self._transactions: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield DataTable(id="transactions-table", cursor_type="cell", zebra_stripes=True)
        yield Static(
            "Click a transaction category to recategorize. Alt+T terms table. Press q to close.",
            id="status",
        )

    def _populate_transactions_table(self) -> None:
        table = self.query_one("#transactions-table", DataTable)
        table.clear(columns=True)
        transactions = transactions_for_category(self._category)
        self._transactions = {
            str(transaction.get("id")): transaction
            for transaction in transactions
            if transaction.get("id") is not None
        }
        keys = transaction_display_column_keys(transactions)
        if not keys:
            table.add_columns(("—", "empty"))
            table.add_row("No transactions in this category.")
            return

        table.add_columns(*((key, key) for key in keys))
        for transaction in transactions:
            row: list[Any] = []
            for key in keys:
                value = transaction.get(key, "")
                if value is None:
                    value = ""
                if key == "amount":
                    row.append(
                        Text(
                            format_transaction_amount(transaction),
                            justify="right",
                            no_wrap=True,
                        )
                    )
                else:
                    row.append(str(value))
            row_key = str(transaction.get("id") or id(transaction))
            table.add_row(*row, key=row_key)

    def on_mount(self) -> None:
        self._populate_transactions_table()

    def refresh_after_recategorize(self) -> None:
        self._populate_transactions_table()

    def action_show_ct_table(self) -> None:
        self.app.push_screen(CategoriesTermsScreen())

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.control.id != "transactions-table":
            return
        if event.cell_key.column_key != "category":
            return
        transaction_id = event.cell_key.row_key.value or str(event.cell_key.row_key)
        transaction = self._transactions.get(str(transaction_id))
        if not transaction:
            return
        self.app.push_screen(CategoryPickerScreen(), partial(self._apply_category_change, transaction))

    def _apply_category_change(
        self, transaction: dict[str, Any], category_name: str | None
    ) -> None:
        if not category_name:
            return
        record_category_change(transaction, category_name)
        self._populate_transactions_table()

    def action_close(self) -> None:
        self.dismiss()


def show_category_totals(totals: dict[str, str]) -> None:
    CategoryTotalsApp(totals).run()


def refresh_o_table_from_keywords(app: App[None]) -> None:
    """Recategorize transactions and refresh open O-table and detail views."""
    if isinstance(app, CategoryTotalsApp):
        app._refresh_totals_from_keywords()
        app._populate_totals_table()

    for screen in app.screen_stack:
        if isinstance(screen, CategoryDetailScreen):
            screen.refresh_after_recategorize()
