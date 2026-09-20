"""Credential sign-in, including the two-step MFA handshake a desktop install needs."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from zonetwo import connection, server


class FakeInner:
    def __init__(self):
        self.dumped = None

    def dump(self, path):
        from pathlib import Path

        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "garmin_tokens.json").write_text("{}")
        self.dumped = path
        # Freshly minted tokens are good: stop rejecting token logins, exactly
        # as the real service would once new tokens replace the stale ones.
        FakeGarmin.token_login_fails = False


class FakeGarmin:
    """Stands in for garminconnect.Garmin across the login paths."""

    instances = []
    needs_mfa = False
    token_login_fails = False

    def __init__(self, email=None, password=None, **kwargs):
        self.email = email
        self.password = password
        self.client = FakeInner()
        self.resumed_with = None
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        if tokenstore is not None:
            if FakeGarmin.token_login_fails:
                from garminconnect import GarminConnectAuthenticationError

                raise GarminConnectAuthenticationError("expired")
            return None, None
        return ("needs_mfa", None) if FakeGarmin.needs_mfa else (None, None)

    def resume_login(self, _state, code):
        if code != "425201":
            raise RuntimeError("bad code")
        self.resumed_with = code

    def get_full_name(self):
        return "Al Tester"


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    FakeGarmin.instances = []
    FakeGarmin.needs_mfa = False
    FakeGarmin.token_login_fails = False
    monkeypatch.setattr(connection, "Garmin", FakeGarmin)
    monkeypatch.setattr(connection, "tokenstore_path", lambda: tmp_path / "tokens")
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    connection.reset()
    yield
    connection.reset()


def test_env_credentials_sign_in_without_a_token_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_EMAIL", "al@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")

    client = connection.client()

    assert isinstance(client, FakeGarmin)
    assert (tmp_path / "tokens" / "garmin_tokens.json").exists()


def test_mfa_pauses_the_login_and_says_what_to_do(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "al@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")
    FakeGarmin.needs_mfa = True

    with pytest.raises(connection.MFARequired, match="one-time code to al@example.com"):
        connection.client()


def test_code_completes_the_login_and_saves_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_EMAIL", "al@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")
    FakeGarmin.needs_mfa = True

    with pytest.raises(connection.MFARequired):
        connection.client()

    assert connection.submit_mfa_code("425201") == "Al Tester"
    assert (tmp_path / "tokens" / "garmin_tokens.json").exists()
    # The pending client is consumed, and the session is now usable.
    assert connection.client() is not None


def test_a_wrong_code_is_rejected_with_guidance(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "al@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")
    FakeGarmin.needs_mfa = True
    with pytest.raises(connection.MFARequired):
        connection.client()

    with pytest.raises(connection.NotLoggedIn, match="30 minutes"):
        connection.submit_mfa_code("000000")


def test_a_code_with_no_login_waiting_says_so():
    with pytest.raises(connection.NoPendingLogin, match="triggers the login"):
        connection.submit_mfa_code("425201")


def test_expired_tokens_fall_back_to_credentials(monkeypatch, tmp_path):
    store = tmp_path / "tokens"
    store.mkdir(parents=True)
    (store / "garmin_tokens.json").write_text("{}")
    FakeGarmin.token_login_fails = True
    monkeypatch.setenv("GARMIN_EMAIL", "al@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")

    assert connection.client() is not None  # re-minted rather than giving up


def test_expired_tokens_without_credentials_ask_for_the_cli(monkeypatch, tmp_path):
    store = tmp_path / "tokens"
    store.mkdir(parents=True)
    (store / "garmin_tokens.json").write_text("{}")
    FakeGarmin.token_login_fails = True

    with pytest.raises(connection.NotLoggedIn, match="zonetwo login"):
        connection.client()


@pytest.mark.anyio
async def test_mfa_prompt_reaches_the_model_as_actionable_text(monkeypatch):
    """The SDK strips non-ToolError text, so this must arrive intact."""
    monkeypatch.setenv("GARMIN_EMAIL", "al@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")
    FakeGarmin.needs_mfa = True

    with pytest.raises(ToolError, match="garmin_submit_mfa_code"):
        await server.garmin_whoami()
