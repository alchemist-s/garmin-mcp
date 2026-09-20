"""Command line entry point: log in, check status, or serve MCP over stdio."""

from __future__ import annotations

import argparse
import getpass
import shutil
import sys
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from . import connection


def _prompt_mfa() -> str:
    return input("Garmin MFA code: ").strip()


def _login(args: argparse.Namespace) -> int:
    store = connection.tokenstore_path()

    if store.exists() and not args.force:
        print(f"Tokens already exist at {store}.")
        print("Re-run with --force to replace them, or `garmin-mcp status` to check them.")
        return 0

    email = args.email or input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
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
        print("Run `garmin-mcp login`.")
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


def _serve(args: argparse.Namespace) -> int:
    from .server import mcp

    mcp.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="garmin-mcp", description="Garmin Connect MCP server."
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

    serve = sub.add_parser("serve", help="run the MCP server on stdio (default)")
    serve.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # No subcommand: MCP clients launch the bare command expecting stdio.
        args.func = _serve
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
