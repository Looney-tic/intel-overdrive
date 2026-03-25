"""Tests for the Claude Code plugin marketplace ingestion adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.workers.ingest_marketplace import (
    _build_raw_url,
    _extract_plugin_repo_url,
    ingest_marketplace_source,
    poll_marketplace_sources,
)


# --- Unit tests for helpers ---


class TestBuildRawUrl:
    def test_github_repo(self):
        url = _build_raw_url("https://github.com/acme/plugins")
        assert (
            url
            == "https://raw.githubusercontent.com/acme/plugins/HEAD/.claude-plugin/marketplace.json"
        )

    def test_github_repo_with_git_suffix(self):
        url = _build_raw_url("https://github.com/acme/plugins.git")
        assert (
            url
            == "https://raw.githubusercontent.com/acme/plugins/HEAD/.claude-plugin/marketplace.json"
        )

    def test_gitlab_repo(self):
        url = _build_raw_url("https://gitlab.com/team/tools")
        assert (
            url
            == "https://gitlab.com/team/tools/-/raw/main/.claude-plugin/marketplace.json"
        )

    def test_direct_json_url(self):
        url = _build_raw_url("https://example.com/marketplace.json")
        assert url == "https://example.com/marketplace.json"

    def test_custom_path(self):
        url = _build_raw_url("https://github.com/acme/plugins", "custom/path.json")
        assert (
            url
            == "https://raw.githubusercontent.com/acme/plugins/HEAD/custom/path.json"
        )


class TestExtractPluginRepoUrl:
    def test_github_source(self):
        plugin = {"source": {"source": "github", "repo": "owner/repo"}}
        assert _extract_plugin_repo_url(plugin) == "https://github.com/owner/repo"

    def test_url_source_github(self):
        plugin = {
            "source": {"source": "url", "url": "https://github.com/owner/repo.git"}
        }
        assert _extract_plugin_repo_url(plugin) == "https://github.com/owner/repo"

    def test_npm_source(self):
        plugin = {"source": {"source": "npm", "package": "@acme/tool"}}
        assert (
            _extract_plugin_repo_url(plugin)
            == "https://www.npmjs.com/package/@acme/tool"
        )

    def test_relative_path_returns_none(self):
        plugin = {"source": "./plugins/my-plugin"}
        assert _extract_plugin_repo_url(plugin) is None

    def test_no_source(self):
        plugin = {}
        assert _extract_plugin_repo_url(plugin) is None


# --- Integration tests ---


@pytest.fixture
def mock_ctx():
    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()
    return {"redis": redis}


SAMPLE_MARKETPLACE = {
    "name": "test-marketplace",
    "owner": {"name": "Test"},
    "plugins": [
        {
            "name": "plugin-a",
            "source": {"source": "github", "repo": "owner/plugin-a"},
            "description": "A great plugin",
            "version": "1.0.0",
            "keywords": ["mcp", "tools"],
            "mcpServers": {"server": {"command": "node"}},
        },
        {
            "name": "plugin-b",
            "source": "./local-only",
            "description": "Local plugin",
        },
        {
            "name": "plugin-c",
            "source": {"source": "npm", "package": "@test/plugin-c"},
            "description": "npm plugin",
            "hooks": {"SessionStart": []},
        },
    ],
}


@pytest.mark.asyncio
async def test_poll_marketplace_sources_dispatches(mock_ctx):
    """poll_marketplace_sources enqueues jobs for all active marketplace sources."""
    mock_source = MagicMock()
    mock_source.id = "marketplace:test"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_source]
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("src.workers.ingest_marketplace._db") as mock_db:
        mock_db.async_session_factory = mock_factory
        await poll_marketplace_sources(mock_ctx)

    mock_ctx["redis"].enqueue_job.assert_called_once_with(
        "ingest_marketplace_source", "marketplace:test", _queue_name="fast"
    )


@pytest.mark.asyncio
async def test_ingest_marketplace_creates_items(mock_ctx):
    """ingest_marketplace_source fetches marketplace.json and creates IntelItems."""
    mock_source = MagicMock()
    mock_source.id = "marketplace:test"
    mock_source.url = "https://github.com/acme/plugins"
    mock_source.is_active = True
    mock_source.poll_interval_seconds = 43200
    mock_source.name = "Test Marketplace"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.begin_nested = MagicMock(return_value=AsyncMock())
    mock_session.begin_nested.return_value.__aenter__ = AsyncMock()
    mock_session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_dedup = AsyncMock()
    mock_dedup.check_url_exists = AsyncMock(return_value=False)
    mock_dedup._compute_url_hash = MagicMock(return_value="hash")
    mock_dedup._get_content_fingerprint = MagicMock(return_value="fp")

    with (
        patch("src.workers.ingest_marketplace._db") as mock_db,
        patch(
            "src.workers.ingest_marketplace._fetch_marketplace_json",
            return_value=SAMPLE_MARKETPLACE,
        ),
        patch(
            "src.workers.ingest_marketplace.is_source_on_cooldown", return_value=False
        ),
        patch(
            "src.workers.ingest_marketplace.handle_source_success",
            new_callable=AsyncMock,
        ),
        patch("src.workers.ingest_marketplace.DedupService", return_value=mock_dedup),
        patch(
            "src.workers.ingest_marketplace._auto_track_plugin_repos",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        mock_db.async_session_factory = mock_factory
        await ingest_marketplace_source(mock_ctx, "marketplace:test")

    # Should have called session.add for 3 plugins
    assert mock_session.add.call_count == 3
    mock_session.commit.assert_called()


@pytest.mark.asyncio
async def test_ingest_marketplace_skips_duplicates(mock_ctx):
    """Duplicate URLs are skipped via dedup check."""
    mock_source = MagicMock()
    mock_source.id = "marketplace:test"
    mock_source.url = "https://github.com/acme/plugins"
    mock_source.is_active = True
    mock_source.poll_interval_seconds = 43200
    mock_source.name = "Test Marketplace"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_dedup = AsyncMock()
    mock_dedup.check_url_exists = AsyncMock(return_value=True)  # All dupes

    with (
        patch("src.workers.ingest_marketplace._db") as mock_db,
        patch(
            "src.workers.ingest_marketplace._fetch_marketplace_json",
            return_value=SAMPLE_MARKETPLACE,
        ),
        patch(
            "src.workers.ingest_marketplace.is_source_on_cooldown", return_value=False
        ),
        patch(
            "src.workers.ingest_marketplace.handle_source_success",
            new_callable=AsyncMock,
        ),
        patch("src.workers.ingest_marketplace.DedupService", return_value=mock_dedup),
    ):
        mock_db.async_session_factory = mock_factory
        await ingest_marketplace_source(mock_ctx, "marketplace:test")

    # No items added since all are duplicates
    mock_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_marketplace_tags_include_components(mock_ctx):
    """Plugin items get tagged with their component types (mcp-server, hooks, etc)."""
    marketplace = {
        "name": "comp-test",
        "owner": {"name": "Test"},
        "plugins": [
            {
                "name": "full-plugin",
                "source": {"source": "github", "repo": "owner/full"},
                "description": "Full featured",
                "mcpServers": {"s": {}},
                "hooks": {"PreToolUse": []},
                "agents": ["./agent.md"],
            }
        ],
    }

    mock_source = MagicMock()
    mock_source.id = "marketplace:comp"
    mock_source.url = "https://github.com/owner/comp"
    mock_source.is_active = True
    mock_source.poll_interval_seconds = 43200
    mock_source.name = "Comp"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_source
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.begin_nested = MagicMock(return_value=AsyncMock())
    mock_session.begin_nested.return_value.__aenter__ = AsyncMock()
    mock_session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_dedup = AsyncMock()
    mock_dedup.check_url_exists = AsyncMock(return_value=False)
    mock_dedup._compute_url_hash = MagicMock(return_value="h")
    mock_dedup._get_content_fingerprint = MagicMock(return_value="f")

    with (
        patch("src.workers.ingest_marketplace._db") as mock_db,
        patch(
            "src.workers.ingest_marketplace._fetch_marketplace_json",
            return_value=marketplace,
        ),
        patch(
            "src.workers.ingest_marketplace.is_source_on_cooldown", return_value=False
        ),
        patch(
            "src.workers.ingest_marketplace.handle_source_success",
            new_callable=AsyncMock,
        ),
        patch("src.workers.ingest_marketplace.DedupService", return_value=mock_dedup),
        patch(
            "src.workers.ingest_marketplace._auto_track_plugin_repos",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        mock_db.async_session_factory = mock_factory
        await ingest_marketplace_source(mock_ctx, "marketplace:comp")

    # Check the item that was added has the right tags
    item = mock_session.add.call_args[0][0]
    assert "mcp-server" in item.tags
    assert "hooks" in item.tags
    assert "agents" in item.tags
    assert "claude-code-plugin" in item.tags
