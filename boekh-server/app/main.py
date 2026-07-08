"""FastAPI application entrypoint.

A minimal server that receives and transmits JSON files. Files are stored on
disk in a folder per person (e.g. ``js`` and ``as``):

  - GET  /                         health check (open, no key)
  - GET  /data                     list people that have stored files
  - GET  /data/{person}            list a person's JSON files
  - GET  /data/{person}/{name}     return a stored JSON file
  - PUT  /data/{person}/{name}     store a JSON file under a person
  - DELETE /data/{person}/{name}   delete a stored JSON file

All ``/data`` endpoints require a single shared API key (sent as
``Authorization: Bearer <key>``) when ``API_KEY`` is configured.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import storage
from .config import Settings, get_settings

# Declaring this as a dependency gives Swagger UI the "Authorize" button (top
# right) and a single shared token field, instead of an `authorization`
# parameter on every operation. The token is the SHA-256 hash of the passphrase.
bearer_scheme = HTTPBearer(
    scheme_name="API key",
    description="Paste the SHA-256 hash of the shared passphrase.",
    auto_error=False,
)


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> None:
    # When no passphrase is configured the endpoints are open (local dev only).
    expected_hash = settings.get_api_key_hash()
    if not expected_hash:
        return
    token = credentials.credentials if credentials else ""
    scheme = credentials.scheme if credentials else ""
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


app = FastAPI(title="bankingApp-server", version="0.1")

# The same key guards both reads and writes.
data = APIRouter(prefix="/data", tags=["data"], dependencies=[Depends(require_api_key)])


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "bankingApp-server"}


@data.get("")
def list_people() -> dict[str, list[str]]:
    return {"people": storage.list_people()}


@data.get("/{person}")
def list_files(person: str) -> dict[str, object]:
    return {"person": person, "files": storage.list_json(person)}


@data.get("/{person}/{name}")
def get_file(person: str, name: str) -> Response:
    payload = storage.read_json(person, name)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(content=payload, media_type="application/json")


@data.put("/{person}/{name}", status_code=status.HTTP_201_CREATED)
async def put_file(person: str, name: str, request: Request) -> dict[str, object]:
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty body")
    try:
        json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON: {exc}",
        )
    try:
        stored_name = storage.save_json(person, name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"person": person, "name": stored_name, "size": len(payload)}


@data.delete("/{person}/{name}")
def delete_file(person: str, name: str) -> dict[str, object]:
    if not storage.delete_json(person, name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {"person": person, "name": name, "deleted": True}


app.include_router(data)
