"""SQLite-backed login users (replaces users.json)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.passwords import hash_password, verify_password
from shared.user_access import enrich_user_record, parse_workspaces

from app.runtime import data_root

USERS_DB_FILENAME = "users.db"
USERS_JSON_FILENAME = "users.json"
_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    workspace TEXT,
    person TEXT,
    selected_workspace TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_person_workspace
    ON users(person COLLATE NOCASE, workspace COLLATE NOCASE);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row[1]) for row in rows]


def _migrate_drop_access_column(conn: sqlite3.Connection) -> None:
    """Recreate ``users`` without the legacy ``access`` column."""
    if "access" not in _table_columns(conn, "users"):
        return
    conn.executescript(
        """
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            workspace TEXT,
            person TEXT,
            selected_workspace TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO users_new
            (id, username, password_hash, workspace, person, selected_workspace, created_at, updated_at)
        SELECT id, username, password_hash, workspace, person, selected_workspace, created_at, updated_at
        FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        CREATE INDEX IF NOT EXISTS idx_users_person_workspace
            ON users(person COLLATE NOCASE, workspace COLLATE NOCASE);
        """
    )


def _legacy_fields_from_json(item: dict[str, Any]) -> tuple[str, str, str]:
    """Map legacy ``users.json`` rows (optional ``access``) to workspace/person fields."""
    workspace = str(item.get("workspace") or "").strip()
    person = str(item.get("person") or "").strip()
    selected = str(item.get("selected_workspace") or "").strip()
    if workspace or person:
        return workspace, person, selected

    access = str(item.get("access") or "").strip().lower()
    if access in ("personal",):
        return workspace, person, selected
    if access in ("local",):
        return workspace, person, selected
    if access in ("regional", "regional_admin", "central"):
        return "", person, selected
    if access:
        try:
            from app.upload_acl import country_workspace_map

            country_ws = country_workspace_map().get(access) or []
            if country_ws:
                return ",".join(country_ws), person, selected
        except Exception:
            pass
    return workspace, person, selected


def users_db_path() -> Path:
    env = os.environ.get("HUB_USERS_DB", "").strip()
    if env:
        return Path(env).resolve()
    return (data_root() / USERS_DB_FILENAME).resolve()


def _legacy_users_json_paths() -> list[Path]:
    env = os.environ.get("HUB_USERS_JSON", "").strip()
    if env:
        return [Path(env).resolve()]
    root = data_root()
    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in (
        root / USERS_JSON_FILENAME,
        root.parent / "client" / "dist" / USERS_JSON_FILENAME,
    ):
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        paths.append(candidate)
    return paths


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": str(row["username"] or ""),
        "password_hash": str(row["password_hash"] or ""),
        "workspace": str(row["workspace"] or ""),
        "person": str(row["person"] or ""),
        "selected_workspace": str(row["selected_workspace"] or ""),
    }


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return enrich_user_record(user)


def _connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = users_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate_drop_access_column(conn)
    conn.commit()
    _CONN = conn
    return conn


def import_users_json(path: Path | None = None) -> int:
    """Import or update users from a legacy ``users.json`` file."""
    paths = [path.resolve()] if path else _legacy_users_json_paths()
    imported = 0
    for json_path in paths:
        if not json_path.is_file():
            continue
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        users = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(users, list):
            continue
        for item in users:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            password_hash = str(item.get("password_hash") or "").strip()
            if not username or not password_hash:
                continue
            workspace, person, selected = _legacy_fields_from_json(item)
            upsert_user(
                username=username,
                password_hash=password_hash,
                workspace=workspace,
                person=person,
                selected_workspace=selected,
            )
            imported += 1
    return imported


def init_user_store(*, migrate_json: bool = True) -> Path:
    """Open the database and optionally import legacy ``users.json`` once."""
    with _LOCK:
        conn = _connect()
        count = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if migrate_json and count == 0:
            migrated = _migrate_from_json(conn)
            if migrated:
                conn.commit()
        return users_db_path()


def _migrate_from_json(conn: sqlite3.Connection) -> int:
    imported = 0
    now = _utc_now()
    for path in _legacy_users_json_paths():
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        users = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(users, list):
            continue
        for item in users:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            password_hash = str(item.get("password_hash") or "").strip()
            if not username or not password_hash:
                continue
            workspace, person, selected = _legacy_fields_from_json(item)
            workspace_val = workspace or None
            person_val = person or None
            selected_val = selected or None
            conn.execute(
                """
                INSERT OR IGNORE INTO users
                    (username, password_hash, workspace, person, selected_workspace, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (username, password_hash, workspace_val, person_val, selected_val, now, now),
            )
            imported += 1
    return imported


def find_user(username: str) -> dict[str, Any] | None:
    needle = (username or "").strip()
    if not needle:
        return None
    with _LOCK:
        init_user_store(migrate_json=False)
        row = _connect().execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (needle,),
        ).fetchone()
    return _row_to_user(row) if row else None


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = find_user(username)
    if user is None:
        return None
    encoded = str(user.get("password_hash") or "")
    if not encoded or not verify_password(password, encoded):
        return None
    return user


def authenticate_public(username: str, password: str) -> dict[str, Any] | None:
    user = authenticate(username, password)
    return _public_user(user) if user else None


def list_users() -> list[dict[str, Any]]:
    with _LOCK:
        init_user_store(migrate_json=False)
        rows = _connect().execute(
            "SELECT * FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
    return [_public_user(_row_to_user(row)) for row in rows]


def upsert_user(
    *,
    username: str,
    password_hash: str,
    workspace: str = "",
    person: str = "",
    selected_workspace: str = "",
) -> dict[str, Any]:
    name = (username or "").strip()
    if not name:
        raise ValueError("username is required")
    token = (password_hash or "").strip()
    if not token:
        raise ValueError("password_hash is required")
    ws = (workspace or "").strip() or None
    person_s = (person or "").strip() or None
    selected = (selected_workspace or "").strip() or None
    now = _utc_now()
    with _LOCK:
        init_user_store(migrate_json=False)
        conn = _connect()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, workspace = ?, person = ?,
                    selected_workspace = ?, updated_at = ?
                WHERE id = ?
                """,
                (token, ws, person_s, selected, now, int(row["id"])),
            )
        else:
            conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, workspace, person, selected_workspace, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, token, ws, person_s, selected, now, now),
            )
        conn.commit()
    user = find_user(name)
    if user is None:
        raise RuntimeError(f"failed to upsert user {name!r}")
    return _public_user(user)


def upsert_personal_login(
    *,
    workspace: str,
    person: str,
    password_hash: str,
) -> dict[str, Any]:
    folder = (person or "").strip()
    ws = (workspace or "").strip()
    if not folder or not ws:
        raise ValueError("workspace and person are required")
    return upsert_user(
        username=folder,
        password_hash=password_hash,
        workspace=ws,
        person=folder,
    )


def password_hash_by_person_center() -> dict[tuple[str, str], str]:
    with _LOCK:
        init_user_store(migrate_json=False)
        rows = _connect().execute(
            """
            SELECT person, workspace, password_hash
            FROM users
            WHERE person IS NOT NULL AND person != ''
              AND workspace IS NOT NULL AND workspace != ''
            """
        ).fetchall()
    out: dict[tuple[str, str], str] = {}
    for row in rows:
        person = str(row["person"] or "").strip()
        raw_ws = str(row["workspace"] or "").strip()
        token = str(row["password_hash"] or "").strip()
        if not person or not token:
            continue
        for center in parse_workspaces(raw_ws) or ([raw_ws] if raw_ws else []):
            if center:
                out[(person, center)] = token
    return out
