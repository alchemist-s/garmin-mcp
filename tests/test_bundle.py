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


def test_record_edits_are_off_by_default_in_the_bundle():
    """Scheduling workouts is always on; editing recorded history is not."""
    assert _manifest()["user_config"]["enable_record_edits"]["default"] is False


def test_the_bundle_wires_the_write_flag_the_server_reads():
    env = _manifest()["server"]["mcp_config"]["env"]
    assert env["ZONETWO_ENABLE_WRITES"] == "${user_config.enable_record_edits}"


def test_entry_point_exists_and_is_listed():
    entry = _manifest()["server"]["entry_point"]
    assert (ROOT / entry).is_file()


def test_privacy_policy_is_declared_over_https():
    """The Connectors Directory rejects bundles without a reachable privacy policy."""
    policies = _manifest().get("privacy_policies")
    assert policies, "manifest must declare privacy_policies"
    assert all(url.startswith("https://") for url in policies)


def test_readme_has_a_privacy_section():
    assert "## Privacy" in (ROOT / "README.md").read_text()


def test_site_pages_exist_for_the_policy_link():
    assert (ROOT / "site" / "index.html").is_file()
    assert (ROOT / "site" / "privacy.html").is_file()
