"""Client for the bankingApp-server storage API.

bankingApp-server stores one folder per person and exposes:

  GET /data/{person}/{name}    return a stored JSON file
  PUT /data/{person}/{name}    store a JSON file under a person

guarded by a single shared key sent as ``Authorization: Bearer <key>`` (only
when the server has a key configured).

The family-member executable uses this to PUT a small *consent record* (session
id + account uids + identification hashes + validity), and the admin side uses
it to GET that record back so the freshly-minted account uids can drive a normal
``fetch``. No bank transaction data passes through here.
"""
from __future__ import annotations

import json
from typing import Any

import requests


class ServerError(RuntimeError):
    """Raised when the storage server rejects a request (or is unreachable)."""


class bankingAppServerClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 30) -> None:
        if not base_url:
            raise ServerError("No server URL configured (server_url / bankingApp_SERVER_URL).")
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    def is_reachable(self) -> bool:
        """Quick liveness check against the server's open health endpoint."""
        try:
            return requests.get(f"{self._base}/", timeout=self._timeout).ok
        except requests.RequestException:
            return False

    def put_json(self, person: str, name: str, data: Any) -> int:
        """Store ``data`` as ``{person}/{name}`` on the server; return byte size."""
        url = f"{self._base}/data/{person}/{name}"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            resp = requests.put(
                url, data=body, headers=self._headers(), timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise ServerError(f"PUT {url} failed: {exc}") from exc
        if not resp.ok:
            raise ServerError(f"PUT {url} failed: {resp.status_code} {resp.text}")
        return len(body)

    def get_json(self, person: str, name: str) -> Any:
        """Return the parsed ``{person}/{name}`` JSON, or ``None`` if absent."""
        url = f"{self._base}/data/{person}/{name}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self._timeout)
        except requests.RequestException as exc:
            raise ServerError(f"GET {url} failed: {exc}") from exc
        if resp.status_code == 404:
            return None
        if not resp.ok:
            raise ServerError(f"GET {url} failed: {resp.status_code} {resp.text}")
        return resp.json() if resp.content else None
