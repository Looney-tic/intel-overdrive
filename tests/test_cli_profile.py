"""
TDD stubs for CLI-06 (profile).

Tests profile --sync with opt-in confirmation and tech stack scanner.
Generated from Plan 05-03 acceptance criteria.
"""
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def test_profile_sync_requires_confirmation():
    """CLI-06: profile --sync asks for opt-in before scanning."""
    mock = MagicMock()
    with patch("cli.profile.get_client", mock):
        # Answer "n" to the confirmation prompt
        result = runner.invoke(app, ["profile", "--sync"], input="n\n")
    # Should NOT call the API since user declined
    mock.return_value.post.assert_not_called()


def test_profile_sync_scans_and_sends():
    """CLI-06: profile --sync with confirmation scans and posts to server."""
    mock_client = MagicMock()
    mock_client.return_value.post.return_value = {
        "message": "Profile updated",
        "profile": {"tech_stack": ["python"], "skills": []},
    }
    mock_scan = MagicMock(
        return_value={"tech_stack": ["python"], "skills": ["co-brainstorm"]}
    )
    with (
        patch("cli.profile.get_client", mock_client),
        patch("cli.profile.scan_claude_profile", mock_scan),
    ):
        result = runner.invoke(app, ["profile", "--sync"], input="y\n")
    assert result.exit_code == 0
    mock_client.return_value.post.assert_called_once()


def test_profile_without_sync_fetches_profile():
    """CLI-06: profile without --sync fetches and displays current profile."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"tech_stack": ["python"], "skills": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get.return_value = mock_response

    with patch("cli.profile.get_client", return_value=mock_client):
        result = runner.invoke(app, ["profile"])
    assert result.exit_code == 0


def test_scan_claude_profile_detects_python(tmp_path):
    """CLI-06: scanner detects python from pyproject.toml."""
    from cli.profile import scan_claude_profile

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        profile = scan_claude_profile()
    assert "python" in profile["tech_stack"]
