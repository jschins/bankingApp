"""FastAPI multi-person lokale boekhouding admin (syncs with centraleBoekhouding)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.settings import get_people, init_app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.centrale_sync import (
        end_session_and_push,
        load_config,
        start_event_worker,
        start_session_and_pull,
        stop_event_worker,
    )

    cfg = load_config(force_reload=True)
    pull = start_session_and_pull()
    if not pull.get("ok"):
        print(f"WARNING: centrale pull failed: {pull.get('error')}")
    elif pull.get("skipped"):
        print("centrale sync disabled — working offline on local files only")
    else:
        print(f"centrale session+pull ok (workspace={pull.get('workspace')}, role={cfg.role})")
    init_app()
    start_event_worker()
    try:
        yield
    finally:
        stop_event_worker()
        push = end_session_and_push()
        if not push.get("ok"):
            print(f"WARNING: centrale end-session push failed: {push.get('error')}")
        elif not push.get("skipped"):
            print(f"centrale end-session push ok (workspace={push.get('workspace')})")


app = FastAPI(title="lokaleBoekhouding", version="0.2", lifespan=lifespan)


class SettingsTermsRequest(BaseModel):
    terms: list[str] = Field(default_factory=list)


class AddTermRequest(BaseModel):
    category_name: str
    term: str
    general: bool = False
    person: str | None = None


class ModificationRequest(BaseModel):
    transaction: dict[str, Any]


class RefreshRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None


def _bank_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app.centrale_sync import load_config, sync_status
    from app.runtime import app_root, frontend_dist_ok, is_frozen

    people = get_people()
    cfg = load_config()
    return {
        "ok": True,
        "app": "centraleAdmin" if cfg.role == "central_admin" else "lokaleBoekhouding",
        "app_root": str(app_root()),
        "frozen": is_frozen(),
        "frontend_ok": frontend_dist_ok(),
        "centrale": {
            "url": cfg.url,
            "workspace": cfg.workspace,
            "enabled": cfg.enabled,
            "port": cfg.port,
            "role": cfg.role,
            **{k: v for k, v in sync_status().items() if k not in ("workspace", "centrale_url", "enabled", "port", "role")},
        },
        "people": [
            {"short": p.short, "folder": p.folder_name, "data_dir": str(p.data_dir)}
            for p in people
        ],
    }


@app.get("/api/workspaces")
def api_workspaces() -> dict[str, Any]:
    from app.centrale_sync import list_hub_workspaces, load_config

    cfg = load_config()
    return {
        "workspaces": list_hub_workspaces(),
        "workspace": cfg.workspace,
        "role": cfg.role,
    }


class WorkspaceRequest(BaseModel):
    workspace: str


@app.post("/api/workspace")
def api_set_workspace(body: WorkspaceRequest) -> dict[str, Any]:
    from app.centrale_sync import list_hub_workspaces, load_config, switch_workspace
    from app.settings import init_app

    cfg = load_config()
    if cfg.role != "central_admin":
        raise HTTPException(status_code=400, detail="workspace switch requires central_admin role")
    names = list_hub_workspaces()
    if body.workspace not in names and names:
        raise HTTPException(status_code=404, detail=f"Unknown workspace: {body.workspace!r}")
    result = switch_workspace(body.workspace)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "switch failed")
    try:
        people = init_app()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "workspace": body.workspace,
        "people": [{"short": p.short, "folder": p.folder_name} for p in people],
    }


@app.get("/api/centrale/status")
def api_centrale_status() -> dict[str, Any]:
    from app.centrale_sync import list_hub_workspaces, load_config, poll_central_events, sync_status

    poll_central_events()
    status = sync_status()
    cfg = load_config()
    if cfg.role == "central_admin":
        status["workspaces"] = list_hub_workspaces()
    return status


@app.get("/api/centrale/notifications")
def api_centrale_notifications() -> dict[str, Any]:
    from app.centrale_sync import poll_central_events, pop_notifications

    poll_central_events()
    return pop_notifications()


def _tracked_paths_for_people() -> list[str]:
    """All people categorized_transactions paths (not personal_categories)."""
    return [
        f"{p.folder_name}/data/categorized_transactions.json" for p in get_people()
    ]


def _person_categorized_path(folder_name: str) -> str:
    return f"{folder_name}/data/categorized_transactions.json"


def _person_personal_path(folder_name: str) -> str:
    return f"{folder_name}/data/personal_categories.json"


def _person_tracked_paths(folder_name: str) -> list[str]:
    return [
        _person_categorized_path(folder_name),
        _person_personal_path(folder_name),
    ]

@app.get("/api/people")
def api_people() -> dict[str, Any]:
    return {
        "people": [
            {"short": p.short, "folder": p.folder_name}
            for p in get_people()
        ]
    }


@app.get("/api/matrix")
def api_matrix() -> dict[str, Any]:
    from app.matrix import build_matrix

    return build_matrix()


@app.post("/api/recalculate")
def api_recalculate() -> dict[str, Any]:
    from app.centrale_sync import mark_and_push
    from app.matrix import recalculate_all

    result = recalculate_all()
    mark_and_push(_tracked_paths_for_people())
    return result


@app.post("/api/refresh")
def api_refresh(body: RefreshRequest | None = None) -> dict[str, Any]:
    from app.centrale_sync import load_config, mark_and_push
    from app.matrix import refresh_all

    if load_config().role == "central_admin":
        raise HTTPException(
            status_code=403,
            detail="Bank refresh is not available in central admin (no secrets).",
        )
    req = body or RefreshRequest()
    result = refresh_all(date_from=req.date_from, date_to=req.date_to)
    mark_and_push(_tracked_paths_for_people())
    return result


@app.get("/api/transactions/{short}/{category_name}")
def api_transactions(short: str, category_name: str) -> dict[str, Any]:
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

    try:
        pack = get_person(short)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

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


@app.put("/api/transactions/{short}/modification")
def api_modification(short: str, body: ModificationRequest) -> dict[str, Any]:
    from app.centrale_sync import mark_and_push
    from app.core.categorize import record_modification
    from app.paths import bind_person
    from app.people import get_person

    try:
        pack = get_person(short)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    with bind_person(pack):
        try:
            modified = record_modification(body.transaction)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    mark_and_push([_person_categorized_path(pack.folder_name)])
    return {"transaction": modified, "person": pack.short}


@app.get("/api/settings")
def api_settings() -> dict[str, Any]:
    from app.core.categorize import (
        _category_map,
        _load_json_object,
        category_code_set,
        remainder_category_name,
        type_rules_payload,
    )
    from app.matrix import category_names, load_general_file
    from app.paths import bind_person

    people = get_people()
    general_file = load_general_file(people)
    general = _category_map(general_file)
    personal: dict[str, dict[str, list[str]]] = {}
    typerules: list[dict[str, str]] = []
    codes: list[int] = []
    remainder = ""
    for pack in people:
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
        "categories": category_names(people),
        "people": [{"short": p.short, "folder": p.folder_name} for p in people],
        "general": general,
        "personal": personal,
        "valid_category_codes": codes,
        "remainder_category": remainder,
        "typerules": typerules,
    }


@app.put("/api/settings/{group}/{category_name}")
def api_update_settings(
    group: str, category_name: str, body: SettingsTermsRequest
) -> dict[str, Any]:
    from app.centrale_sync import mark_and_push
    from app.core.categorize import recategorize_transactions
    from app.matrix import recalculate_all, save_general_terms, save_personal_terms
    from app.paths import bind_person
    from app.people import get_person

    if group == "general":
        terms = save_general_terms(category_name, body.terms)
        matrix = recalculate_all()
        # General terms: shared categories + recategorized transactions only.
        mark_and_push(["categories.json"] + _tracked_paths_for_people())
        return {"group": "general", "category": category_name, "terms": terms, "matrix": matrix}

    try:
        pack = get_person(group)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    terms = save_personal_terms(pack.short, category_name, body.terms)
    with bind_person(pack):
        recategorize_transactions()
    from app.matrix import build_matrix
    mark_and_push(_person_tracked_paths(pack.folder_name))

    return {
        "group": pack.short,
        "category": category_name,
        "terms": terms,
        "matrix": build_matrix(),
    }


@app.post("/api/settings/add-term")
def api_add_term(body: AddTermRequest) -> dict[str, Any]:
    from app.centrale_sync import mark_and_push
    from app.core.categorize import append_category_term, recategorize_transactions
    from app.matrix import build_matrix, load_general_file, sync_general_categories
    from app.paths import bind_person
    from app.people import get_person

    people = get_people()
    if body.general:
        pack = people[0]
        with bind_person(pack):
            try:
                terms = append_category_term(
                    body.category_name,
                    body.term,
                    group="general",
                    person=pack.short,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        sync_general_categories(load_general_file([pack]), people)
        for p in people:
            with bind_person(p):
                recategorize_transactions()
        mark_and_push(["categories.json"] + _tracked_paths_for_people())
        return {
            "group": "general",
            "category": body.category_name,
            "term": body.term,
            "terms": terms,
            "matrix": build_matrix(),
        }

    short = (body.person or "").strip()
    if not short:
        raise HTTPException(status_code=400, detail="person is required when general=false")
    try:
        pack = get_person(short)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    with bind_person(pack):
        try:
            terms = append_category_term(
                body.category_name,
                body.term,
                group=pack.short,
                person=pack.short,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        recategorize_transactions()
    mark_and_push(_person_tracked_paths(pack.folder_name))
    return {
        "group": pack.short,
        "category": body.category_name,
        "term": body.term,
        "terms": terms,
        "matrix": build_matrix(),
    }


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
        "Rebuild frontend with: npm run build (in frontend/)\n"
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

    from app.centrale_sync import load_config
    from app.runtime import app_root, is_frozen

    cfg = load_config()
    host = os.environ.get("HOST", "127.0.0.1" if is_frozen() else "0.0.0.0")
    # PORT env wins; else lokale_config.json "port" (dkg=8301, jl=8302, central_admin=8300).
    port = int(os.environ.get("PORT", str(cfg.port)))
    label = "centraleAdmin" if cfg.role == "central_admin" else "lokaleBoekhouding"
    # People discovery happens in lifespan after centrale pull.
    print(f"{label} root: {app_root()} (workspace={cfg.workspace}, port={port})")

    if is_frozen():

        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}/")

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
