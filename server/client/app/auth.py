"""Login users + signed session cookies for multi-user client BFF."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from shared.passwords import hash_password, verify_password
from shared.user_access import (
    ACCESS_LOCAL,
    ACCESS_PERSONAL,
    ACCESS_REGIONAL_ADMIN,
    deduce_access,
    parse_workspaces,
)

COOKIE_NAME = "boekhouding_session"
SESSION_TTL_SEC = 12 * 3600

__all__ = [
    "COOKIE_NAME",
    "SESSION_TTL_SEC",
    "auth_enabled",
    "authenticate",
    "cookie_kwargs",
    "decode_session",
    "encode_session",
    "hash_password",
    "profile_from_user",
    "session_secret",
    "verify_password",
]


def config_dir() -> Path:
    from app.runtime import exe_dir, is_frozen, project_root

    env = os.environ.get("CLIENT_CONFIG", "").strip()
    if env:
        return Path(env).resolve().parent
    if is_frozen():
        return exe_dir()
    return project_root() / "dist"


def read_client_file_cfg() -> dict[str, Any]:
    from app.centrale_sync import config_path

    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def auth_enabled() -> bool:
    raw = os.environ.get("CLIENT_AUTH", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return bool(read_client_file_cfg().get("auth_enabled", False))


def session_secret() -> str:
    env = os.environ.get("CLIENT_SESSION_SECRET", "").strip()
    if env:
        return env
    secret = str(read_client_file_cfg().get("session_secret") or "").strip()
    if secret:
        return secret
    return "dev-insecure-boekhouding-session-secret"


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Verify credentials against the hub SQLite user store."""
    from app.centrale_sync import hub_request, load_base_settings

    if not (username or "").strip() or not password:
        return None
    base = load_base_settings()
    if not base.get("enabled"):
        return None
    try:
        data = hub_request(
            "POST",
            "/api/auth/login",
            body={"username": username.strip(), "password": password},
            timeout=15.0,
        )
    except RuntimeError:
        return None
    user = data.get("user") if isinstance(data, dict) else None
    return user if isinstance(user, dict) else None


def profile_from_user(user: dict[str, Any]) -> dict[str, Any]:
    person = str(user.get("person") or "").strip()
    workspaces_raw = user.get("workspaces")
    if isinstance(workspaces_raw, list):
        workspaces = [str(w).strip() for w in workspaces_raw if str(w).strip()]
    else:
        workspaces = parse_workspaces(str(user.get("workspace") or ""))
    access = deduce_access(person=person, workspaces=workspaces)
    username = str(user.get("username") or "").strip()

    if access == ACCESS_PERSONAL:
        workspace = workspaces[0] if workspaces else parse_workspaces(str(user.get("workspace") or ""))[0]
    elif access == ACCESS_LOCAL:
        workspace = workspaces[0]
    else:
        workspace = workspaces[0] if workspaces else ""

    return {
        "username": username,
        "access": access,
        "workspace": workspace,
        "workspaces": workspaces,
        "person": person,
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def encode_session(payload: dict[str, Any], *, secret: str | None = None) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + SESSION_TTL_SEC
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    key = (secret or session_secret()).encode("utf-8")
    sig = hmac.new(key, raw, hashlib.sha256).digest()
    return f"{_b64url(raw)}.{_b64url(sig)}"


def decode_session(token: str, *, secret: str | None = None) -> dict[str, Any] | None:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64url_decode(raw_b64)
        sig = _b64url_decode(sig_b64)
        key = (secret or session_secret()).encode("utf-8")
        expected = hmac.new(key, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        exp = int(data.get("exp") or 0)
        if exp < int(time.time()):
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError, OSError):
        return None


def cookie_kwargs(*, clear: bool = False) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
    if clear:
        base["value"] = ""
        base["max_age"] = 0
    else:
        base["max_age"] = SESSION_TTL_SEC
    return base
