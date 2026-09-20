import pytest

from garmin_mcp import connection


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StubGarmin:
    """Stands in for a logged-in Garmin client; records calls, returns canned payloads.

    A payload that is an Exception instance is raised instead of returned, which
    is how tests cover Garmin's intermittent 5xx responses.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if isinstance(value, Exception):
                raise value
            return value

        return method


@pytest.fixture
def stub(monkeypatch):
    """Install a StubGarmin as the process-wide client."""

    def install(responses):
        client = StubGarmin(responses)
        monkeypatch.setattr(connection, "client", lambda: client)
        return client

    return install
