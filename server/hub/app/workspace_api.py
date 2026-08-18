"""High-level workspace UI operations (hub-only data; no client copies)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from app import store
from app.paths import CALC_LOCK
from app.runtime import set_active_workspace
from app.yearpath import current_year, ensure_year_folder, has_person_layout, parse_year


def _clean_ws(workspace: str) -> str:
    ws = workspace.strip().replace("\\", "/").strip("/")
    if not ws or ".." in ws.split("/") or ws.startswith("/"):
        raise ValueError(f"Invalid workspace: {workspace!r}")
    store.workspace_dir(ws)
    return ws


@contextmanager
def _workspace_scope(workspace: str) -> Iterator[str]:
    """Bind active workspace + people list under CALC_LOCK for the whole request.

    Path globals and ``_active_workspace`` are process-wide; uvicorn runs sync
    routes in a threadpool, so concurrent client requests must not interleave.
    """
    ws = _clean_ws(workspace)
    with CALC_LOCK:
        set_active_workspace(ws)
        from app.settings import init_app

        init_app()
        yield ws


def _has_secrets(workspace: str) -> bool:
    root = store.workspace_dir(workspace)
    if not root.is_dir():
        return False
    for child in root.iterdir():
        if child.is_dir() and (child / "secret").is_dir() and has_person_layout(child):
            if any((child / "secret").glob("*.pem")):
                return True
    return False


def _people_payload(people: list[Any]) -> list[dict[str, str]]:
    return [{"short": p.short, "folder": p.folder_name} for p in people]


def _with_person(rows: list[dict[str, Any]], short: str) -> list[dict[str, Any]]:
    """Stamp each row with the bound person short (API identity; not written to disk)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["person"] = short
        out.append(item)
    return out


def capabilities(workspace: str) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
        from app.settings import get_people

        people = get_people()
        return {
            "ok": True,
            "workspace": ws,
            "has_secrets": _has_secrets(ws),
            "people": _people_payload(people),
        }


def people(workspace: str) -> dict[str, Any]:
    caps = capabilities(workspace)
    return {"workspace": caps["workspace"], "people": caps["people"]}


def matrix(workspace: str) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
        from app.matrix import build_matrix

        payload = build_matrix()
        payload["workspace"] = ws
        return payload


def transactions(workspace: str, short: str, category_name: str) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
        import app.paths as paths
        from app.core.categorize import (
            _load_json_object,
            _read_json,
            category_code_set,
            modification_style_ids,
            remainder_category_name,
            terms_for_category,
            transaction_display_column_keys as column_keys,
            transactions_for_category as load_transactions,
        )
        from app.paths import bind_person
        from app.people import get_person

        pack = get_person(short)
        with bind_person(pack):
            rows = load_transactions(category_name)
            cat_data = _read_json(paths.CATEGORIES_PATH)
            payload = _load_json_object(paths.CATEGORIZED_TRANSACTIONS_PATH)
            description_modified_ids, category_modified_ids = modification_style_ids(
                payload
            )
            return {
                "workspace": ws,
                "person": pack.short,
                "folder": pack.folder_name,
                "category": category_name,
                "columns": column_keys(rows),
                "transactions": _with_person(rows, pack.short),
                "keywords": terms_for_category(category_name),
                "description_modified_ids": description_modified_ids,
                "category_modified_ids": category_modified_ids,
                "abbreviations": cat_data.get("abbreviations", {})
                if isinstance(cat_data, dict)
                else {},
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
    with _workspace_scope(workspace) as ws:
        from app.core.categorize import record_modification as _record
        from app.paths import bind_person
        from app.people import get_person

        pack = get_person(short)
        with bind_person(pack):
            modified = _record(transaction)
        rel = store.person_year_rel(pack.folder_name, store.CATEGORIZED, year=pack.year)
        path = store.resolve_file_path(ws, rel)
        content = json.loads(path.read_text(encoding="utf-8"))
        store.put_file(
            ws,
            rel,
            content,
            source=source,
            skip_recalc=True,
            skip_event=True,
        )
        result = store.mutate_and_recalculate(ws, [rel], source=source)
        modified_out = dict(modified) if isinstance(modified, dict) else modified
        if isinstance(modified_out, dict):
            modified_out["person"] = pack.short
        return {
            "workspace": ws,
            "person": pack.short,
            "folder": pack.folder_name,
            "transaction": modified_out,
            "affected_files": result.get("affected_files") or [],
            "matrix": result.get("matrix"),
        }


def settings(workspace: str) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
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
            "workspace": ws,
            "categories": category_names(people_list),
            "people": _people_payload(people_list),
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
    """Save terms, then announce + recalculate (lock released before long recalc)."""
    from app.matrix import build_matrix, save_general_terms, save_personal_terms
    from app.people import get_person

    with _workspace_scope(workspace) as ws:
        if group == "general":
            cleaned = save_general_terms(category_name, terms)
            cats_path = store.merged_categories_path()
            content = json.loads(cats_path.read_text(encoding="utf-8"))
            rel = store.SHARED_CATEGORIES
            recalc_all = True
            pack_short = "general"
            pack_folder = None
        else:
            pack = get_person(group)
            cleaned = save_personal_terms(pack.short, category_name, terms)
            rel = store.person_secret_rel(pack.folder_name, store.PERSONAL_CATEGORIES)
            path = store.resolve_file_path(ws, rel)
            if path.is_file():
                content = json.loads(path.read_text(encoding="utf-8"))
            else:
                content = {}
            recalc_all = False
            pack_short = pack.short
            pack_folder = pack.folder_name

    store.put_file(
        ws,
        rel,
        content,
        source=source,
        skip_recalc=True,
        skip_event=True,
    )
    result = store.mutate_and_recalculate(
        ws,
        [rel],
        source=source,
        recalc_all_workspaces=recalc_all,
    )
    with _workspace_scope(ws):
        matrix = result.get("matrix") or {**build_matrix(), "workspace": ws}
    return {
        "workspace": ws,
        "group": pack_short,
        "folder": pack_folder,
        "category": category_name,
        "terms": cleaned,
        "matrix": matrix,
        "affected_files": result.get("affected_files") or [],
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
    with _workspace_scope(workspace) as ws:
        from app.core.categorize import append_category_term
        from app.matrix import (
            build_matrix,
            load_general_file,
            sync_general_categories,
        )
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
                skip_event=True,
            )
            result = store.mutate_and_recalculate(
                ws,
                [store.SHARED_CATEGORIES],
                source=source,
                recalc_all_workspaces=True,
            )
            return {
                "workspace": ws,
                "group": "general",
                "folder": None,
                "category": category_name,
                "term": term,
                "terms": terms,
                "matrix": result.get("matrix") or {**build_matrix(), "workspace": ws},
                "affected_files": result.get("affected_files") or [],
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
        rel = store.person_secret_rel(pack.folder_name, store.PERSONAL_CATEGORIES)
        path = store.resolve_file_path(ws, rel)
        content = json.loads(path.read_text(encoding="utf-8"))
        store.put_file(
            ws,
            rel,
            content,
            source=source,
            skip_recalc=True,
            skip_event=True,
        )
        result = store.mutate_and_recalculate(ws, [rel], source=source)
        return {
            "workspace": ws,
            "group": pack.short,
            "folder": pack.folder_name,
            "category": category_name,
            "term": term,
            "terms": terms,
            "matrix": result.get("matrix") or {**build_matrix(), "workspace": ws},
            "affected_files": result.get("affected_files") or [],
        }


def _ingest_person_data_files(
    ws: str, *, folder_names: list[str] | None = None, year: str | None = None
) -> list[str]:
    """Load on-disk person year JSON into the store; return relative paths."""
    inputs: list[str] = []
    root = store.workspace_dir(ws)
    wanted = {name for name in folder_names} if folder_names is not None else None
    y = parse_year(year)
    for child in root.iterdir():
        if not child.is_dir() or not has_person_layout(child):
            continue
        if wanted is not None and child.name not in wanted:
            continue
        for name in (store.DOWNLOADED, store.CATEGORIZED, store.CATEGORY_TOTALS):
            path = child / y / name
            if not path.is_file():
                continue
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rel = store.person_year_rel(child.name, name, year=y)
            inputs.append(rel)
            store.put_file(
                ws,
                rel,
                content,
                source="central",
                skip_recalc=True,
                skip_event=True,
            )
    return inputs


def _ensure_people_year(
    ws: str, *, folder_names: list[str] | None = None, year: str | None = None
) -> None:
    """Seed missing year folders for people in ``ws`` (write path only)."""
    from app.paths import shared_categories_path

    y = parse_year(year)
    cats = shared_categories_path()
    root = store.workspace_dir(ws)
    wanted = {name for name in folder_names} if folder_names is not None else None
    for child in root.iterdir():
        if not child.is_dir() or not has_person_layout(child):
            continue
        if wanted is not None and child.name not in wanted:
            continue
        ensure_year_folder(child, y, categories_path=cats)


def refresh(
    workspace: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Refresh all people (append-only). No new-year overwrite."""
    from app.matrix import refresh_all

    with _workspace_scope(workspace) as ws:
        _ensure_people_year(ws)
        result = refresh_all(date_from=date_from, date_to=date_to)
        inputs = _ingest_person_data_files(ws)

    mut = store.mutate_and_recalculate(ws, inputs, source="central")
    matrix_payload = mut.get("matrix") or result.get("matrix") or {}
    if isinstance(matrix_payload, dict):
        matrix_payload = {**matrix_payload, "workspace": ws}
    # Keep per-person fetch stats (transaction_count, skipped, …) from refresh_all.
    return {
        **result,
        "workspace": ws,
        "matrix": matrix_payload,
        "affected_files": mut.get("affected_files") or [],
        "results": list(result.get("results") or []),
        "warnings": list(result.get("warnings") or []),
    }


def refresh_person(
    workspace: str,
    short: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    new_year: bool = False,
) -> dict[str, Any]:
    """Refresh one person; optional new-year overwrite applies only to that person."""
    from app.matrix import refresh_person as matrix_refresh_person
    from app.people import get_person

    with _workspace_scope(workspace) as ws:
        pack = get_person(short)
        _ensure_people_year(ws, folder_names=[pack.folder_name], year=pack.year)
        result = matrix_refresh_person(
            short,
            date_from=date_from,
            date_to=date_to,
            new_year=new_year,
        )
        inputs = _ingest_person_data_files(ws, folder_names=[pack.folder_name])
        from app import consent_flow

        # Person-only fetch completed (or re-skipped); drop the post-callback prompt.
        consent_flow.clear_ready(workspace=ws, short=pack.short)

    mut = store.mutate_and_recalculate(ws, inputs, source="central")
    matrix_payload = mut.get("matrix") or result.get("matrix") or {}
    if isinstance(matrix_payload, dict):
        matrix_payload = {**matrix_payload, "workspace": ws}
    return {
        **result,
        "workspace": ws,
        "matrix": matrix_payload,
        "affected_files": mut.get("affected_files") or [],
        "results": list(result.get("results") or []),
        "warnings": list(result.get("warnings") or []),
    }


_DEFAULT_REDIRECT = "https://deoudegracht.nl/banking-callback.html"


_FOLDER_NAME_MAX = 40


def _valid_folder_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"Invalid folder name: {name!r}")
    if not all(c.isalnum() or c in "_-" for c in cleaned):
        raise ValueError(
            f"Folder name must be alphanumeric/underscore/hyphen: {name!r}"
        )
    if len(cleaned) > _FOLDER_NAME_MAX:
        raise ValueError(f"Folder name too long (max {_FOLDER_NAME_MAX}): {name!r}")
    return cleaned


def create_person(
    workspace: str,
    *,
    folder: str,
    account_name: str,
    country: str = "NL",
    aspsp: str = "ING",
    redirect_url: str | None = None,
) -> dict[str, Any]:
    """Scaffold a person pack under ``workspaces/<ws>/<folder>/``."""
    from app.people import list_people
    from app.settings import refresh_people

    folder_name = _valid_folder_name(folder)
    holder = (account_name or "").strip()
    if not holder:
        raise ValueError("account_name is required")
    country_s = (country or "NL").strip().upper()
    if len(country_s) != 2:
        raise ValueError(f"country must be ISO alpha-2: {country!r}")
    aspsp_s = (aspsp or "ING").strip()
    if not aspsp_s:
        raise ValueError("aspsp is required")
    redirect = (redirect_url or _DEFAULT_REDIRECT).strip()
    if not redirect.startswith("https://"):
        raise ValueError("redirect_url must be https://…")

    with _workspace_scope(workspace) as ws:
        root = store.workspace_dir(ws)
        target = root / folder_name
        if target.exists():
            raise ValueError(f"Folder already exists: {folder_name}")
        for pack in list_people(root):
            if pack.folder_name.lower() == folder_name.lower():
                raise ValueError(f"Person already exists: {folder_name}")

        secret_dir = target / "secret"
        secret_dir.mkdir(parents=True, exist_ok=False)
        (secret_dir / store.PERSONAL_CATEGORIES).write_text("{}\n", encoding="utf-8")
        from app.paths import shared_categories_path

        ensure_year_folder(target, current_year(), categories_path=shared_categories_path())

        profile = {
            "person": folder_name,
            "app_id": "",
            "key_file": "",
            "country": country_s,
            "aspsp": aspsp_s,
            "redirect_url": redirect,
            "account_name": holder,
        }
        (secret_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        refresh_people()

    return {
        "ok": True,
        "workspace": ws,
        "folder": folder_name,
        "person": folder_name,
        "account_name": holder,
        "profile": profile,
        "enable_banking_url": "https://enablebanking.com/cp/applications",
    }


def upload_person_pem(
    workspace: str,
    short: str,
    *,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    """Store the Enable Banking private key and update profile app_id from the filename."""
    from pathlib import Path

    from app.people import get_person
    from app.settings import refresh_people

    person = _valid_folder_name(short)
    name = Path(filename).name
    if not name.lower().endswith(".pem"):
        raise ValueError("PEM upload must be a .pem file")
    stem = Path(name).stem.strip()
    if not stem:
        raise ValueError("PEM filename stem (Application ID) is empty")
    if not content or b"PRIVATE KEY" not in content:
        raise ValueError("File does not look like an RSA private key PEM")

    with _workspace_scope(workspace) as ws:
        pack = get_person(person)
        secret = pack.secret_dir
        secret.mkdir(parents=True, exist_ok=True)
        for old in secret.glob("*.pem"):
            old.unlink(missing_ok=True)
        pem_path = secret / f"{stem}.pem"
        pem_path.write_bytes(content)

        profile_path = pack.profile_path
        if not profile_path.is_file():
            profile_path = secret / "profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            profile = {}
        profile["person"] = person
        profile["app_id"] = stem
        profile["key_file"] = pem_path.name
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        refresh_people()

    return {
        "ok": True,
        "workspace": ws,
        "person": person,
        "folder": pack.folder_name,
        "app_id": stem,
        "key_file": pem_path.name,
        "profile": profile,
    }


def bootstrap_person_fetch(
    workspace: str,
    short: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """First fetch after PEM install (defaults: 1 Jan current year … today)."""
    from datetime import date

    today = date.today()
    start = date_from or f"{today.year}-01-01"
    end = date_to or today.isoformat()
    return refresh_person(
        workspace,
        short,
        date_from=start,
        date_to=end,
        new_year=True,
    )
