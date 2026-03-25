"""
TDD stubs for CLI-03 (search).

Tests the search command with query, JSON output, and empty state.
Generated from Plan 05-02 acceptance criteria.
"""
import json
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

MOCK_SEARCH_RESPONSE = {
    "items": [
        {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "title": "MCP Tools Guide",
            "excerpt": "Comprehensive guide to building MCP tools...",
            "primary_type": "docs",
            "tags": ["mcp", "tutorial"],
            "relevance_score": 0.88,
            "rank": 1,
            "created_at": "2026-03-13T08:00:00Z",
        }
    ],
    "total": 1,
}


def _mock_client(response=None):
    mock = MagicMock()
    mock.return_value.get.return_value = response or MOCK_SEARCH_RESPONSE
    return mock


def test_search_basic():
    """CLI-03: search command with query renders results."""
    with patch("cli.search.get_client", _mock_client()):
        result = runner.invoke(app, ["search", "mcp tools"])
    assert result.exit_code == 0
    assert "MCP Tools Guide" in result.output or "mcp" in result.output.lower()


def test_search_json_output():
    """CLI-09: --json produces NDJSON for search results."""
    with patch("cli.search.get_client", _mock_client()):
        result = runner.invoke(app, ["--json", "search", "mcp"])
    assert result.exit_code == 0
    lines = [l for l in result.output.strip().split("\n") if l]
    assert len(lines) >= 1
    parsed = json.loads(lines[0])
    assert "title" in parsed


def test_search_empty_state():
    """CLI-11: empty search shows suggestion text."""
    with patch("cli.search.get_client", _mock_client({"items": [], "total": 0})):
        result = runner.invoke(app, ["search", "nonexistent-thing"])
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "no results" in combined.lower() or "try" in combined.lower()


def test_search_passes_query_param():
    """CLI-03: search sends q= parameter to API."""
    mock = _mock_client()
    with patch("cli.search.get_client", mock):
        runner.invoke(app, ["search", "claude code"])
    mock.return_value.get.assert_called_once()
    call_args = mock.return_value.get.call_args
    # Should pass query as q parameter
    assert call_args is not None
    _, kwargs = call_args
    assert kwargs.get("q") == "claude code"
