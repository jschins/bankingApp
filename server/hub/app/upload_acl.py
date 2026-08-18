"""Token-scoped Excel uploads into a person's data folder."""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime import data_root

ACL_FILENAME = "upload_acl.json"


@dataclass(frozen=True)
class UploadGrant:
    person: str
    token: str
    center: str

    @property
    def data_dir(self) -> str:
        return f"{self.center}/{self.person}/data"


def acl_path() -> Path:
    return data_root() / ACL_FILENAME


def _normalize_ip(raw: str) -> str:
    host = (raw or "").strip()
    if host in ("::1", "0:0:0:0:0:0:0:1"):
        return "127.0.0.1"
    if host.startswith("::ffff:"):
        return host.split("::ffff:", 1)[-1]
    return host


def _normalize_rel(raw: str) -> str:
    p = (raw or "").strip().replace("\\", "/").lstrip("/")
    if not p or p.endswith("/."):
        raise ValueError(f"Invalid path: {raw!r}")
    parts = [x for x in p.split("/") if x and x != "."]
    if not parts or ".." in parts:
        raise ValueError(f"Invalid path: {raw!r}")
    return "/".join(parts)


def load_acl_document() -> dict[str, Any]:
    path = acl_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def hub_allowed_ips() -> frozenset[str]:
    """IPs allowed to reach the hub at all.

    When ``hub_ips`` in ``upload_acl.json`` is a non-empty list, every request
    (except the bank consent callback and ``/upload``) must come from one of
    those addresses. ``127.0.0.1`` is always included.
    Empty / missing ``hub_ips`` → no hub-wide gate.
    """
    raw = load_acl_document().get("hub_ips")
    if not isinstance(raw, list) or not raw:
        return frozenset()
    ips = {_normalize_ip(str(x)) for x in raw if str(x).strip()}
    ips.discard("")
    ips = {ip for ip in ips if "x" not in ip.lower()}
    if not ips:
        return frozenset()
    ips.add("127.0.0.1")
    return frozenset(ips)


def load_grants(*, force: bool = False) -> list[UploadGrant]:
    raw = load_acl_document()
    items = raw.get("grants") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out: list[UploadGrant] = []
    for item in items:
        if not isinstance(item, dict) or "token" not in item:
            continue
        person = str(item.get("person") or "").strip()
        center = str(item.get("center") or "").strip()
        if not person or not center or ".." in person or ".." in center:
            continue
        if "/" in person or "\\" in person or "/" in center or "\\" in center:
            continue
        out.append(
            UploadGrant(
                person=person,
                token=str(item.get("token") or ""),
                center=center,
            )
        )
    return out


def find_grant_by_token(token: str | None) -> UploadGrant | None:
    needle = "" if token is None else str(token)
    for grant in load_grants():
        try:
            if secrets.compare_digest(grant.token, needle):
                return grant
        except ValueError:
            continue
    return None


def client_ip(host: str | None) -> str:
    return _normalize_ip(host or "unknown")


def is_upload_http_path(path: str) -> bool:
    """Upload UI + upload API only (not the rest of the hub)."""
    p = path or ""
    return p == "/upload" or p.startswith("/api/upload")


def path_allowed(grant: UploadGrant, rel_path: str) -> bool:
    target = _normalize_rel(rel_path)
    prefix = grant.data_dir
    return target == prefix or target.startswith(prefix + "/")


def list_grant_xlsx_files(grant: UploadGrant) -> list[str]:
    """Basenames of ``*.xlsx`` already present under the grant data folder."""
    folder = resolve_under_data_root(grant.data_dir)
    if not folder.is_dir():
        return []
    names: list[str] = []
    for path in sorted(folder.glob("*.xlsx"), key=lambda p: p.name.lower()):
        if path.is_file() and not path.name.startswith("~$"):
            names.append(path.name)
    return names


def resolve_under_data_root(rel_path: str) -> Path:
    rel = _normalize_rel(rel_path)
    root = data_root().resolve()
    full = (root / rel).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes data root: {rel_path!r}") from exc
    return full


def save_upload(
    *,
    grant: UploadGrant,
    ip: str,
    rel_path: str,
    content: bytes,
    filename: str | None = None,
) -> dict[str, Any]:
    """Write into ``{center}/{person}/data/{original filename}``."""
    del ip  # uploads are token-gated; any IP may use /upload
    safe_name = Path(filename or "").name if filename else ""
    dest = (rel_path or "").strip()
    if not dest:
        if not safe_name:
            raise ValueError("filename is required")
        dest = f"{grant.data_dir}/{safe_name}"
    elif not Path(dest).name and safe_name:
        dest = dest.rstrip("/") + "/" + safe_name

    if not path_allowed(grant, dest):
        raise PermissionError(f"Path {dest!r} is not allowed for {grant.person}")

    rel = _normalize_rel(dest)
    full = resolve_under_data_root(rel)
    full.parent.mkdir(parents=True, exist_ok=True)

    from app import store

    if rel.endswith(".json"):
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON upload is not valid UTF-8 JSON") from exc
        try:
            if rel == store.SHARED_CATEGORIES:
                result = store.put_file(
                    list(store.list_workspaces() or ["dkg"])[0],
                    store.SHARED_CATEGORIES,
                    payload,
                    source="central",
                )
            else:
                parts = rel.split("/")
                if len(parts) < 2:
                    raise ValueError("workspace JSON path must be workspace/…")
                ws, rest = parts[0], "/".join(parts[1:])
                result = store.put_file(ws, rest, payload, source="central")
            return {
                "ok": True,
                "path": rel,
                "bytes": len(content),
                "via": "store",
                "person": grant.person,
                "result": {
                    "ok": result.get("ok"),
                    "revision": result.get("revision"),
                    "unchanged": result.get("unchanged"),
                    "path": result.get("path"),
                },
            }
        except ValueError:
            pass

    full.write_bytes(content)
    return {
        "ok": True,
        "path": rel,
        "bytes": len(content),
        "via": "raw",
        "person": grant.person,
    }


def ensure_example_acl() -> Path:
    path = acl_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "hub_ips": ["127.0.0.1"],
        "grants": [
            {
                "person": "example_person",
                "token": "CHANGE-ME-" + secrets.token_urlsafe(16),
                "center": "dkg",
            }
        ],
    }
    path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    return path
