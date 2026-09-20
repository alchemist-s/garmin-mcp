"""Garmin answers 429 rather than queuing, so calls must back off and retry."""

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import connection


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr(connection, "RETRY_BASE_DELAY_S", 0.0)
    monkeypatch.setattr(connection, "RETRY_JITTER_S", 0.0)


class FlakyClient:
    """Raises `failures` rate-limit errors, then succeeds."""

    def __init__(self, failures, error=None):
        self.remaining = failures
        self.attempts = 0
        self.error = error or GarminConnectTooManyRequestsError("429")

    def get_sleep_data(self, cdate):
        self.attempts += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.error
        return {"ok": cdate}


@pytest.mark.anyio
async def test_rate_limit_is_retried_until_it_succeeds(monkeypatch):
    client = FlakyClient(failures=2)
    monkeypatch.setattr(connection, "client", lambda: client)

    assert await connection.call("get_sleep_data", "2026-03-04") == {"ok": "2026-03-04"}
    assert client.attempts == 3


@pytest.mark.anyio
async def test_rate_limit_gives_up_after_the_attempt_budget(monkeypatch):
    client = FlakyClient(failures=99)
    monkeypatch.setattr(connection, "client", lambda: client)

    with pytest.raises(GarminConnectTooManyRequestsError):
        await connection.call("get_sleep_data", "2026-03-04")
    assert client.attempts == connection.RETRY_ATTEMPTS


@pytest.mark.anyio
async def test_rate_limit_message_reaches_the_model(monkeypatch):
    """A 429 that survives retries must arrive as actionable text, not 'Error executing tool'."""
    from mcp.server.mcpserver.exceptions import ToolError

    from garmin_mcp import server

    client = FlakyClient(failures=99)
    monkeypatch.setattr(connection, "client", lambda: client)

    with pytest.raises(ToolError, match="rate-limiting"):
        await server.garmin_sleep(date="2026-03-04")


@pytest.mark.anyio
async def test_auth_failure_reconnects_once(monkeypatch):
    calls = {"connects": 0}

    class ExpiredThenFine:
        def __init__(self):
            self.first = True

        def get_sleep_data(self, cdate):
            if self.first:
                self.first = False
                raise GarminConnectAuthenticationError("token expired")
            return {"ok": True}

    shared = ExpiredThenFine()

    def fake_client():
        calls["connects"] += 1
        return shared

    monkeypatch.setattr(connection, "client", fake_client)

    assert await connection.call("get_sleep_data", "2026-03-04") == {"ok": True}
    assert calls["connects"] >= 2  # reconnected after the auth error


@pytest.mark.anyio
async def test_concurrent_calls_are_capped(monkeypatch):
    """More callers than slots must not all be in flight at once."""
    import threading

    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class Counting:
        def get_sleep_data(self, cdate):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            try:
                import time

                time.sleep(0.02)
                return {"ok": True}
            finally:
                with lock:
                    state["now"] -= 1

    monkeypatch.setattr(connection, "client", lambda: Counting())

    import anyio

    async with anyio.create_task_group() as tg:
        for _ in range(8):
            tg.start_soon(connection.call, "get_sleep_data", "2026-03-04")

    assert state["peak"] <= connection.MAX_CONCURRENT_CALLS
