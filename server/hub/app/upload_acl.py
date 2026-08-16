"""IP- and token-scoped file uploads into the hub data root."""
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
    id: str
    label: str
    token: str
    ips: tuple[str, ...]  # empty = any IP (token still required)
    paths: tuple[str, ...]  # prefixes or exact paths under data_root


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


def _normalize_prefix(raw: str) -> str:
    """Allow directory prefixes (trailing slash kept as marker via ending /)."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("empty path rule")
    trailing = text.endswith("/")
    body = _normalize_rel(text.rstrip("/"))
    return body + ("/" if trailing else "")


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
    (except the bank consent callback) must come from one of those addresses.
    ``127.0.0.1`` is always included. Empty / missing ``hub_ips`` → no hub-wide gate.
    """
    raw = load_acl_document().get("hub_ips")
    if not isinstance(raw, list) or not raw:
        return frozenset()
    ips = {_normalize_ip(str(x)) for x in raw if str(x).strip()}
    ips.discard("")
    # Drop placeholder examples so a copied template does not lock the hub open wrongly.
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
        if not isinstance(item, dict):
            continue
        # Require the key; explicit "" is a valid empty token (IP + path only).
        if "token" not in item:
            continue
        token = str(item.get("token") or "")
        gid = str(item.get("id") or "").strip() or secrets.token_hex(4)
        ips_raw = item.get("ips") or []
        ips: list[str] = []
        if isinstance(ips_raw, list):
            for ip in ips_raw:
                n = _normalize_ip(str(ip))
                if n and "x" not in n.lower():
                    ips.append(n)
        paths_raw = item.get("paths") or []
        paths: list[str] = []
        if isinstance(paths_raw, list):
            for p in paths_raw:
                try:
                    paths.append(_normalize_prefix(str(p)))
                except ValueError:
                    continue
        if not paths:
            continue
        out.append(
            UploadGrant(
                id=gid,
                label=str(item.get("label") or gid).strip() or gid,
                token=token,
                ips=tuple(ips),
                paths=tuple(paths),
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


def _local_machine_ips() -> set[str]:
    """IPv4 addresses of this host (LAN, Tailscale, loopback)."""
    import socket

    found = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            found.add(_normalize_ip(info[4][0]))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            found.add(_normalize_ip(sock.getsockname()[0]))
    except OSError:
        pass
    found.discard("")
    return found


def grant_allows_ip(grant: UploadGrant, ip: str) -> bool:
    if not grant.ips:
        return True
    client = client_ip(ip)
    allowed = set(grant.ips)
    if client in allowed:
        return True
    # Browser on the hub machine → request IP is 127.0.0.1, not Tailscale/LAN.
    if client == "127.0.0.1" and (_local_machine_ips() & allowed):
        return True
    return False


def ip_matches_any_grant(ip: str) -> bool:
    """True if this client IP is allowed by at least one upload grant."""
    for grant in load_grants():
        if grant_allows_ip(grant, ip):
            return True
    return False


def is_upload_http_path(path: str) -> bool:
    """Upload UI + upload API only (not the rest of the hub)."""
    p = path or ""
    return p == "/upload" or p.startswith("/api/upload")


def path_allowed(grant: UploadGrant, rel_path: str) -> bool:
    target = _normalize_rel(rel_path)
    for rule in grant.paths:
        if rule.endswith("/"):
            prefix = rule.rstrip("/")
            if target == prefix or target.startswith(prefix + "/"):
                return True
        elif target == rule:
            return True
    return False


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
    """Validate grant/IP/path and write into ``data_root``."""
    if not grant_allows_ip(grant, ip):
        raise PermissionError(f"IP {client_ip(ip)!r} is not allowed for grant {grant.id!r}")
    # If only a filename was chosen in the browser, require an exact single-file rule
    # or a destination path from the form.
    dest = (rel_path or "").strip()
    if not dest and filename:
        # Map bare filename onto the sole exact file rule, if unique.
        exact = [p for p in grant.paths if not p.endswith("/")]
        if len(exact) == 1 and Path(exact[0]).name == Path(filename).name:
            dest = exact[0]
        else:
            raise ValueError("destination path is required")
    if not dest:
        raise ValueError("destination path is required")
    if not path_allowed(grant, dest):
        raise PermissionError(f"Path {dest!r} is not allowed for grant {grant.id!r}")

    rel = _normalize_rel(dest)
    full = resolve_under_data_root(rel)
    full.parent.mkdir(parents=True, exist_ok=True)

    from app import store

    # Prefer store.put_file for tracked workspace JSON so clients get events / recalc.
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
                "grant": grant.id,
                "result": {
                    "ok": result.get("ok"),
                    "revision": result.get("revision"),
                    "unchanged": result.get("unchanged"),
                    "path": result.get("path"),
                },
            }
        except ValueError:
            # Not a tracked sync file — fall through to raw write.
            pass

    full.write_bytes(content)
    return {
        "ok": True,
        "path": rel,
        "bytes": len(content),
        "via": "raw",
        "grant": grant.id,
    }


def ensure_example_acl() -> Path:
    """Create a commented example file if missing (does not enable uploads)."""
    path = acl_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "hub_ips": [
            "127.0.0.1",
            "100.87.15.71",
        ],
        "grants": [
            {
                "id": "example",
                "label": "Example — edit token, ips, and paths before use",
                "token": "CHANGE-ME-" + secrets.token_urlsafe(16),
                "ips": ["127.0.0.1", "100.87.15.71"],
                "paths": [
                    "jl/example_person/data/",
                    "jl/example_person/data/downloaded_transactions.json",
                ],
            }
        ],
    }
    path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    return path
