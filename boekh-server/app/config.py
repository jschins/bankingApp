"""Application settings, loaded from environment / .env file.

See deploy/.env.example for the full list of variables.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    # Directory holding one JSON folder per person.
    storage_dir: Path = Path("./storage")

    # Shared raw passphrase. If provided, it is hashed before authorization.
    server_api_passphrase: str = Field("", env=["SERVER_API_PASSPHRASE"])
    # Optional direct hashed key. When set, it is used directly.
    api_key: str = Field("", env=["API_KEY"])

    def get_api_key_hash(self) -> str:
        """Return the SHA256 hash of the passphrase.

        If the configured value already looks like a SHA-256 hex digest, it is
        returned directly for compatibility with existing hashed configs.
        """
        if self.api_key:
            if len(self.api_key) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", self.api_key):
                return self.api_key.lower()
            return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()
        if self.server_api_passphrase:
            return hashlib.sha256(self.server_api_passphrase.encode("utf-8")).hexdigest()
        return ""


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
