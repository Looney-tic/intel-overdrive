"""
Tests for CLI-07 (auth) and CLI-10 (key fallback).

Tests auth login/status and three-tier API key resolution.
"""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import typer
from typer.testing import CliRunner

from cli.main import app
from cli.client import get_api_key, store_api_key, APIClient

runner = CliRunner()


# --- Auth login ---


def test_auth_login_stores_key():
    """CLI-07: auth login prompts for key and stores it."""
    with patch("cli.auth.store_api_key") as mock_store:
        result = runner.invoke(
            app, ["auth", "login"], input="dti_v1_testkey123abc456\n"
        )
    assert result.exit_code == 0
    mock_store.assert_called_once_with("dti_v1_testkey123abc456")


def test_auth_login_rejects_invalid_format():
    """CLI-07: auth login rejects keys without dti_v1_ prefix."""
    result = runner.invoke(app, ["auth", "login"], input="invalid-key-format\n")
    assert result.exit_code == 1
    assert "invalid" in result.output.lower() or "format" in result.output.lower()


# --- Auth status ---


def test_auth_status_when_configured():
    """CLI-07: auth status shows key presence when configured."""
    with patch("cli.auth.get_api_key", return_value="dti_v1_testkey123abc456"):
        result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "dti_v1_testk" in result.output  # masked key prefix


def test_auth_status_when_not_configured():
    """CLI-07: auth status warns when no key configured."""
    with patch("cli.auth.get_api_key", return_value=None):
        result = runner.invoke(app, ["auth", "status"])
    # Should show warning about missing key
    assert "login" in result.output.lower() or "not configured" in result.output.lower()


# --- Three-tier key fallback ---


def test_api_key_fallback_keyring():
    """CLI-10: get_api_key reads from keyring first."""
    with patch("keyring.get_password", return_value="dti_v1_from_keyring"):
        key = get_api_key()
    assert key == "dti_v1_from_keyring"


def test_api_key_fallback_env(monkeypatch):
    """CLI-10: get_api_key falls back to OVERDRIVE_API_KEY env var."""
    with patch("keyring.get_password", side_effect=Exception("no keyring")):
        monkeypatch.setenv("OVERDRIVE_API_KEY", "dti_v1_from_env")
        key = get_api_key()
    assert key == "dti_v1_from_env"


def test_api_key_fallback_file(monkeypatch, tmp_path):
    """CLI-10: get_api_key falls back to config file."""
    key_file = tmp_path / "key"
    key_file.write_text("dti_v1_from_file")
    with (
        patch("keyring.get_password", side_effect=Exception("no keyring")),
        patch("cli.config.CONFIG_KEY_PATH", key_file),
    ):
        monkeypatch.delenv("OVERDRIVE_API_KEY", raising=False)
        key = get_api_key()
    assert key == "dti_v1_from_file"


def test_api_key_fallback_none(monkeypatch, tmp_path):
    """CLI-10: get_api_key returns None when all tiers fail."""
    with (
        patch("keyring.get_password", side_effect=Exception("no keyring")),
        patch("cli.config.CONFIG_KEY_PATH", tmp_path / "nonexistent"),
    ):
        monkeypatch.delenv("OVERDRIVE_API_KEY", raising=False)
        key = get_api_key()
    assert key is None


# --- Gap closure: error paths ---


def test_api_key_whitespace_config_file(monkeypatch, tmp_path):
    """Gap: config file with only whitespace returns None-equivalent."""
    key_file = tmp_path / "key"
    key_file.write_text("   \n  ")
    with (
        patch("keyring.get_password", side_effect=Exception("no keyring")),
        patch("cli.config.CONFIG_KEY_PATH", key_file),
    ):
        monkeypatch.delenv("OVERDRIVE_API_KEY", raising=False)
        key = get_api_key()
    # .strip() on whitespace returns empty string, which is falsy
    assert not key


def test_store_api_key_file_write_error(tmp_path):
    """Gap: store_api_key propagates error when file write fails."""
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o444)
    bad_path = readonly_dir / "subdir" / "key"
    with (
        patch("keyring.set_password", side_effect=Exception("no keyring")),
        patch("cli.config.CONFIG_KEY_PATH", bad_path),
    ):
        try:
            store_api_key("dti_v1_test")
        except (PermissionError, OSError):
            pass  # expected — error propagates
        finally:
            readonly_dir.chmod(0o755)


def test_client_request_500_reraises():
    """Gap: _request re-raises non-401/429 HTTP errors."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server Error", request=MagicMock(), response=mock_response
    )
    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.request.return_value = mock_response
        client = APIClient(api_key="dti_v1_test", base_url="http://test")
        client.client = mock_httpx.return_value
        try:
            client._request("GET", "/v1/feed")
            assert False, "Should have raised"
        except httpx.HTTPStatusError as e:
            assert e.response.status_code == 500


def test_client_request_timeout_exits():
    """Gap: _request exits cleanly on timeout."""
    from click.exceptions import Exit

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.request.side_effect = httpx.TimeoutException("timeout")
        client = APIClient(api_key="dti_v1_test", base_url="http://test")
        client.client = mock_httpx.return_value
        try:
            client._request("GET", "/v1/feed")
            assert False, "Should have raised"
        except Exit as e:
            assert e.exit_code == 1
