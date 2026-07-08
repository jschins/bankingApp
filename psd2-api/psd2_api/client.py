"""Thin client for the Enable Banking API.

Docs: https://enablebanking.com/docs/api/reference/

Authentication uses a JWT signed (RS256) with your application's private RSA
key. You create an application in the Enable Banking Control Panel, which gives
you an Application ID (used as the JWT `kid`) and a private key file.

Account-information flow:
  1. list_aspsps("NL")            -> find the bank (ING NL name is "ING")
  2. start_authorization(...)     -> get a URL the user opens to authenticate
  3. (user authenticates in their ING app; the bank redirects back with ?code=)
  4. create_session(code)         -> returns session_id + linked accounts (uid)
  5. get_transactions(uid)        -> pull transactions as JSON
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import jwt
import requests

BASE_URL = "https://api.enablebanking.com"


class EnableBankingError(RuntimeError):
    """Raised when the Enable Banking API returns an error response."""


class EnableBankingClient:
    def __init__(self, application_id: str, private_key: str | bytes, timeout: float = 30) -> None:
        if not application_id or not private_key:
            raise EnableBankingError(
                "Missing credentials: set ENABLEBANKING_APP_ID and "
                "ENABLEBANKING_KEY_PATH (see .env.example)."
            )
        self._app_id = application_id
        self._private_key = private_key
        self._timeout = timeout

    @classmethod
    def from_key_file(cls, application_id: str, key_path: str, timeout: float = 30) -> "EnableBankingClient":
        path = Path(key_path)
        if not path.exists():
            raise EnableBankingError(f"Private key file not found: {path}")
        return cls(application_id, path.read_bytes(), timeout)

    # --- auth / low-level helpers ------------------------------------------
    def _jwt(self) -> str:
        """Build a short-lived RS256 JWT for the Authorization header."""
        now = int(time.time())
        headers = {"typ": "JWT", "alg": "RS256", "kid": self._app_id}
        payload = {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + 3600,  # max allowed is 24h; 1h is plenty for a CLI run
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256", headers=headers)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._jwt()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{BASE_URL}{path}"
        response = requests.request(
            method, url, headers=self._headers(), timeout=self._timeout, **kwargs
        )
        if not response.ok:
            raise EnableBankingError(
                f"{method} {path} failed: {response.status_code} {response.text}"
            )
        return response.json() if response.content else None

    # --- API methods --------------------------------------------------------
    def list_aspsps(self, country: str | None = None) -> list[dict[str, Any]]:
        query = f"?country={country}" if country else ""
        resp = self._request("GET", f"/aspsps{query}")
        return resp.get("aspsps", []) if isinstance(resp, dict) else []

    def start_authorization(
        self,
        aspsp_name: str,
        country: str,
        redirect_url: str,
        valid_until: str,
        psu_type: str = "personal",
        state: str | None = None,
    ) -> dict[str, Any]:
        """Begin authorization; returns a dict with `url` and `authorization_id`."""
        payload = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": aspsp_name, "country": country},
            "redirect_url": redirect_url,
            "psu_type": psu_type,
            "state": state or uuid.uuid4().hex,
        }
        return self._request("POST", "/auth", json=payload)

    def create_session(self, code: str) -> dict[str, Any]:
        """Exchange the redirect `code` for a session (incl. linked accounts)."""
        return self._request("POST", "/sessions", json={"code": code})

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}")

    def get_account_details(self, account_uid: str) -> dict[str, Any]:
        return self._request("GET", f"/accounts/{account_uid}/details")

    def get_balances(self, account_uid: str) -> dict[str, Any]:
        return self._request("GET", f"/accounts/{account_uid}/balances")

    def get_transactions(
        self,
        account_uid: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return ALL raw transaction objects, following pagination.

        `date_from`/`date_to` are optional ISO dates (YYYY-MM-DD).
        """
        all_transactions: list[dict[str, Any]] = []
        continuation_key: str | None = None
        while True:
            params: dict[str, str] = {}
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            if continuation_key:
                params["continuation_key"] = continuation_key
            resp = self._request(
                "GET", f"/accounts/{account_uid}/transactions", params=params
            )
            all_transactions.extend(resp.get("transactions", []))
            continuation_key = resp.get("continuation_key")
            if not continuation_key:
                break
        return all_transactions
