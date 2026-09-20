"""Command line entry point: log in, check status, or serve MCP over stdio."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from . import connection


NO_TTY_HELP = """Logging in needs an interactive terminal, and this shell has none.

Either run it in a real terminal:

    cd {project} && uv run zonetwo login

or supply credentials through the environment (only works if your Garmin
account has MFA turned off, since an MFA code cannot be prompted for here):

    GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=... uv run zonetwo login
"""


def _interactive() -> bool:
    return sys.stdin.isatty()


def _prompt_mfa() -> str:
    if not _interactive():
        raise GarminConnectAuthenticationError(
            "This account requires an MFA code, which needs an interactive "
            "terminal. Run `zonetwo login` from a real terminal."
        )
    return input("Garmin MFA code: ").strip()


def _login(args: argparse.Namespace) -> int:
    store = connection.tokenstore_path()

    if store.exists() and not args.force:
        print(f"Tokens already exist at {store}.")
        print("Re-run with --force to replace them, or `zonetwo status` to check them.")
        return 0

    email = args.email or os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        if not _interactive():
            # Prompting here would raise EOFError and print a traceback, which
            # tells the reader nothing about what to do instead.
            print(
                NO_TTY_HELP.format(project=Path(__file__).resolve().parents[2]),
                file=sys.stderr,
            )
            return 1
        email = email or input("Garmin email: ").strip()
        password = password or getpass.getpass("Garmin password: ")

    if not email or not password:
        print("Email and password are both required.", file=sys.stderr)
        return 1

    if store.exists():
        shutil.rmtree(store)
    store.mkdir(parents=True, exist_ok=True)

    client = Garmin(email, password, is_cn=args.china, prompt_mfa=_prompt_mfa)
    try:
        # Authenticate against the credentials directly rather than via
        # Garmin.login(tokenstore), which would try the stale token store first.
        client.client.login(email, password, prompt_mfa=_prompt_mfa)
        client.client.dump(str(store))
    except GarminConnectAuthenticationError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1

    store.chmod(0o700)
    for token in store.glob("*.json"):
        token.chmod(0o600)

    verified = Garmin()
    verified.login(str(store))
    print(f"Logged in as {verified.get_full_name() or email}.")
    print(f"Tokens written to {store} (they last about a year).")
    return 0


def _status(args: argparse.Namespace) -> int:
    store = connection.tokenstore_path()
    if not store.exists():
        print(f"Not logged in — no token store at {store}.")
        print("Run `zonetwo login`.")
        return 1
    try:
        client = connection.client()
    except connection.NotLoggedIn as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Logged in as {client.get_full_name() or 'unknown'}.")
    print(f"Token store: {store}")
    last = client.get_device_last_used() or {}
    if last.get("lastUsedDeviceName"):
        print(f"Last synced device: {last['lastUsedDeviceName']}")
    return 0


def _logout(args: argparse.Namespace) -> int:
    store = connection.tokenstore_path()
    if not store.exists():
        print("Already logged out.")
        return 0
    shutil.rmtree(store)
    print(f"Removed {store}.")
    return 0


# Tools that need no arguments, plus the activity tools once an ID is known.
# Several depend on device features (HRV, pulse ox), so "no data" is a pass.
_SIMPLE_CHECKS = [
    "garmin_whoami",
    "garmin_briefing",
    "garmin_devices",
    "garmin_daily_summary",
    "garmin_sleep",
    "garmin_heart_rate",
    "garmin_hrv",
    "garmin_stress",
    "garmin_body_battery",
    "garmin_steps",
    "garmin_spo2",
    "garmin_respiration",
    "garmin_intensity_minutes",
    "garmin_training_readiness",
    "garmin_training_status",
    "garmin_vo2max",
    "garmin_race_predictions",
    "garmin_personal_records",
    "garmin_activities",
    "garmin_activities_by_date",
    "garmin_last_activity",
    "garmin_weight",
]
_ACTIVITY_CHECKS = ["garmin_activity", "garmin_activity_splits", "garmin_activity_weather"]


def _preview(result: object, width: int = 88) -> str:
    import json

    payload = getattr(result, "structured_content", None)
    if payload is None:
        content = getattr(result, "content", None) or []
        payload = getattr(content[0], "text", "") if content else ""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _is_blank(result: object, preview: str) -> bool:
    """True when a tool succeeded but had nothing to report.

    Tools signal "the device does not record this" with a lone ``note`` key;
    reporting that as ok would overstate what the account actually returned.
    """
    import json

    if preview in {"", "{}", "[]", "null"}:
        return True
    try:
        payload = json.loads(_preview(result, width=10_000))
    except (ValueError, TypeError):
        return False
    if isinstance(payload, dict):
        if "result" in payload and payload["result"] in ([], {}, None):
            return True
        # A response carrying only the echoed date, or only an explanatory
        # note, told us nothing about the account.
        if not {k for k in payload if k not in {"note", "date"}}:
            return True
    return False


async def _check_tools(date: str | None) -> int:
    from mcp.server.mcpserver.exceptions import ToolError

    from .server import mcp

    plan: list[tuple[str, dict]] = []
    for name in _SIMPLE_CHECKS:
        arguments: dict = {}
        if date and name not in {
            "garmin_whoami",
            "garmin_devices",
            "garmin_race_predictions",
            "garmin_personal_records",
            "garmin_last_activity",
            "garmin_activities",
        }:
            arguments = {"date": date} if name not in {
                "garmin_body_battery",
                "garmin_steps",
                "garmin_activities_by_date",
                "garmin_weight",
            } else {"end": date}
        plan.append((name, arguments))

    failures = 0
    empty = 0
    activity_id = None

    for name, arguments in plan:
        try:
            result = await mcp.call_tool(name, arguments)
        except ToolError as exc:
            print(f"  {name:28} FAIL  {exc}")
            failures += 1
            continue
        except Exception as exc:  # noqa: BLE001 - a check run reports, never crashes
            print(f"  {name:28} FAIL  {type(exc).__name__}: {exc}")
            failures += 1
            continue

        preview = _preview(result)
        if _is_blank(result, preview):
            print(f"  {name:28} none  (no data for this date)")
            empty += 1
        else:
            print(f"  {name:28} ok    {preview}")

        if name == "garmin_last_activity" and activity_id is None:
            import json

            with __import__("contextlib").suppress(Exception):
                raw = _preview(result, width=10_000)
                activity_id = str(json.loads(raw).get("activityId") or "") or None

    if activity_id:
        for name in _ACTIVITY_CHECKS:
            try:
                result = await mcp.call_tool(name, {"activity_id": activity_id})
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:28} FAIL  {type(exc).__name__}: {exc}")
                failures += 1
                continue
            preview = _preview(result)
            if _is_blank(result, preview):
                print(f"  {name:28} none  (not recorded for this activity)")
                empty += 1
            else:
                print(f"  {name:28} ok    {preview}")
    else:
        print(f"  {'(activity tools)':28} skip  no recent activity to test against")

    total = len(plan) + (len(_ACTIVITY_CHECKS) if activity_id else 0)
    print(f"\n{total - failures}/{total} tools responded ({empty} with no data), {failures} failed.")
    return 1 if failures else 0


def _check(args: argparse.Namespace) -> int:
    import anyio

    store = connection.tokenstore_path()
    if not store.exists():
        print(f"Not logged in — no token store at {store}. Run `zonetwo login`.", file=sys.stderr)
        return 1
    print(f"Calling every read-only tool against the live account ({store}):\n")
    return anyio.run(_check_tools, args.date)


def _serve(args: argparse.Namespace) -> int:
    from .server import mcp

    mcp.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zonetwo", description="Garmin Connect MCP server."
    )
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser("login", help="authenticate once and cache OAuth tokens")
    login.add_argument("--email", help="Garmin account email (prompted if omitted)")
    login.add_argument("--force", action="store_true", help="replace existing tokens")
    login.add_argument("--china", action="store_true", help="use the Garmin China endpoints")
    login.set_defaults(func=_login)

    status = sub.add_parser("status", help="check whether the cached tokens still work")
    status.set_defaults(func=_status)

    logout = sub.add_parser("logout", help="delete the cached tokens")
    logout.set_defaults(func=_logout)

    check = sub.add_parser(
        "check", help="call every read-only tool against the live account"
    )
    check.add_argument(
        "--date", help="date to query (default: today, which may be partially synced)"
    )
    check.set_defaults(func=_check)

    serve = sub.add_parser("serve", help="run the MCP server on stdio (default)")
    serve.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # No subcommand: MCP clients launch the bare command expecting stdio.
        args.func = _serve
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
