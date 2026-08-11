"""Client for centraleBoekhouding lock + selected-file sync."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime import app_root

CATEGORIZED = "categorized_transactions.json"
PERSONAL_CATEGORIES = "personal_categories.json"
SHARED_CATEGORIES = "categories.json"

_saw_central_lock = False
_local_session_active = False
_last_error: str | None = None


@dataclass(frozen=True)
class CentraleConfig:
    url: str
    workspace: str
    api_key: str
    enabled: bool


def load_config() -> CentraleConfig:
    """Resolve sync settings from env and optional ``lokale_config.json`` beside the deploy root."""
    cfg_path = app_root() / "lokale_config.json"
    file_cfg: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            file_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            file_cfg = {}

    url = (
        os.environ.get("CENTRALE_URL", "").strip()
        or str(file_cfg.get("centrale_url") or "").strip()
        or "http://127.0.0.1:8400"
    ).rstrip("/")
    workspace = (
        os.environ.get("LOKALE_WORKSPACE", "").strip()
        or str(file_cfg.get("workspace") or "").strip()
        or "dkg"
    )
    api_key = (
        os.environ.get("CENTRALE_API_KEY", "").strip()
        or str(file_cfg.get("api_key") or "").strip()
    )
    enabled_raw = os.environ.get("CENTRALE_SYNC", "").strip().lower()
    if enabled_raw in ("0", "false", "off", "no"):
        enabled = False
    elif enabled_raw in ("1", "true", "on", "yes"):
        enabled = True
    else:
        enabled = bool(file_cfg.get("enabled", True))
    return CentraleConfig(url=url, workspace=workspace, api_key=api_key, enabled=enabled)


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    cfg = load_config()
    url = f"{cfg.url}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def lock_status() -> dict[str, Any]:
    """Poll centrale lock; maintain sticky warning until local logout after seeing central lock."""
    global _saw_central_lock, _last_error
    cfg = load_config()
    if not cfg.enabled:
        return {
            "enabled": False,
            "central_admin_logged_in": False,
            "local_session_active": _local_session_active,
            "show_overwrite_warning": False,
            "workspace": cfg.workspace,
            "centrale_url": cfg.url,
            "error": None,
        }
    try:
        remote = _request("GET", "/api/lock", timeout=5.0)
        _last_error = None
        if remote.get("central_admin_logged_in"):
            _saw_central_lock = True
        show = bool(remote.get("central_admin_logged_in")) or (
            _saw_central_lock and _local_session_active
        )
        return {
            "enabled": True,
            "central_admin_logged_in": bool(remote.get("central_admin_logged_in")),
            "local_sessions": remote.get("local_sessions") or [],
            "local_session_active": _local_session_active,
            "show_overwrite_warning": show,
            "workspace": cfg.workspace,
            "centrale_url": cfg.url,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — surface any transport/JSON failure to UI
        _last_error = str(exc)
        show = _saw_central_lock and _local_session_active
        return {
            "enabled": True,
            "central_admin_logged_in": False,
            "local_session_active": _local_session_active,
            "show_overwrite_warning": show,
            "workspace": cfg.workspace,
            "centrale_url": cfg.url,
            "error": _last_error,
        }


def collect_local_files() -> dict[str, Any]:
    root = app_root()
    categories_path = root / SHARED_CATEGORIES
    categories = _read_json(categories_path)
    people: dict[str, Any] = {}
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or not (child / "data").is_dir():
            continue
        data = child / "data"
        people[child.name] = {
            "categorized_transactions": _read_json(data / CATEGORIZED),
            "personal_categories": _read_json(data / PERSONAL_CATEGORIES),
        }
    return {"categories": categories, "people": people}


def apply_remote_files(payload: dict[str, Any]) -> None:
    root = app_root()
    if payload.get("categories") is not None:
        _write_json(root / SHARED_CATEGORIES, payload["categories"])
    people = payload.get("people") or {}
    if not isinstance(people, dict):
        return
    for person, files in people.items():
        if not isinstance(files, dict):
            continue
        safe = Path(person).name
        data_dir = root / safe / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        if files.get("categorized_transactions") is not None:
            _write_json(data_dir / CATEGORIZED, files["categorized_transactions"])
        if files.get("personal_categories") is not None:
            _write_json(data_dir / PERSONAL_CATEGORIES, files["personal_categories"])


def login_and_pull() -> dict[str, Any]:
    """Register local session and pull selected files from centrale."""
    global _local_session_active, _saw_central_lock, _last_error
    cfg = load_config()
    if not cfg.enabled:
        _local_session_active = True
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    ws = cfg.workspace
    try:
        _request("POST", f"/api/local/{ws}/login")
        files = _request("GET", f"/api/local/{ws}/files")
        apply_remote_files(files)
        _local_session_active = True
        # If central already logged in at pull time, sticky warning starts immediately.
        remote = _request("GET", "/api/lock", timeout=5.0)
        if remote.get("central_admin_logged_in"):
            _saw_central_lock = True
        _last_error = None
        return {"ok": True, "workspace": ws, "people": list((files.get("people") or {}).keys())}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        _local_session_active = True  # still allow offline local work
        return {"ok": False, "error": _last_error, "workspace": ws}


def logout_and_push() -> dict[str, Any]:
    """Push selected files to centrale and end local session."""
    global _local_session_active, _saw_central_lock, _last_error
    cfg = load_config()
    if not cfg.enabled:
        _local_session_active = False
        _saw_central_lock = False
        return {"ok": True, "skipped": True, "reason": "sync disabled"}
    ws = cfg.workspace
    try:
        payload = collect_local_files()
        _request("PUT", f"/api/local/{ws}/files", body=payload)
        _request("POST", f"/api/local/{ws}/logout")
        _last_error = None
        result: dict[str, Any] = {"ok": True, "workspace": ws}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        result = {"ok": False, "error": _last_error, "workspace": ws}
    _local_session_active = False
    _saw_central_lock = False
    return result


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
