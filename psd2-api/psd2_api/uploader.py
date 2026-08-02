"""bankingApp-server storage client — re-exported from shared library.

See :mod:`shared.server_client` for the canonical implementation.
"""
from __future__ import annotations

from shared.server_client import (  # noqa: F401
    ServerClient,
    ServerError,
)

# Backwards-compatible alias used throughout psd2-api.
bankingAppServerClient = ServerClient

__all__ = ["ServerError", "ServerClient", "bankingAppServerClient"]
