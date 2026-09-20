"""Garmin Connect session handling.

The MCP server never logs in with a password. Credentials are exchanged for
OAuth tokens once, interactively, by ``garmin-mcp login``; the server only ever
resumes from that token store. MFA cannot be answered over stdio, so a server
that tried to log in would simply hang.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import anyio.to_thread
from garminconnect import Garmin, GarminConnectAuthenticationError

DEFAULT_TOKENSTORE = "~/.garminconnect"


class NotLoggedIn(RuntimeError):
    """Raised when no usable token store is present."""


def tokenstore_path() -> Path:
    """Where tokens live. ``GARMINTOKENS`` matches the garminconnect convention."""
    return Path(os.getenv("GARMINTOKENS", DEFAULT_TOKENSTORE)).expanduser()


_client: Garmin | None = None
_lock = threading.Lock()


def _connect() -> Garmin:
    store = tokenstore_path()
    if not store.exists():
        raise NotLoggedIn(
            f"No Garmin tokens at {store}. Run `garmin-mcp login` once to create them."
        )
    client = Garmin()
    try:
        client.login(str(store))
    except GarminConnectAuthenticationError as exc:
        raise NotLoggedIn(
            f"Garmin tokens at {store} were rejected ({exc}). "
            "They expire after about a year, or when the password changes — "
            "run `garmin-mcp login` again."
        ) from exc
    return client


def client() -> Garmin:
    """The process-wide logged-in client, created on first use."""
    global _client
    with _lock:
        if _client is None:
            _client = _connect()
        return _client


def reset() -> None:
    """Drop the cached client so the next call reconnects."""
    global _client
    with _lock:
        _client = None


async def call(method: str, *args, **kwargs):
    """Run a blocking ``Garmin`` method off the event loop.

    One retry on auth failure: a token refresh can fail on a long-lived client
    while the stored refresh token is still good, and reconnecting fixes it.
    """

    def _invoke():
        return getattr(client(), method)(*args, **kwargs)

    try:
        return await anyio.to_thread.run_sync(_invoke)
    except GarminConnectAuthenticationError:
        reset()
        return await anyio.to_thread.run_sync(_invoke)
