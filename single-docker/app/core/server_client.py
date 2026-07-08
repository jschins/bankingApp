"""Client for the optional bankingApp-server JSON store."""

from __future__ import annotations

import json
from typing import Any

import requests


class ServerError(RuntimeError):
    """Raised when the storage server rejects a request."""


class ServerClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 30) -> None:
        if not base_url:
            raise ServerError("No server_url configured.")
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    def put_json(self, person: str, name: str, data: Any) -> int:
        url = f"{self._base}/data/{person}/{name}"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            resp = requests.put(url, data=body, headers=self._headers(), timeout=self._timeout)
        except requests.RequestException as exc:
            raise ServerError(f"PUT {url} failed: {exc}") from exc
        if not resp.ok:
            raise ServerError(f"PUT {url} failed: {resp.status_code} {resp.text}")
        return len(body)
