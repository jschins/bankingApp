"""Application configuration, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- This back-end ---
    host: str = "0.0.0.0"
    port: int = 8100
    # Origins allowed to call this API (the Vite dev server by default).
    # Override with a JSON list in .env, e.g. CORS_ORIGINS=["https://app.example.com"]
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- Storage server (bankingApp-server) we read JSON from ---
    storage_base_url: str = "http://localhost:8000"
    # Shared API key for bankingApp-server. The client sends its SHA-256
    # hash as "Authorization: Bearer <hash>".
    # Leave empty only if bankingApp-server itself runs without a key.
    storage_api_key: str = Field("", env=["STORAGE_API_KEY"])
    request_timeout: float = 30.0

    # --- Local mirror ---
    # The durable local store: distilled "<person>_transactions.json" plus the
    # raw bank files the collector writes here directly (and deletes after
    # distilling). Mirrors a "storage/<person>/<name>" layout.
    local_storage_dir: Path = Path("./storage")

    # --- Bank data collection (psd2-api) ---
    # The psd2-api project is run as a subprocess by POST /api/refresh to fetch
    # each person's bank data straight into local_storage_dir. Path is relative
    # to bankingApp-admin's working directory (the sibling project by default).
    psd2_api_dir: Path = Path("../psd2-api")
    # How far back to fetch on each refresh. Enable Banking allows at most 90
    # days; the distilled file accumulates (merge by id), so a rolling window is
    # enough — older transactions already saved are kept.
    collect_days_back: int = 89
    # Seconds to allow the whole collection (all people) to run.
    collect_timeout: float = 600.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
