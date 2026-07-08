from __future__ import annotations

import sys

from paths import init_paths

init_paths()

from categorize import process_transactions
from input_window import prompt_fetch_parameters, show_category_totals, show_result
from single_client import EnableBankingError, fetch_transactions


def run_pipeline(
    date_from: str | None = None,
    date_to: str | None = None,
    redirect_code: str | None = None,
    new_year: bool = False,
) -> dict[str, str]:
    raw_transactions = fetch_transactions(
        date_from=date_from,
        date_to=date_to,
        redirect_code=redirect_code,
    )
    return process_transactions(raw_transactions, new_year)


def main() -> int:
    params = prompt_fetch_parameters()
    if params is None:
        return 0

    try:
        totals = run_pipeline(**params)
    except (EnableBankingError, FileNotFoundError, OSError) as exc:
        show_result(f"Failed.\n\n{exc}")
        return 1

    show_category_totals(totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
