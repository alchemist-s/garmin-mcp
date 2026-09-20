"""Garmin Connect session handling.

The MCP server never logs in with a password. Credentials are exchanged for
OAuth tokens once, interactively, by ``zonetwo login``; the server only ever
resumes from that token store. MFA cannot be answered over stdio, so a server
that tried to log in would simply hang.
"""

from __future__ import annotations

import contextlib
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
    """Raised when there is neither a usable token store nor credentials."""


class MFARequired(RuntimeError):
    """Raised when Garmin has emailed a code and is waiting for it.

    The half-authenticated client is held in memory: ``resume_login`` completes
    from state on the client object (its ``_client_state`` argument is ignored),
    so the same instance must survive until the code arrives. Garmin's codes
    last 30 minutes, which is ample for a round trip through the conversation.
    """


class NoPendingLogin(RuntimeError):
    """Raised when an MFA code arrives but no login is waiting for one."""


def tokenstore_path() -> Path:
    """Where tokens live. ``GARMINTOKENS`` matches the garminconnect convention."""
    return Path(os.getenv("GARMINTOKENS", DEFAULT_TOKENSTORE)).expanduser()


_client: Garmin | None = None
_pending: Garmin | None = None
_lock = threading.RLock()


def _credentials() -> tuple[str | None, str | None]:
    """Credentials supplied by the environment, as a desktop bundle provides them."""
    return os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD")


def _harden(store: Path) -> None:
    with contextlib.suppress(OSError):
        store.chmod(0o700)
        for token in store.glob("*.json"):
            token.chmod(0o600)


def _finalise(client: Garmin, store: Path) -> Garmin:
    """Persist tokens, then reload from them.

    Reloading is not ceremony: an early return from the MFA branch skips
    loading the profile, and ``displayName`` is required by most endpoints. A
    fresh client built from the token store is always fully populated.
    """
    store.mkdir(parents=True, exist_ok=True)
    client.client.dump(str(store))
    _harden(store)
    fresh = Garmin()
    fresh.login(str(store))
    return fresh


def _login_with_credentials(email: str, password: str, store: Path) -> Garmin:
    global _pending
    client = Garmin(email, password, return_on_mfa=True)
    try:
        status, _ = client.login()
    except GarminConnectAuthenticationError as exc:
        raise NotLoggedIn(f"Garmin rejected those credentials: {exc}") from exc

    if status == "needs_mfa":
        _pending = client
        raise MFARequired(
            f"Garmin has emailed a one-time code to {email}. Call "
            "`garmin_submit_mfa_code` with that code to finish signing in. "
            "The code is valid for 30 minutes."
        )
    return _finalise(client, store)


def _connect() -> Garmin:
    store = tokenstore_path()

    if store.exists():
        client = Garmin()
        try:
            client.login(str(store))
            return client
        except GarminConnectAuthenticationError as exc:
            # Expired or revoked. Fall through to credentials if we have them.
            if not all(_credentials()):
                raise NotLoggedIn(
                    f"Garmin tokens at {store} were rejected ({exc}). They expire "
                    "after about a year, or when the password changes — run "
                    "`zonetwo login` again."
                ) from exc

    email, password = _credentials()
    if email and password:
        return _login_with_credentials(email, password, store)

    raise NotLoggedIn(
        f"No Garmin tokens at {store}. Run `zonetwo login` once to create "
        "them, or set GARMIN_EMAIL and GARMIN_PASSWORD."
    )


def submit_mfa_code(code: str) -> str:
    """Complete a login that is waiting on an emailed code."""
    global _client, _pending
    with _lock:
        if _pending is None:
            raise NoPendingLogin(
                "No Garmin login is waiting for a code. Ask for some Garmin data "
                "first — that triggers the login and sends a fresh code."
            )
        pending = _pending
        try:
            pending.resume_login(None, code.strip())
        except Exception as exc:
            raise NotLoggedIn(
                f"Garmin rejected that code ({exc}). Codes expire after 30 minutes; "
                "ask for some Garmin data again to trigger a new one."
            ) from exc
        _client = _finalise(pending, tokenstore_path())
        _pending = None
        return _client.get_full_name() or "your Garmin account"


def client() -> Garmin:
    """The process-wide logged-in client, created on first use."""
    global _client
    with _lock:
        if _client is None:
            _client = _connect()
        return _client


def reset() -> None:
    """Drop the cached client so the next call reconnects."""
    global _client, _pending
    with _lock:
        _client = None
        _pending = None


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
