"""SQLite-backed login users."""
from __future__ import annotations

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
_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    title TEXT,
    workspace TEXT,
    person TEXT,
    selected_workspace TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_person_workspace
    ON users(person COLLATE NOCASE, workspace COLLATE NOCASE);
"""


def users_db_path() -> Path:
    env = os.environ.get("HUB_USERS_DB", "").strip()
    if env:
        return Path(env).resolve()
    return (data_root() / USERS_DB_FILENAME).resolve()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": str(row["username"] or ""),
        "password_hash": str(row["password_hash"] or ""),
        "title": str(row["title"] or ""),
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
    conn.commit()
    _CONN = conn
    return conn


def init_user_store() -> Path:
    """Open the database and ensure schema exists."""
    with _LOCK:
        _connect()
        return users_db_path()


def find_user(username: str) -> dict[str, Any] | None:
    needle = (username or "").strip()
    if not needle:
        return None
    with _LOCK:
        init_user_store()
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
        init_user_store()
        rows = _connect().execute(
            "SELECT * FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
    return [_public_user(_row_to_user(row)) for row in rows]


def upsert_user(
    *,
    username: str,
    password_hash: str,
    title: str = "",
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
    title_s = (title or "").strip() or None
    ws = (workspace or "").strip() or None
    person_s = (person or "").strip() or None
    selected = (selected_workspace or "").strip() or None
    now = _utc_now()
    with _LOCK:
        init_user_store()
        conn = _connect()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, title = ?, workspace = ?, person = ?,
                    selected_workspace = ?, updated_at = ?
                WHERE id = ?
                """,
                (token, title_s, ws, person_s, selected, now, int(row["id"])),
            )
        else:
            conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, title, workspace, person, selected_workspace, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, token, title_s, ws, person_s, selected, now, now),
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
        init_user_store()
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
