"""
TDD stubs for CLI-04 (info).

Tests info command: UUID detection, search-then-fetch fallback, not found.
Generated from Plan 05-02 acceptance criteria.
"""
import json
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

MOCK_ITEM = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "source_id": "test:rss-source",
    "url": "https://example.com/article",
    "title": "New MCP Server for Docker",
    "excerpt": "A new MCP server...",
    "summary": "This MCP server manages Docker containers...",
    "primary_type": "skill",
    "tags": ["mcp", "docker"],
    "relevance_score": 0.92,
    "quality_score": 0.80,
    "confidence_score": 0.95,
    "status": "processed",
    "created_at": "2026-03-14T10:00:00Z",
}


def test_info_by_uuid():
    """CLI-04: info with valid UUID calls /v1/info/{uuid} directly."""
    mock = MagicMock()
    mock.return_value.get.return_value = MOCK_ITEM
    with patch("cli.info.get_client", mock):
        result = runner.invoke(app, ["info", "550e8400-e29b-41d4-a716-446655440000"])
    assert result.exit_code == 0
    # Should call /v1/info/{uuid} directly (not search)
    call_path = mock.return_value.get.call_args[0][0]
    assert "/v1/info/" in call_path


def test_info_by_name_search_then_fetch():
    """CLI-04: info with non-UUID does search first, then fetches by ID."""
    mock = MagicMock()
    # First call: search returns an item with ID
    # Second call: info returns full item
    mock.return_value.get.side_effect = [
        {"items": [{"id": "550e8400-e29b-41d4-a716-446655440000"}], "total": 1},
        MOCK_ITEM,
    ]
    with patch("cli.info.get_client", mock):
        result = runner.invoke(app, ["info", "docker-mcp-server"])
    assert result.exit_code == 0
    assert mock.return_value.get.call_count == 2


def test_info_not_found():
    """CLI-04: info with no matching item exits with error."""
    mock = MagicMock()
    mock.return_value.get.return_value = {"items": [], "total": 0}
    with patch("cli.info.get_client", mock):
        result = runner.invoke(app, ["info", "nonexistent-thing"])
    assert result.exit_code == 1


def test_info_json_output():
    """CLI-09: --json flag outputs raw JSON for info."""
    mock = MagicMock()
    mock.return_value.get.return_value = MOCK_ITEM
    with patch("cli.info.get_client", mock):
        result = runner.invoke(
            app, ["--json", "info", "550e8400-e29b-41d4-a716-446655440000"]
        )
    assert result.exit_code == 0
    parsed = json.loads(result.output.strip())
    assert parsed["title"] == "New MCP Server for Docker"


def test_info_uuid_api_error():
    """Gap: info handles API error on /v1/info/{uuid} endpoint."""
    import httpx

    mock = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock.return_value.get.side_effect = httpx.HTTPStatusError(
        "Server Error", request=MagicMock(), response=mock_resp
    )
    with patch("cli.info.get_client", mock):
        result = runner.invoke(app, ["info", "550e8400-e29b-41d4-a716-446655440000"])
    assert result.exit_code != 0
