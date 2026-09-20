"""The desktop bundle manifest must stay in step with the package."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _manifest():
    return json.loads((ROOT / "manifest.json").read_text())


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_manifest_version_matches_the_package():
    """A bundle that reports a different version than it runs is a debugging trap."""
    assert _manifest()["version"] == _pyproject()["version"]


def test_bundle_collects_the_credentials_the_server_reads():
    """user_config feeds env vars; the names must match what connection.py looks up."""
    manifest = _manifest()
    env = manifest["server"]["mcp_config"]["env"]
    assert env["GARMIN_EMAIL"] == "${user_config.email}"
    assert env["GARMIN_PASSWORD"] == "${user_config.password}"
    assert set(manifest["user_config"]) >= {"email", "password"}


def test_the_password_field_is_marked_sensitive():
    assert _manifest()["user_config"]["password"]["sensitive"] is True


def test_writes_are_off_by_default_in_the_bundle():
    assert _manifest()["user_config"]["enable_writes"]["default"] is False


def test_entry_point_exists_and_is_listed():
    entry = _manifest()["server"]["entry_point"]
    assert (ROOT / entry).is_file()
