"""Login users + signed session cookies for multi-user client BFF."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from app.runtime import exe_dir, is_frozen, normalize_access, project_root

COOKIE_NAME = "boekhouding_session"
SESSION_TTL_SEC = 12 * 3600
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def config_dir() -> Path:
    env = os.environ.get("CLIENT_CONFIG", "").strip()
    if env:
        return Path(env).resolve().parent
    if is_frozen():
        return exe_dir()
    return project_root() / "dist"


def users_path() -> Path:
    env = os.environ.get("CLIENT_USERS", "").strip()
    if env:
        return Path(env)
    return config_dir() / "users.json"


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
    # Dev fallback — set session_secret in client_config for real deploys.
    return "dev-insecure-boekhouding-session-secret"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    dig = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt_bytes).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(dig).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n_s, r_s, p_s, salt_b64, hash_b64 = encoded.split("$", 5)
        if kind != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.urlsafe_b64decode(salt_b64 + "==")
        expected = base64.urlsafe_b64decode(hash_b64 + "==")
        got = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError, OSError):
        return False


def load_users() -> list[dict[str, Any]]:
    path = users_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, list):
        return []
    return [u for u in users if isinstance(u, dict)]


def find_user(username: str) -> dict[str, Any] | None:
    needle = username.strip().lower()
    if not needle:
        return None
    for user in load_users():
        if str(user.get("username") or "").strip().lower() == needle:
            return user
    return None


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = find_user(username)
    if user is None:
        return None
    encoded = str(user.get("password_hash") or "")
    if not encoded or not verify_password(password, encoded):
        return None
    return user


def profile_from_user(user: dict[str, Any]) -> dict[str, Any]:
    access = normalize_access(str(user.get("access") or "local"))
    workspace = str(user.get("workspace") or "").strip()
    person = str(user.get("person") or "").strip()
    selected = str(user.get("selected_workspace") or "").strip()
    return {
        "username": str(user.get("username") or "").strip(),
        "access": access,
        "workspace": workspace,
        "person": person,
        "selected_workspace": selected,
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
