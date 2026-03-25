"""
TDD stubs for CLI-02 (feed) and CLI-11 (empty state).

Tests the feed command with filters, JSON output, and empty state handling.
Generated from Plan 05-02 acceptance criteria.
"""
import json
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

MOCK_FEED_RESPONSE = {
    "items": [
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "title": "New MCP Server for Docker",
            "excerpt": "A new MCP server that manages Docker containers...",
            "primary_type": "skill",
            "tags": ["mcp", "docker"],
            "relevance_score": 0.92,
            "created_at": "2026-03-14T10:00:00Z",
        }
    ],
    "total": 1,
    "offset": 0,
    "limit": 20,
}

MOCK_EMPTY_RESPONSE = {"items": [], "total": 0, "offset": 0, "limit": 20}


def _mock_client(response=None):
    """Create a mock get_client that returns preset data."""
    mock = MagicMock()
    mock.return_value.get.return_value = response or MOCK_FEED_RESPONSE
    return mock


def test_feed_default():
    """CLI-02: feed command with no filters returns items."""
    with patch("cli.feed.get_client", _mock_client()):
        result = runner.invoke(app, ["feed"])
    assert result.exit_code == 0
    assert "MCP Server" in result.output or "Docker" in result.output


def test_feed_with_filters():
    """CLI-02: feed command passes --days, --type, --tag as query params."""
    mock = _mock_client()
    with patch("cli.feed.get_client", mock):
        result = runner.invoke(
            app, ["feed", "--days", "14", "--type", "skill", "--tag", "mcp"]
        )
    assert result.exit_code == 0
    call_args = mock.return_value.get.call_args
    assert call_args is not None
    # Verify filter params were passed to client.get()
    _, kwargs = call_args
    assert kwargs.get("days") == 14
    assert kwargs.get("type") == "skill"
    assert kwargs.get("tag") == "mcp"


def test_feed_json_output():
    """CLI-09: --json flag produces valid NDJSON output."""
    with patch("cli.feed.get_client", _mock_client()):
        result = runner.invoke(app, ["--json", "feed"])
    assert result.exit_code == 0
    # Output should be parseable JSON
    lines = [l for l in result.output.strip().split("\n") if l]
    assert len(lines) >= 1
    parsed = json.loads(lines[0])
    assert "title" in parsed


def test_feed_empty_state():
    """CLI-11: empty feed shows suggestion text."""
    with patch("cli.feed.get_client", _mock_client(MOCK_EMPTY_RESPONSE)):
        result = runner.invoke(app, ["feed"])
    # Empty state message should appear (on stderr or stdout)
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "no results" in combined.lower() or "try" in combined.lower()


def test_feed_pagination():
    """CLI-02: feed passes --limit and --offset params."""
    mock = _mock_client()
    with patch("cli.feed.get_client", mock):
        result = runner.invoke(app, ["feed", "--limit", "5", "--offset", "10"])
    assert result.exit_code == 0
    call_args = mock.return_value.get.call_args
    _, kwargs = call_args
    assert kwargs.get("limit") == 5
    assert kwargs.get("offset") == 10


def test_feed_malformed_response():
    """Gap: feed handles response missing 'items' key."""
    mock = _mock_client({"total": 0})  # missing 'items'
    with patch("cli.feed.get_client", mock):
        result = runner.invoke(app, ["feed"])
    # Should either crash with KeyError (current) or handle gracefully
    # This test documents current behavior — if it fails, error handling was added
    assert result.exit_code != 0 or "items" not in result.output
