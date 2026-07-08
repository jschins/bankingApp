"""Async client for the bankingApp-server storage server.

Talks to the minimal bankingApp-server JSON API:
  - GET    /data                     list people that have stored files
  - GET    /data/{person}            list a person's JSON files
  - GET    /data/{person}/{name}     fetch a stored JSON file
  - DELETE /data/{person}/{name}     delete a stored JSON file

Authentication is a single shared API key sent as ``Authorization: Bearer
<key>`` (only when configured).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

import httpx

from .config import Settings


class StorageError(RuntimeError):
    """Raised when the storage server returns an unexpected response."""


@dataclass
class StoredFile:
    """One JSON file read from storage, identified by ``person/name``."""

    id: str
    person: str
    name: str
    size: int
    content: Any
    # Exact bytes received from the server, so the file can be written to the
    # local disk mirror byte-for-byte.
    raw: bytes


def _bearer_token(key: str) -> str:
    if len(key) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", key):
        return key.lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class StorageClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        headers: dict[str, str] = {}
        if settings.storage_api_key:
            headers["Authorization"] = f"Bearer {_bearer_token(settings.storage_api_key)}"
        self._client = httpx.AsyncClient(
            base_url=settings.storage_base_url.rstrip("/"),
            timeout=settings.request_timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_people(self) -> list[str]:
        resp = await self._client.get("/data")
        if resp.status_code != 200:
            raise StorageError(f"GET /data failed: {resp.status_code} {resp.text}")
        return resp.json().get("people", [])

    async def list_person_files(self, person: str) -> list[str]:
        resp = await self._client.get(f"/data/{person}")
        if resp.status_code != 200:
            raise StorageError(
                f"GET /data/{person} failed: {resp.status_code} {resp.text}"
            )
        return resp.json().get("files", [])

    async def fetch_file(self, person: str, name: str) -> tuple[Any, bytes]:
        resp = await self._client.get(f"/data/{person}/{name}")
        if resp.status_code == 404:
            raise StorageError(f"{person}/{name} not found")
        if resp.status_code != 200:
            raise StorageError(
                f"GET /data/{person}/{name} failed: {resp.status_code} {resp.text}"
            )
        try:
            return resp.json(), resp.content
        except ValueError as exc:  # not valid JSON
            raise StorageError(f"{person}/{name} is not valid JSON: {exc}") from exc

    async def delete_file(self, person: str, name: str) -> None:
        """Delete one file from storage. A missing file (404) is treated as done."""
        resp = await self._client.delete(f"/data/{person}/{name}")
        if resp.status_code not in (200, 404):
            raise StorageError(
                f"DELETE /data/{person}/{name} failed: {resp.status_code} {resp.text}"
            )

    async def load_all(self) -> list[StoredFile]:
        """Read every JSON file for every person currently in storage."""
        files: list[StoredFile] = []
        for person in await self.list_people():
            for name in await self.list_person_files(person):
                content, raw = await self.fetch_file(person, name)
                files.append(
                    StoredFile(
                        id=f"{person}/{name}",
                        person=person,
                        name=name,
                        size=len(raw),
                        content=content,
                        raw=raw,
                    )
                )
        return files
