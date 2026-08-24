"""SQLite-backed login users."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from shared.user_access import enrich_user_record, parse_workspaces

from app.runtime import data_root

USERS_DB_FILENAME = "users.db"
_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

FORMAT_SECRET = "secret"
FORMAT_MULTIPLE = "multiple"

# Stable upload-grant token (legacy scrypt string still accepted by /upload?t=…).
DEFAULT_UPLOAD_TOKEN = (
    "scrypt$16384$8$1$DqM8xC0un6VYeM0i4FwKcQ$sUhw7V7Wfd4Rz0PB9RoWEHVIVcNpNId2GM5QIU-8_fQ"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    title TEXT,
    workspace TEXT,
    person TEXT,
    format TEXT,
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


def password_for_username(username: str) -> str:
    """Login password is identical to the username (temporary hard-coded rule)."""
    return str(username or "").strip()


def is_single_bank_format(fmt: str | None) -> bool:
    """True when ``format`` is a concrete bank CSV layout (not empty/secret/multiple)."""
    value = str(fmt or "").strip().lower()
    if not value or value in (FORMAT_SECRET, FORMAT_MULTIPLE):
        return False
    from app.core.bank_csv import is_csv_bank_format

    return is_csv_bank_format(value)


def as_date_only(value: str | None) -> str | None:
    """Normalize a date/datetime string to ``YYYY-MM-DD``, or ``None`` if unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    # ISO datetime / date
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        candidate = text[:10]
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    # App transaction dates DD-MM-YYYY
    if len(text) >= 10 and text[2] == "-" and text[5] == "-":
        day, month, year = text[0:2], text[3:5], text[6:10]
        candidate = f"{year}-{month}-{day}"
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        return None


def latest_transaction_date(transactions: Iterable[Any]) -> str | None:
    """Latest ``date`` among transaction dicts, as ``YYYY-MM-DD``."""
    best: str | None = None
    for item in transactions:
        if not isinstance(item, dict):
            continue
        iso = as_date_only(str(item.get("date") or ""))
        if iso and (best is None or iso > best):
            best = iso
    return best


def _utc_today() -> str:
    """Calendar date in UTC as ``YYYY-MM-DD`` (for ``created_at`` on insert)."""
    return datetime.now(timezone.utc).date().isoformat()


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "username": str(row["username"] or ""),
        "title": str(row["title"] or "") if "title" in keys else "",
        "workspace": str(row["workspace"]) if row["workspace"] is not None else "",
        "person": str(row["person"]) if row["person"] is not None else "",
        "format": (
            str(row["format"]).strip()
            if "format" in keys and row["format"] is not None
            else ""
        ),
    }


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return enrich_user_record(user)


def _strip_timestamps_to_dates(conn: sqlite3.Connection) -> None:
    """Keep ``created_at`` / ``updated_at`` as date-only (``YYYY-MM-DD``)."""
    conn.execute(
        """
        UPDATE users
        SET created_at = substr(created_at, 1, 10)
        WHERE created_at IS NOT NULL AND length(created_at) > 10
        """
    )
    conn.execute(
        """
        UPDATE users
        SET updated_at = substr(updated_at, 1, 10)
        WHERE updated_at IS NOT NULL AND length(updated_at) > 10
        """
    )


def _connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = users_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _strip_timestamps_to_dates(conn)
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
    expected = password_for_username(str(user.get("username") or ""))
    if str(password or "") != expected:
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
    title: str = "",
    workspace: str = "",
    person: str = "",
) -> dict[str, Any]:
    """Insert or update a user. Does not change ``format`` or ``updated_at`` on update."""
    name = (username or "").strip()
    if not name:
        raise ValueError("username is required")
    title_s = (title or "").strip() or None
    ws = (workspace or "").strip() or None
    person_s = (person or "").strip() or None
    today = _utc_today()
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
                SET title = ?, workspace = ?, person = ?
                WHERE id = ?
                """,
                (title_s, ws, person_s, int(row["id"])),
            )
        else:
            conn.execute(
                """
                INSERT INTO users
                    (username, title, workspace, person, format, created_at, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (name, title_s, ws, person_s, today, today),
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
) -> dict[str, Any]:
    folder = (person or "").strip()
    ws = (workspace or "").strip()
    if not folder or not ws:
        raise ValueError("workspace and person are required")
    return upsert_user(
        username=folder,
        workspace=ws,
        person=folder,
    )


def set_user_format(*, username: str, format: str) -> dict[str, Any] | None:
    """Set ``format`` for an existing user (does not change ``updated_at``)."""
    name = (username or "").strip()
    fmt = (format or "").strip()
    if not name or not fmt:
        return None
    with _LOCK:
        init_user_store()
        conn = _connect()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE users SET format = ? WHERE id = ?",
            (fmt, int(row["id"])),
        )
        conn.commit()
    user = find_user(name)
    return _public_user(user) if user else None


def set_user_updated_at(*, username: str, date: str | None) -> dict[str, Any] | None:
    """Set ``updated_at`` to a date-only value after a successful refresh/upload."""
    name = (username or "").strip()
    iso = as_date_only(date)
    if not name or not iso:
        return None
    with _LOCK:
        init_user_store()
        conn = _connect()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE users SET updated_at = ? WHERE id = ?",
            (iso, int(row["id"])),
        )
        conn.commit()
    user = find_user(name)
    return _public_user(user) if user else None


def upload_token_by_person_center() -> dict[tuple[str, str], str]:
    """Map ``(person, center)`` → upload token for personal users."""
    with _LOCK:
        init_user_store()
        rows = _connect().execute(
            """
            SELECT person, workspace
            FROM users
            WHERE person IS NOT NULL AND person != ''
              AND workspace IS NOT NULL AND workspace != ''
            """
        ).fetchall()
    out: dict[tuple[str, str], str] = {}
    for row in rows:
        person = str(row["person"] or "").strip()
        raw_ws = str(row["workspace"] or "").strip()
        if not person:
            continue
        for center in parse_workspaces(raw_ws) or ([raw_ws] if raw_ws else []):
            if center:
                out[(person, center)] = DEFAULT_UPLOAD_TOKEN
    return out
