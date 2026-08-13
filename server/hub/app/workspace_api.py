"""High-level workspace UI operations (hub-only data; no client copies)."""
from __future__ import annotations

import json
from typing import Any

from app import store
from app.runtime import set_active_workspace


def _bind(workspace: str) -> str:
    ws = workspace.strip().replace("\\", "/").strip("/")
    if not ws or ".." in ws.split("/") or ws.startswith("/"):
        raise ValueError(f"Invalid workspace: {workspace!r}")
    # Validate path under data_root.
    store.workspace_dir(ws)
    set_active_workspace(ws)
    from app.settings import init_app

    init_app()
    return ws


def _has_secrets(workspace: str) -> bool:
    root = store.workspace_dir(workspace)
    if not root.is_dir():
        return False
    for child in root.iterdir():
        if child.is_dir() and (child / "secret").is_dir() and (child / "data").is_dir():
            if any((child / "secret").glob("*.pem")):
                return True
    return False


def capabilities(workspace: str) -> dict[str, Any]:
    ws = _bind(workspace)
    from app.settings import get_people

    people = get_people()
    return {
        "ok": True,
        "workspace": ws,
        "has_secrets": _has_secrets(ws),
        "people": [{"short": p.short, "folder": p.folder_name} for p in people],
    }


def people(workspace: str) -> dict[str, Any]:
    return {"people": capabilities(workspace)["people"]}


def matrix(workspace: str) -> dict[str, Any]:
    _bind(workspace)
    from app.matrix import build_matrix

    return build_matrix()


def transactions(workspace: str, short: str, category_name: str) -> dict[str, Any]:
    _bind(workspace)
    import app.paths as paths
    from app.core.categorize import (
        _load_json_object,
        _read_json,
        category_code_set,
        modification_style_ids,
        remainder_category_name,
        terms_for_category,
        transaction_display_column_keys,
        transactions_for_category as load_transactions,
    )
    from app.paths import bind_person
    from app.people import get_person

    pack = get_person(short)
    with bind_person(pack):
        rows = load_transactions(category_name)
        cat_data = _read_json(paths.CATEGORIES_PATH)
        payload = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
        description_modified_ids, category_modified_ids = modification_style_ids(payload)
        return {
            "person": pack.short,
            "folder": pack.folder_name,
            "category": category_name,
            "columns": transaction_display_column_keys(rows),
            "transactions": rows,
            "keywords": terms_for_category(category_name),
            "description_modified_ids": description_modified_ids,
            "category_modified_ids": category_modified_ids,
            "abbreviations": cat_data.get("abbreviations", {}) if isinstance(cat_data, dict) else {},
            "valid_category_codes": sorted(category_code_set()),
            "remainder_category": remainder_category_name(),
        }


def record_modification(
    workspace: str,
    short: str,
    transaction: dict[str, Any],
    *,
    source: str = "local",
) -> dict[str, Any]:
    ws = _bind(workspace)
    from app.core.categorize import record_modification as _record
    from app.paths import bind_person
    from app.people import get_person

    pack = get_person(short)
    with bind_person(pack):
        modified = _record(transaction)
    rel = f"{pack.folder_name}/data/{store.CATEGORIZED}"
    path = store.resolve_file_path(ws, rel)
    content = json.loads(path.read_text(encoding="utf-8"))
    store.put_file(
        ws,
        rel,
        content,
        source=source,
        skip_recalc=True,
    )
    store.recalculate_workspace(ws)
    return {"transaction": modified, "person": pack.short}


def settings(workspace: str) -> dict[str, Any]:
    _bind(workspace)
    from app.core.categorize import (
        _category_map,
        _load_json_object,
        category_code_set,
        remainder_category_name,
        type_rules_payload,
    )
    from app.matrix import category_names, load_general_file
    from app.paths import bind_person
    from app.settings import get_people

    people_list = get_people()
    general_file = load_general_file(people_list)
    general = _category_map(general_file)
    personal: dict[str, dict[str, list[str]]] = {}
    typerules: list[dict[str, str]] = []
    codes: list[int] = []
    remainder = ""
    for pack in people_list:
        with bind_person(pack):
            personal[pack.short] = _category_map(
                _load_json_object(pack.personal_categories_path)
            )
            if not typerules:
                typerules = type_rules_payload()
            if not codes:
                codes = sorted(category_code_set())
                remainder = remainder_category_name()
    return {
        "categories": category_names(people_list),
        "people": [{"short": p.short, "folder": p.folder_name} for p in people_list],
        "general": general,
        "personal": personal,
        "valid_category_codes": codes,
        "remainder_category": remainder,
        "typerules": typerules,
    }


def update_settings(
    workspace: str,
    group: str,
    category_name: str,
    terms: list[str],
    *,
    source: str = "local",
) -> dict[str, Any]:
    ws = _bind(workspace)
    from app.matrix import save_general_terms, save_personal_terms
    from app.people import get_person

    if group == "general":
        cleaned = save_general_terms(category_name, terms)
        cats_path = store.merged_categories_path()
        content = json.loads(cats_path.read_text(encoding="utf-8"))
        store.put_file(
            ws,
            store.SHARED_CATEGORIES,
            content,
            source=source,
            skip_recalc=True,
        )
        recalc = store.recalculate_workspace(ws)
        return {
            "group": "general",
            "category": category_name,
            "terms": cleaned,
            "matrix": recalc.get("matrix") or matrix(ws),
        }

    pack = get_person(group)
    cleaned = save_personal_terms(pack.short, category_name, terms)
    rel = f"{pack.folder_name}/data/{store.PERSONAL_CATEGORIES}"
    path = store.resolve_file_path(ws, rel)
    content = json.loads(path.read_text(encoding="utf-8"))
    store.put_file(
        ws,
        rel,
        content,
        source=source,
        skip_recalc=True,
    )
    recalc = store.recalculate_workspace(ws)
    return {
        "group": pack.short,
        "category": category_name,
        "terms": cleaned,
        "matrix": recalc.get("matrix") or matrix(ws),
    }


def add_term(
    workspace: str,
    *,
    category_name: str,
    term: str,
    general: bool,
    person: str | None = None,
    source: str = "local",
) -> dict[str, Any]:
    ws = _bind(workspace)
    from app.core.categorize import append_category_term
    from app.matrix import load_general_file, sync_general_categories
    from app.paths import bind_person
    from app.people import get_person
    from app.settings import get_people

    people_list = get_people()
    if general:
        pack = people_list[0]
        with bind_person(pack):
            terms = append_category_term(
                category_name,
                term,
                group="general",
                person=pack.short,
            )
        sync_general_categories(load_general_file([pack]), people_list)
        cats_path = store.merged_categories_path()
        content = json.loads(cats_path.read_text(encoding="utf-8"))
        store.put_file(
            ws,
            store.SHARED_CATEGORIES,
            content,
            source=source,
            skip_recalc=True,
        )
        recalc = store.recalculate_workspace(ws)
        return {
            "group": "general",
            "category": category_name,
            "term": term,
            "terms": terms,
            "matrix": recalc.get("matrix") or matrix(ws),
        }

    short = (person or "").strip()
    if not short:
        raise ValueError("person is required when general=false")
    pack = get_person(short)
    with bind_person(pack):
        terms = append_category_term(
            category_name,
            term,
            group=pack.short,
            person=pack.short,
        )
    rel = f"{pack.folder_name}/data/{store.PERSONAL_CATEGORIES}"
    path = store.resolve_file_path(ws, rel)
    content = json.loads(path.read_text(encoding="utf-8"))
    store.put_file(
        ws,
        rel,
        content,
        source=source,
        skip_recalc=True,
    )
    recalc = store.recalculate_workspace(ws)
    return {
        "group": pack.short,
        "category": category_name,
        "term": term,
        "terms": terms,
        "matrix": recalc.get("matrix") or matrix(ws),
    }


def refresh(
    workspace: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    ws = _bind(workspace)
    if not _has_secrets(ws):
        raise PermissionError(
            "Bank refresh requires secrets under workspaces/<ws>/<person>/secret/."
        )
    from app.matrix import refresh_all

    result = refresh_all(date_from=date_from, date_to=date_to)
    # Publish downloaded + categorized after bank fetch.
    root = store.workspace_dir(ws)
    for child in root.iterdir():
        if not child.is_dir() or not (child / "data").is_dir():
            continue
        for name in (store.DOWNLOADED, store.CATEGORIZED, store.CATEGORY_TOTALS):
            path = child / "data" / name
            if not path.is_file():
                continue
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            store.put_file(
                ws,
                f"{child.name}/data/{name}",
                content,
                source="central",
                skip_recalc=True,
            )
    recalc = store.recalculate_workspace(ws)
    result["matrix"] = recalc.get("matrix") or result.get("matrix")
    return result
