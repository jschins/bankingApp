"""Client for the optional bankingApp-server JSON store — re-exported from shared library.

See :mod:`shared.server_client` for the canonical implementation.
"""
from __future__ import annotations

from shared.server_client import ServerClient, ServerError  # noqa: F401

__all__ = ["ServerClient", "ServerError"]
