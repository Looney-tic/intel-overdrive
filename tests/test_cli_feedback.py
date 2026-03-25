"""
TDD stubs for feedback commands.

Tests feedback miss/noise reporting to the server.
Generated from Plan 05-03 acceptance criteria.
"""
import json
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def test_feedback_miss():
    """Feedback miss posts URL to server with report_type='miss'."""
    mock = MagicMock()
    mock.return_value.post.return_value = {
        "message": "Feedback recorded",
        "id": "550e8400-e29b-41d4-a716-446655440099",
    }
    with patch("cli.feedback.get_client", mock):
        result = runner.invoke(app, ["feedback", "miss", "https://example.com/tool"])
    assert result.exit_code == 0
    call_args = mock.return_value.post.call_args
    assert call_args[1]["json"]["report_type"] == "miss"
    assert call_args[1]["json"]["url"] == "https://example.com/tool"


def test_feedback_noise():
    """Feedback noise posts item_id to server with report_type='noise'."""
    mock = MagicMock()
    mock.return_value.post.return_value = {
        "message": "Feedback recorded",
        "id": "550e8400-e29b-41d4-a716-446655440099",
    }
    with patch("cli.feedback.get_client", mock):
        result = runner.invoke(
            app,
            ["feedback", "noise", "550e8400-e29b-41d4-a716-446655440000"],
        )
    assert result.exit_code == 0
    call_args = mock.return_value.post.call_args
    assert call_args[1]["json"]["report_type"] == "noise"


def test_feedback_noise_invalid_uuid():
    """Feedback noise rejects invalid UUID."""
    result = runner.invoke(app, ["feedback", "noise", "not-a-uuid"])
    assert result.exit_code == 1
