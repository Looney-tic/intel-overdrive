"""
TDD stubs for CLI-05 (status).

Tests the status command rendering sources, spend, and health.
Generated from Plan 05-02 acceptance criteria.
"""
import json
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

MOCK_STATUS_RESPONSE = {
    "sources": [
        {
            "id": 1,
            "name": "Anthropic Changelog",
            "type": "rss",
            "is_active": True,
            "last_successful_poll": "2026-03-14T09:00:00Z",
            "consecutive_errors": 0,
            "poll_interval_seconds": 1800,
        },
        {
            "id": 2,
            "name": "GitHub MCP Search",
            "type": "github",
            "is_active": True,
            "last_successful_poll": "2026-03-14T08:30:00Z",
            "consecutive_errors": 1,
            "poll_interval_seconds": 3600,
        },
    ],
    "daily_spend_remaining": 4.50,
    "pipeline_health": "healthy",
}


def test_status_renders():
    """CLI-05: status command shows sources and spend info."""
    mock = MagicMock()
    mock.return_value.get.return_value = MOCK_STATUS_RESPONSE
    with patch("cli.status.get_client", mock):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Anthropic" in result.output or "healthy" in result.output.lower()


def test_status_json_output():
    """CLI-09: --json flag produces valid JSON for status."""
    mock = MagicMock()
    mock.return_value.get.return_value = MOCK_STATUS_RESPONSE
    with patch("cli.status.get_client", mock):
        result = runner.invoke(app, ["--json", "status"])
    assert result.exit_code == 0
    parsed = json.loads(result.output.strip())
    assert "sources" in parsed
    assert parsed["pipeline_health"] == "healthy"
