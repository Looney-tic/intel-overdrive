"""Interactive setup wizard — single command to register, configure, and verify."""

from __future__ import annotations

import sys

import typer
import httpx

import cli.config as _cfg
from cli.client import store_api_key
from cli.render import console, print_error, print_success

setup_app = typer.Typer(help="Set up overdrive-intel in one step")


@setup_app.callback(invoke_without_command=True)
def setup(
    url: str = typer.Option(
        None, "--url", envvar="OVERDRIVE_API_URL", help="API base URL"
    ),
    email: str = typer.Option(None, "--email", help="Email address"),
    invite_code: str = typer.Option(None, "--invite-code", help="Invite code"),
    key: str = typer.Option(
        None, "--key", help="Existing API key (skips registration)"
    ),
) -> None:
    """One-command setup: register, store key, configure URL, verify connection.

    Examples:
      overdrive-intel setup --url https://api.example.com --invite-code YOUR_INVITE_CODE
      overdrive-intel setup --key dti_v1_existing_key
    """
    console.print("\n[bold]Overdrive Intel Setup[/bold]\n")

    # 1. API URL
    if not url:
        if not sys.stdout.isatty():
            print_error("--url is required in non-interactive mode.")
            raise typer.Exit(1)
        url = typer.prompt("API URL", default=_cfg.DEFAULT_API_URL)

    # Store URL in config
    _cfg.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    url_path = _cfg.CONFIG_DIR / "api_url"
    url_path.write_text(url.rstrip("/"))
    console.print(f"  API URL: [bold]{url}[/bold]")

    # 2. API Key — either existing or register
    if key:
        if not key.startswith("dti_v1_"):
            print_error("Invalid key format. Expected 'dti_v1_...'")
            raise typer.Exit(1)
        store_api_key(key)
        console.print(f"  API Key: [bold]{key[:14]}...[/bold] (stored)")
    else:
        # Register
        if not email:
            if not sys.stdout.isatty():
                print_error("--email is required in non-interactive mode.")
                raise typer.Exit(1)
            email = typer.prompt("Email")
        if not invite_code:
            invite_code = typer.prompt("Invite code", default="")

        body: dict = {"email": email}
        if invite_code:
            body["invite_code"] = invite_code

        console.print(f"  Registering [bold]{email}[/bold]...")
        try:
            resp = httpx.post(f"{url}/v1/auth/register", json=body, timeout=10.0)
        except httpx.ConnectError:
            print_error(f"Cannot connect to {url}. Check the URL and try again.")
            raise typer.Exit(1)

        if resp.status_code == 201:
            data = resp.json()
            raw_key = data["api_key"]
            store_api_key(raw_key)
            console.print(
                f"  API Key: [bold]{raw_key[:14]}...[/bold] (stored securely)"
            )
        elif resp.status_code == 409:
            print_error(
                "Account already exists. Use: overdrive-intel setup --key YOUR_KEY"
            )
            raise typer.Exit(1)
        else:
            detail = resp.json().get("detail", resp.text)
            print_error(f"Registration failed: {detail}")
            raise typer.Exit(1)

    # 3. Verify connection
    console.print("\n  Verifying connection...")
    try:
        from cli.client import get_api_key

        api_key = get_api_key()
        resp = httpx.get(
            f"{url}/v1/feed",
            headers={"X-API-Key": api_key},
            params={"limit": 1, "days": 7},
            timeout=10.0,
        )
        if resp.status_code == 200:
            total = resp.json().get("total", 0)
            print_success(f"Connected! {total} items available in the last 7 days.")
        else:
            console.print(
                f"  [yellow]Warning: API returned {resp.status_code}[/yellow]"
            )
    except Exception as e:
        console.print(f"  [yellow]Warning: Could not verify ({e})[/yellow]")

    # 4. Next steps
    console.print("\n[bold]You're all set![/bold] Try:\n")
    console.print("  overdrive-intel feed --sort significance --limit 5")
    console.print("  overdrive-intel library topics")
    console.print('  overdrive-intel library search "mcp server best practices"')
    console.print("  overdrive-intel profile --sync   # personalize your feed")
    console.print()
