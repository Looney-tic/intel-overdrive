"""Auth commands: login (store API key) and status (show key info)."""

from __future__ import annotations

import os

import typer

import cli.config as _cfg
from cli.client import get_api_key, store_api_key
from cli.render import console, print_error, print_json, print_success, print_warning

auth_app = typer.Typer(help="Manage API key authentication")


def _detect_key_tier() -> str:
    """Detect which storage tier the API key is coming from.

    Returns 'keyring', 'environment', or 'config_file'.
    """
    # Tier 1: system keyring
    try:
        import keyring

        key = keyring.get_password(_cfg.KEYRING_SERVICE, _cfg.KEYRING_USERNAME)
        if key:
            return "keyring"
    except Exception:
        pass

    # Tier 2: environment variable
    if os.environ.get("OVERDRIVE_API_KEY"):
        return "environment"

    # Tier 3: config file
    if _cfg.CONFIG_KEY_PATH.exists():
        return "config_file"

    return "unknown"


@auth_app.command("login")
def login(
    key: str = typer.Option(None, "--key", help="API key (skips prompt)"),
) -> None:
    """Authenticate with the Intel Overdrive API.

    If you already have a key: overdrive-intel auth login --key dti_v1_...
    If you need a key: overdrive-intel auth register
    """
    if not key:
        key = typer.prompt("API key", hide_input=True)

    if not key.startswith("dti_v1_"):
        print_error("Invalid key format. Expected 'dti_v1_...'")
        raise typer.Exit(1)

    store_api_key(key)
    print_success("API key stored successfully.")


@auth_app.command("register")
def register(
    email: str = typer.Option(None, "--email", help="Email address"),
    invite_code: str = typer.Option(
        None, "--invite-code", help="Invite code (if required)"
    ),
    api_url: str = typer.Option(
        None, "--api-url", envvar="OVERDRIVE_API_URL", help="API base URL"
    ),
) -> None:
    """Register a new account and store the API key automatically.

    Example: overdrive-intel auth register --email me@example.com --invite-code YOUR_INVITE_CODE
    """
    import httpx

    if not email:
        email = typer.prompt("Email")

    base_url = api_url or _cfg.get_api_url()
    body: dict = {"email": email}
    if invite_code:
        body["invite_code"] = invite_code

    try:
        resp = httpx.post(
            f"{base_url}/v1/auth/register",
            json=body,
            timeout=10.0,
        )
    except httpx.ConnectError:
        print_error(
            f"Cannot connect to {base_url}. "
            "Check OVERDRIVE_API_URL or pass --api-url."
        )
        raise typer.Exit(1)

    if resp.status_code == 201:
        data = resp.json()
        raw_key = data["api_key"]
        store_api_key(raw_key)
        print_success(f"Registered! API key stored securely.")
        console.print(
            f"  Key: [bold]{raw_key[:14]}...[/bold] (full key saved to keyring)"
        )
        console.print(f"  Next: [dim]overdrive-intel feed --limit 5[/dim]")
    elif resp.status_code == 403:
        detail = resp.json().get("detail", "Registration failed.")
        print_error(detail)
        if "invite" in detail.lower():
            console.print("[dim]  Use --invite-code YOUR_CODE[/dim]")
        raise typer.Exit(1)
    elif resp.status_code == 409:
        print_error(
            "An account with this email already exists. Use: overdrive-intel auth login"
        )
        raise typer.Exit(1)
    else:
        detail = resp.json().get("detail", resp.text)
        print_error(f"Registration failed: {detail}")
        raise typer.Exit(1)


@auth_app.command("status")
def auth_status() -> None:
    """Show current authentication status."""
    from cli.main import state

    key = get_api_key()

    if key:
        tier = _detect_key_tier()
        prefix = key[:12]
        if state.json_mode:
            print_json({"authenticated": True, "key_prefix": prefix, "storage": tier})
        else:
            console.print(
                f"[green]Authenticated[/green]  "
                f"Key: [bold]{prefix}...[/bold]  "
                f"Storage: [dim]{tier}[/dim]"
            )
    else:
        if state.json_mode:
            print_json(
                {"authenticated": False, "hint": "Run: overdrive-intel auth login"}
            )
        else:
            print_warning("No API key configured. Run: overdrive-intel auth login")
