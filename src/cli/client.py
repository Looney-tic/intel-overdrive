"""Synchronous API client with three-tier auth fallback."""

from __future__ import annotations

import os
from typing import Any

import httpx
import keyring
import typer

import cli.config as _cfg


def get_api_key() -> str | None:
    """Resolve API key: keyring -> env var -> config file.

    Uses runtime attribute access on cli.config so tests can patch
    CONFIG_KEY_PATH, KEYRING_SERVICE etc. on the config module directly.
    """
    # Tier 1: system keyring (macOS Keychain / Windows Credential Mgr / Linux SecretService)
    try:
        key = keyring.get_password(_cfg.KEYRING_SERVICE, _cfg.KEYRING_USERNAME)
        if key:
            return key
    except Exception:
        pass  # keyring unavailable (headless server, CI) -> fall through

    # Tier 2: environment variable
    key = os.environ.get("OVERDRIVE_API_KEY")
    if key:
        return key

    # Tier 3: config file
    if _cfg.CONFIG_KEY_PATH.exists():
        return _cfg.CONFIG_KEY_PATH.read_text().strip()

    return None


def store_api_key(key: str) -> None:
    """Store API key in keyring; fall back to config file on keyring error."""
    try:
        keyring.set_password(_cfg.KEYRING_SERVICE, _cfg.KEYRING_USERNAME, key)
    except Exception:
        _cfg.CONFIG_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _cfg.CONFIG_KEY_PATH.write_text(key)
        _cfg.CONFIG_KEY_PATH.chmod(0o600)


class APIClient:
    """Synchronous httpx wrapper with auth header and error handling."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        resolved_key = api_key if api_key is not None else get_api_key()
        if resolved_key is None:
            typer.echo(
                "Error: No API key found. Run: overdrive-intel auth login",
                err=True,
            )
            raise typer.Exit(code=1)

        self.client = httpx.Client(
            base_url=base_url or _cfg.get_api_url(),
            headers={"X-API-Key": resolved_key},
            timeout=timeout,
        )

    def get(self, path: str, **params: Any) -> dict:
        """GET request with filtered None params and standard error handling."""
        filtered = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=filtered)

    def post(self, path: str, json: dict) -> dict:
        """POST request with standard error handling."""
        return self._request("POST", path, json=json)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        """Execute request with unified error handling."""
        try:
            resp = self.client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            typer.echo(
                "Error: Cannot connect to the API server. "
                "Check that OVERDRIVE_API_URL is correct and the server is running.",
                err=True,
            )
            raise typer.Exit(code=1)
        except httpx.TimeoutException:
            typer.echo(
                "Error: Request timed out. Check your connection or try again.",
                err=True,
            )
            raise typer.Exit(code=1)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                typer.echo(
                    "Error: Authentication failed. Run: overdrive-intel auth login",
                    err=True,
                )
                raise typer.Exit(code=1)
            if status == 429:
                typer.echo("Error: Rate limit exceeded. Try again later.", err=True)
                raise typer.Exit(code=1)
            raise


def get_client(base_url: str | None = None, api_key: str | None = None) -> APIClient:
    """Factory for APIClient with config-based defaults."""
    return APIClient(base_url=base_url, api_key=api_key)
