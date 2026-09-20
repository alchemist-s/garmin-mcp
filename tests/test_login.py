"""Login must fail legibly when there is no terminal to prompt at."""

import argparse
from pathlib import Path

import pytest

from garmin_mcp import cli


def _args(**overrides):
    defaults = {"email": None, "force": True, "china": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def temp_store(monkeypatch, tmp_path):
    store = tmp_path / "tokens"
    monkeypatch.setattr(cli.connection, "tokenstore_path", lambda: store)
    return store


def test_login_without_a_tty_explains_itself(monkeypatch, capsys, temp_store):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)

    exit_code = cli._login(_args())

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "needs an interactive terminal" in err
    assert "GARMIN_EMAIL" in err
    assert not temp_store.exists()  # nothing half-written


def test_login_reads_credentials_from_the_environment(monkeypatch, temp_store):
    """No TTY is fine when credentials come from the environment."""
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setenv("GARMIN_EMAIL", "someone@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2")

    seen = {}

    class FakeClient:
        def login(self, email, password, prompt_mfa=None):
            seen["credentials"] = (email, password)

        def dump(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)
            (Path(path) / "oauth1_token.json").write_text("{}")

    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            self.client = FakeClient()

        def login(self, tokenstore):
            return None, None

        def get_full_name(self):
            return "Al Tester"

    monkeypatch.setattr(cli, "Garmin", FakeGarmin)

    exit_code = cli._login(_args())

    assert exit_code == 0
    assert seen["credentials"] == ("someone@example.com", "hunter2")
    assert (temp_store / "oauth1_token.json").exists()


def test_mfa_prompt_without_a_tty_is_an_auth_error(monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    with pytest.raises(cli.GarminConnectAuthenticationError, match="interactive terminal"):
        cli._prompt_mfa()


def test_existing_tokens_are_not_clobbered_without_force(capsys, temp_store):
    temp_store.mkdir(parents=True)
    (temp_store / "oauth1_token.json").write_text("{}")

    exit_code = cli._login(_args(force=False))

    assert exit_code == 0
    assert "--force" in capsys.readouterr().out
    assert (temp_store / "oauth1_token.json").exists()
