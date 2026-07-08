from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.paths import configure
from app.settings import get_app_settings

_settings = None


def _init_app() -> None:
    global _settings
    _settings = configure()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _init_app()
    yield


app = FastAPI(title="single-docker", version="0.1", lifespan=lifespan)


class FetchRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    redirect_code: str | None = None
    new_year: bool = False


class TermsColumnRequest(BaseModel):
    terms: list[str] = Field(default_factory=list)


class TermsCellRequest(BaseModel):
    row_index: int
    value: str


class CategoryChangeRequest(BaseModel):
    category_name: str


class ModificationRequest(BaseModel):
    transaction: dict[str, Any]


class SettingsTermsRequest(BaseModel):
    terms: list[str] = Field(default_factory=list)


class AddTermRequest(BaseModel):
    category_name: str
    term: str
    general: bool = False


class AccountSelectionRequest(BaseModel):
    enabled_uids: list[str] = Field(default_factory=list)


def _bank_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app.core.categorize import (
        CATEGORIZE_LOGIC_VERSION,
        _category_map,
        _load_raw_transactions,
        _read_json,
    )
    from app.paths import (
        CATEGORIZED_TRANSACTIONS_PATH,
        CATEGORIES_PATH,
        CONSENT_PATH,
        PERSONAL_CATEGORIES_PATH,
        RAW_TRANSACTIONS_PATH,
    )
    from app.runtime import app_root, bundle_dir, frontend_dist_dir, frontend_dist_ok, is_frozen

    settings = get_app_settings()
    dist = frontend_dist_dir()
    general_terms = 0
    if CATEGORIES_PATH.is_file():
        general = _category_map(_read_json(CATEGORIES_PATH))
        general_terms = sum(len(terms) for terms in general.values())
    personal_terms = 0
    if PERSONAL_CATEGORIES_PATH.is_file():
        personal = _category_map(_read_json(PERSONAL_CATEGORIES_PATH))
        personal_terms = sum(len(terms) for terms in personal.values())
    raw_transactions = _load_raw_transactions()
    raw_with_account_index = sum(
        1 for tx in raw_transactions if isinstance(tx, dict) and "_account_index" in tx
    )
    return {
        "status": "ok",
        "person": settings.person_short,
        "app_root": str(app_root()),
        "data_dir": str(settings.data_dir),
        "profile": str(settings.profile_path),
        "private_key": str(settings.private_key_path),
        "consent": str(CONSENT_PATH),
        "categories": str(CATEGORIES_PATH),
        "personal_categories": str(PERSONAL_CATEGORIES_PATH),
        "raw_transactions": str(RAW_TRANSACTIONS_PATH),
        "categorized_transactions": str(CATEGORIZED_TRANSACTIONS_PATH),
        "general_term_count": general_terms,
        "personal_term_count": personal_terms,
        "raw_transaction_count": len(raw_transactions),
        "raw_with_account_index": raw_with_account_index,
        "categorize_logic_version": CATEGORIZE_LOGIC_VERSION,
        "frozen": is_frozen(),
        "frontend_dist": str(dist),
        "frontend_ok": frontend_dist_ok(),
        "bundle_dir": str(bundle_dir() or ""),
    }


@app.get("/api/consent/status")
def consent_status() -> dict[str, Any]:
    from app.core.single_client import needs_consent_renewal

    return {"needs_renewal": needs_consent_renewal()}


@app.post("/api/consent/authorize")
def consent_authorize() -> dict[str, str]:
    from app.core.single_client import EnableBankingError, get_authorization_url

    try:
        return {"url": get_authorization_url()}
    except EnableBankingError as exc:
        raise _bank_error(exc) from exc


@app.get("/api/accounts")
def bank_accounts() -> dict[str, Any]:
    from app.core.single_client import list_bank_accounts

    return list_bank_accounts()


@app.put("/api/accounts")
def update_bank_accounts(body: AccountSelectionRequest) -> dict[str, Any]:
    from app.core.single_client import EnableBankingError, set_enabled_account_uids

    try:
        return set_enabled_account_uids(body.enabled_uids)
    except EnableBankingError as exc:
        raise _bank_error(exc) from exc


@app.post("/api/fetch")
def fetch_transactions(body: FetchRequest) -> dict[str, Any]:
    from app.core.categorize import process_transactions
    from app.core.single_client import EnableBankingError, fetch_transactions as bank_fetch

    try:
        result = bank_fetch(
            date_from=body.date_from,
            date_to=body.date_to,
            redirect_code=body.redirect_code,
        )
        totals = process_transactions(result.transactions, body.new_year)
    except EnableBankingError as exc:
        raise _bank_error(exc) from exc
    return {
        "transaction_count": len(result.transactions),
        "totals": totals,
        "date_from": result.date_from,
        "date_to": result.date_to,
        "renewal_day": result.renewal_day,
        "warnings": result.warnings,
        "account_errors": result.account_errors,
    }


@app.get("/api/totals")
def category_totals() -> dict[str, str]:
    from app.core.categorize import load_category_totals, recategorize_transactions

    totals = load_category_totals()
    if not totals:
        totals = recategorize_transactions()
    return totals


@app.post("/api/recalculate")
def recalculate() -> dict[str, str]:
    from app.core.categorize import recategorize_transactions

    return recategorize_transactions()


@app.get("/api/categories")
def list_categories() -> dict[str, Any]:
    from app.core.categorize import category_code_set, category_names, remainder_category_name

    return {
        "categories": category_names(),
        "valid_category_codes": sorted(category_code_set()),
        "remainder_category": remainder_category_name(),
    }


@app.get("/api/transactions/{category_name}")
def transactions_for_category(category_name: str) -> dict[str, Any]:
    from app.core.categorize import (
        _load_json_object,
        _read_json,
        category_code_set,
        CATEGORIZED_TRANSACTIONS_PATH,
        CATEGORIES_PATH,
        remainder_category_name,
        terms_for_category,
        transaction_display_column_keys,
        transactions_for_category as load_transactions,
    )
    from app.paths import PERSON_SHORT

    rows = load_transactions(category_name)
    cat_data = _read_json(CATEGORIES_PATH)
    modifications = _load_json_object(CATEGORIZED_TRANSACTIONS_PATH).get("modifications")
    modified_ids = [
        str(item.get("id"))
        for item in (modifications if isinstance(modifications, list) else [])
        if isinstance(item, dict) and item.get("id") is not None
    ]
    return {
        "person": PERSON_SHORT,
        "category": category_name,
        "columns": transaction_display_column_keys(rows),
        "transactions": rows,
        "keywords": terms_for_category(category_name),
        "modified_ids": modified_ids,
        "abbreviations": cat_data.get("abbreviations", {}) if isinstance(cat_data, dict) else {},
        "valid_category_codes": sorted(category_code_set()),
        "remainder_category": remainder_category_name(),
    }


@app.put("/api/transactions/modification")
def save_modification(body: ModificationRequest) -> dict[str, Any]:
    from app.core.categorize import record_modification

    try:
        modified = record_modification(body.transaction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"transaction": modified}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    from app.core.categorize import (
        _category_map,
        _load_json_object,
        _read_json,
        category_code_set,
        category_names,
        CATEGORIES_PATH,
        PERSONAL_CATEGORIES_PATH,
        remainder_category_name,
    )
    from app.paths import PERSON_SHORT

    general = _category_map(_read_json(CATEGORIES_PATH))
    personal = _category_map(_load_json_object(PERSONAL_CATEGORIES_PATH))
    return {
        "categories": category_names(),
        "person": PERSON_SHORT,
        "general": general,
        "personal": {PERSON_SHORT: personal},
        "valid_category_codes": sorted(category_code_set()),
        "remainder_category": remainder_category_name(),
    }


@app.put("/api/settings/{group}/{category_name}")
def update_settings(group: str, category_name: str, body: SettingsTermsRequest) -> dict[str, Any]:
    from app.core.categorize import (
        _save_general_category_terms,
        _save_personal_category_terms,
        recategorize_transactions,
        terms_for_category,
    )
    from app.paths import PERSON_SHORT

    if group == "general":
        _save_general_category_terms(category_name, body.terms)
    elif group == PERSON_SHORT:
        _save_personal_category_terms(category_name, body.terms)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown settings group: {group}")
    recategorize_transactions()
    return {"group": group, "category": category_name, "terms": terms_for_category(category_name)}


@app.post("/api/settings/add-term")
def add_term(body: AddTermRequest) -> dict[str, Any]:
    from app.core.categorize import append_category_term, recategorize_transactions
    from app.paths import PERSON_SHORT

    group = "general" if body.general else PERSON_SHORT
    try:
        terms = append_category_term(
            body.category_name,
            body.term,
            group=group,
            person=PERSON_SHORT,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    totals = recategorize_transactions()
    return {
        "group": group,
        "category": body.category_name,
        "term": body.term.strip(),
        "terms": terms,
        "totals": totals,
    }


@app.get("/api/terms")
def terms_table() -> dict[str, Any]:
    from app.core.categorize import category_terms_table

    columns, rows = category_terms_table(extra_rows=0)
    return {
        "columns": [{"name": name, "key": key} for name, key in columns],
        "rows": rows,
    }


@app.put("/api/terms/{category_name}")
def save_terms_column(category_name: str, body: TermsColumnRequest) -> dict[str, Any]:
    from app.core.categorize import recategorize_transactions, save_category_terms_column

    terms = save_category_terms_column(category_name, body.terms)
    totals = recategorize_transactions()
    return {"category": category_name, "terms": terms, "totals": totals}


@app.patch("/api/terms/{category_name}/cell")
def save_terms_cell(category_name: str, body: TermsCellRequest) -> dict[str, Any]:
    from app.core.categorize import recategorize_transactions, set_category_term_cell

    terms = set_category_term_cell(category_name, body.row_index, body.value)
    totals = recategorize_transactions()
    return {"category": category_name, "terms": terms, "totals": totals}


@app.post("/api/transactions/{transaction_id}/category")
def change_transaction_category(transaction_id: str, body: CategoryChangeRequest) -> dict[str, Any]:
    from app.core.categorize import (
        _source_transaction_by_id,
        record_category_change,
    )

    source = _source_transaction_by_id(transaction_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Transaction not found: {transaction_id}")
    modified = record_category_change(source, body.category_name)
    return {"transaction": modified}


@app.post("/api/upload")
def upload_to_server() -> dict[str, Any]:
    from app.core.server_client import ServerClient, ServerError
    from app.core.single_client import EnableBankingError, load_profile
    from app.paths import CATEGORIZED_TRANSACTIONS_PATH, CATEGORY_TOTALS_PATH, CONSENT_PATH

    settings = get_app_settings()
    if not settings.server_url:
        raise HTTPException(
            status_code=400,
            detail="server_url is not set; use bankingApp_SERVER_URL environment variable",
        )

    try:
        profile = load_profile()
    except EnableBankingError as exc:
        raise _bank_error(exc) from exc

    person = str(profile.get("person") or "unknown")
    client = ServerClient(settings.server_url, settings.server_api_key)

    payloads: dict[str, Any] = {}
    for path, name in (
        (CONSENT_PATH, "consent.json"),
        (CATEGORY_TOTALS_PATH, "category_totals.json"),
        (CATEGORIZED_TRANSACTIONS_PATH, "categorized_transactions.json"),
    ):
        if path.exists():
            payloads[name] = json.loads(path.read_text(encoding="utf-8"))

    if not payloads:
        raise HTTPException(status_code=400, detail="Nothing to upload; data folder is empty")

    try:
        sizes = {name: client.put_json(person, name, data) for name, data in payloads.items()}
    except ServerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"person": person, "uploaded": list(sizes.keys()), "bytes": sizes}


def _mount_frontend() -> None:
    import sys

    from fastapi.staticfiles import StaticFiles

    from app.runtime import frontend_dist_dir, frontend_dist_ok

    dist = frontend_dist_dir()
    if frontend_dist_ok():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
        return

    print(
        f"WARNING: UI not bundled — {dist / 'index.html'} missing.\n"
        "Rebuild with: uv run --group build python scripts/build_exe.py\n"
        "API still works at /api/health",
        file=sys.stderr,
    )


_mount_frontend()


def run() -> None:
    import os
    import threading
    import time
    import webbrowser

    import uvicorn

    from app.runtime import is_frozen

    host = os.environ.get("HOST", "127.0.0.1" if is_frozen() else "0.0.0.0")
    port = int(os.environ.get("PORT", "8200"))
    _init_app()

    if is_frozen():

        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}/")

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
