"""High-level workspace UI operations (hub-only data; no client copies)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app import store
from app.paths import CALC_LOCK
from app.runtime import set_active_workspace
from app.yearpath import current_year, ensure_year_folder, has_person_layout, parse_year


def _clean_ws(workspace: str) -> str:
    ws = workspace.strip().replace("\\", "/").strip("/")
    if not ws or ".." in ws.split("/") or ws.startswith("/"):
        raise ValueError(f"Invalid workspace: {workspace!r}")
    store.require_workspace_dir(ws)
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


def matrix(workspace: str, *, year: str | None = None, bank: str | None = None) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
        from app.matrix import build_matrix

        payload = build_matrix(year=year, bank=bank)
        payload["workspace"] = ws
        if bank:
            payload["bank_view"] = bank
        return payload


def person_banks(workspace: str, short: str, *, year: str | None = None) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
        from app.core.bank_csv import person_bank_folder_options
        from app.people import get_person

        pack = get_person(short, year=year)
        opts = person_bank_folder_options(
            pack.folder, pack.year, person=pack.folder_name, center=ws
        )
        from app.upload_acl import grant_token_for_person

        token = grant_token_for_person(pack.folder_name, ws)
        return {
            "workspace": ws,
            "person": pack.short,
            "year": pack.year,
            "upload_token": token or "",
            **opts,
        }


def transactions(
    workspace: str,
    short: str,
    category_name: str,
    *,
    year: str | None = None,
    bank: str | None = None,
) -> dict[str, Any]:
    with _workspace_scope(workspace) as ws:
        import app.paths as paths
        from app.core.bank_csv import pack_for_bank_view
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

        pack = get_person(short, year=year)
        pack = pack_for_bank_view(pack, bank, center=ws)
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
    """Seed missing year folders for secret-folder people in ``ws`` (write path).

    Excel-only people get a new year folder only when an upload's first sheet
    entry lands in a year that does not exist yet — never during refresh/import.
    """
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
        if not (child / "secret").is_dir():
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



# Seed password for new personal logins created by add-person (must match client users.json).
_DEFAULT_PERSONAL_PASSWORD_HASH = (
    "scrypt$16384$8$1$DqM8xC0un6VYeM0i4FwKcQ$sUhw7V7Wfd4Rz0PB9RoWEHVIVcNpNId2GM5QIU-8_fQ"
)


def _users_json_targets() -> list[Path]:
    """Hub + client copies of users.json that should receive new personal logins."""
    from app.runtime import data_root
    from app.upload_acl import users_json_path

    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in (
        users_json_path(),
        data_root() / "users.json",
        data_root().parent / "client" / "dist" / "users.json",
    ):
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        paths.append(candidate)
    return paths


def ensure_personal_login_user(*, workspace: str, person: str) -> dict[str, Any]:
    """Upsert a personal login (username=person, password changeme) into users.json copies."""
    ws = _clean_ws(workspace)
    folder = _valid_folder_name(person)
    username = folder
    entry = {
        "username": username,
        "password_hash": _DEFAULT_PERSONAL_PASSWORD_HASH,
        "access": "personal",
        "workspace": ws,
        "person": folder,
    }
    written: list[str] = []
    for path in _users_json_targets():
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = {"users": []}
        except (OSError, json.JSONDecodeError):
            raw = {"users": []}
        if not isinstance(raw, dict):
            raw = {"users": []}
        users = raw.get("users")
        if not isinstance(users, list):
            users = []
        updated = False
        next_users: list[Any] = []
        for item in users:
            if not isinstance(item, dict):
                next_users.append(item)
                continue
            same_user = str(item.get("username") or "").strip().lower() == username.lower()
            same_person = (
                str(item.get("person") or "").strip().lower() == folder.lower()
                and str(item.get("workspace") or "").strip().lower() == ws.lower()
            )
            if same_user or same_person:
                next_users.append({**item, **entry})
                updated = True
            else:
                next_users.append(item)
        if not updated:
            next_users.append(entry)
        raw["users"] = next_users
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    return {
        "username": username,
        "password": "changeme",
        "workspace": ws,
        "person": folder,
        "users_json": written,
    }


def _pem_profile_template(
    *,
    person: str,
    country: str,
    aspsp: str,
    app_id: str = "",
) -> dict[str, Any]:
    """Canonical profile shape: ``person`` + ``connections[]`` only (see anton_schins)."""
    conn: dict[str, Any] = {
        "aspsp": aspsp,
        "country": country,
        "accounts": [],
    }
    app_id = str(app_id or "").strip()
    if app_id:
        conn = {"app_id": app_id, **conn}
    return {"person": person, "connections": [conn]}


def _set_profile_app_id(profile: dict[str, Any], app_id: str) -> dict[str, Any]:
    connections = profile.get("connections")
    if not isinstance(connections, list):
        connections = []
    aspsp = ""
    country = ""
    conn: dict[str, Any] | None = None
    for item in connections:
        if not isinstance(item, dict):
            continue
        item_aspsp = str(item.get("aspsp") or "").strip()
        item_country = str(item.get("country") or "").strip()
        if item_aspsp and item_country:
            aspsp, country = item_aspsp, item_country
            conn = item
            break
    if not aspsp:
        aspsp = str(profile.get("aspsp") or "ING")
    if not country:
        country = str(profile.get("country") or "NL")
    if conn is None:
        conn = {"aspsp": aspsp, "country": country, "accounts": []}
        connections.append(conn)
    conn["app_id"] = app_id
    profile["connections"] = connections
    for key in ("app_id", "key_file", "redirect_url", "aspsp", "country", "account_name"):
        profile.pop(key, None)
    return profile


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
    account_name: str = "",
    mode: str = "pem",
    country: str = "NL",
    aspsp: str = "ING",
    initial_balance: str | None = None,
    account_number: str | None = None,
) -> dict[str, Any]:
    """Scaffold a person pack under an *existing* ``workspaces/<ws>/<folder>/``.

    The workspace folder must already exist on disk; this never creates it.
    """
    from app.people import list_people
    from app.settings import refresh_people
    from app.yearpath import CATEGORY_TOTALS_FILENAME

    folder_name = _valid_folder_name(folder)
    mode_s = (mode or "pem").strip().lower()
    if mode_s not in {"pem", "excel"}:
        raise ValueError("mode must be 'pem' or 'excel'")
    holder = (account_name or "").strip()
    if mode_s == "excel" and not holder:
        raise ValueError("account holder name is required")
    account_no = (account_number or "").strip()
    country_s = (country or "NL").strip().upper()
    if len(country_s) != 2:
        raise ValueError(f"country must be ISO alpha-2: {country!r}")
    aspsp_s = (aspsp or "ING").strip()
    if not aspsp_s:
        raise ValueError("aspsp is required")

    with _workspace_scope(workspace) as ws:
        root = store.require_workspace_dir(ws)
        target = root / folder_name
        if target.exists():
            raise ValueError(f"Folder already exists: {folder_name}")
        for pack in list_people(root):
            if pack.folder_name.lower() == folder_name.lower():
                raise ValueError(f"Person already exists: {folder_name}")

        from app.paths import shared_categories_path

        ensure_year_folder(
            target,
            current_year(),
            categories_path=shared_categories_path(),
            include_downloaded=mode_s != "excel",
        )
        if mode_s == "excel":
            # Excel mode: no secret folder; keep zeroed categories and seed opening balance.
            amount_text = str(initial_balance or "0").strip().replace(",", ".")
            try:
                amount = float(amount_text)
            except ValueError as exc:
                raise ValueError(f"invalid initial_balance: {initial_balance!r}") from exc
            totals_path = target / current_year() / CATEGORY_TOTALS_FILENAME
            totals = json.loads(totals_path.read_text(encoding="utf-8"))
            if not isinstance(totals, dict):
                totals = {}
            balances = totals.get("account_balances")
            if not isinstance(balances, list) or not balances:
                balances = [
                    {
                        "iban": "onbekend",
                        "name": holder or "onbekend",
                        "currency": "EUR",
                        "balance": "0.00",
                        "files": [],
                    }
                ]
            first = balances[0] if isinstance(balances[0], dict) else {}
            if not isinstance(first, dict):
                first = {}
            first["iban"] = account_no or "onbekend"
            first["name"] = holder
            first["balance"] = f"{amount:.2f}"
            balances[0] = first
            totals["account_balances"] = balances
            totals_path.write_text(json.dumps(totals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            refresh_people()
            login = ensure_personal_login_user(workspace=ws, person=folder_name)
            return {
                "ok": True,
                "workspace": ws,
                "folder": folder_name,
                "person": folder_name,
                "mode": "excel",
                "account_holder": holder,
                "account_number": account_no or "onbekend",
                "initial_balance": f"{amount:.2f}",
                "login": login,
            }

        secret_dir = target / "secret"
        secret_dir.mkdir(parents=True, exist_ok=False)
        (secret_dir / store.PERSONAL_CATEGORIES).write_text("{}\n", encoding="utf-8")
        profile = _pem_profile_template(
            person=folder_name,
            country=country_s,
            aspsp=aspsp_s,
        )
        (secret_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        refresh_people()
        login = ensure_personal_login_user(workspace=ws, person=folder_name)

    return {
        "ok": True,
        "workspace": ws,
        "folder": folder_name,
        "person": folder_name,
        "mode": "pem",
        "profile": profile,
        "login": login,
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
        profile = _set_profile_app_id(profile, stem)
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
