"""Garmin Connect session handling.

The MCP server never logs in with a password. Credentials are exchanged for
OAuth tokens once, interactively, by ``garmin-mcp login``; the server only ever
resumes from that token store. MFA cannot be answered over stdio, so a server
that tried to log in would simply hang.
"""

from __future__ import annotations

import os
import random
import threading
import time
from pathlib import Path

import anyio.to_thread
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

DEFAULT_TOKENSTORE = "~/.garminconnect"

# Garmin rate-limits per IP and answers 429 rather than queuing. Two defences:
# cap how many calls are in flight at once, and back off when told to.
MAX_CONCURRENT_CALLS = 3
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 2.0
RETRY_JITTER_S = 1.0

# A threading primitive rather than an anyio one: these calls already run in a
# worker thread, and a thread semaphore is not bound to an event loop, so it
# survives the several loops a test session creates.
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)


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


def _invoke(method: str, args: tuple, kwargs: dict):
    """Call a Garmin method, backing off when Garmin says to slow down.

    The semaphore is released before sleeping: holding a slot through the
    backoff would throttle unrelated calls that are not being rate-limited.
    """
    delay = RETRY_BASE_DELAY_S
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        with _slots:
            try:
                return getattr(client(), method)(*args, **kwargs)
            except GarminConnectTooManyRequestsError:
                if attempt == RETRY_ATTEMPTS:
                    raise
        time.sleep(delay + random.uniform(0, RETRY_JITTER_S))
        delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


async def call(method: str, *args, **kwargs):
    """Run a blocking ``Garmin`` method off the event loop.

    One extra attempt on auth failure: a token refresh can fail on a long-lived
    client while the stored refresh token is still good, and reconnecting fixes
    it. Rate-limit backoff is handled inside ``_invoke``, which is already on a
    worker thread and can simply sleep.
    """
    try:
        return await anyio.to_thread.run_sync(_invoke, method, args, kwargs)
    except GarminConnectAuthenticationError:
        reset()
        return await anyio.to_thread.run_sync(_invoke, method, args, kwargs)
