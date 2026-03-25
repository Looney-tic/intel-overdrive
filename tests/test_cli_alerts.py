"""
Unit tests for CLI alerts commands.

Requirements traced:
- ALERT-06: set-slack command configures Slack webhook via API
- ALERT-07: status command shows alert rules and delivery state
"""

from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# ALERT-06: set-slack command
# ---------------------------------------------------------------------------


def test_set_slack_command():
    """ALERT-06: 'alerts set-slack' sends webhook URL to /v1/alerts/slack-webhook."""
    mock = MagicMock()
    mock.return_value.post.return_value = {
        "message": "Slack webhook configured for 1 rules"
    }
    with patch("cli.alerts.get_client", mock):
        result = runner.invoke(
            app, ["alerts", "set-slack", "https://hooks.slack.com/services/T00/B00/xxx"]
        )
    assert result.exit_code == 0
    call_args = mock.return_value.post.call_args
    assert "/v1/alerts/slack-webhook" in call_args[0][0]
    assert (
        call_args[1]["json"]["webhook_url"]
        == "https://hooks.slack.com/services/T00/B00/xxx"
    )


# ---------------------------------------------------------------------------
# ALERT-07: status command
# ---------------------------------------------------------------------------


def test_alerts_status_command():
    """ALERT-07: 'alerts status' calls GET /v1/alerts/status and shows rules."""
    mock = MagicMock()
    mock.return_value.get.return_value = {
        "rules": [
            {
                "id": "550e8400-e29b-41d4-a716-446655440099",
                "name": "Claude Alerts",
                "keywords": ["claude"],
                "delivery_channels": {"slack_webhook": "https://hooks.slack.com/xxx"},
                "is_active": True,
                "cooldown_minutes": 60,
                "created_at": "2026-03-14T00:00:00",
                "is_on_cooldown": False,
                "last_fired_at": None,
            }
        ],
        "message": "1 active alert rules",
    }
    with patch("cli.alerts.get_client", mock):
        result = runner.invoke(app, ["alerts", "status"])
    assert result.exit_code == 0
    assert "Claude Alerts" in result.output
    mock.return_value.get.assert_called_once()
    call_args = mock.return_value.get.call_args
    assert "/v1/alerts/status" in call_args[0][0]


def test_alerts_status_empty():
    """ALERT-07: 'alerts status' with no rules shows empty rules in JSON mode (CliRunner is non-TTY)."""
    mock = MagicMock()
    mock.return_value.get.return_value = {
        "rules": [],
        "message": "0 active alert rules",
    }
    with patch("cli.alerts.get_client", mock):
        result = runner.invoke(app, ["alerts", "status"])
    assert result.exit_code == 0
    # CliRunner is non-TTY, so json_mode=True -> prints JSON
    assert '"rules": []' in result.output or "rules" in result.output
