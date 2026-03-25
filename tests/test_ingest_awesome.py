"""
INGEST-14: awesome-claude-code git diff adapter tests.

Tests for ingest_awesome_source covering:
- First run: all README entries extracted, last_commit_sha set
- Diff-only run: only new entries from added lines extracted
- Same-SHA skip: no items created when last_commit_sha == current HEAD
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_awesome._pull_or_clone with MagicMock (sync)
- Patch src.workers.ingest_awesome._extract_new_entries with MagicMock (sync)
- Both functions are wrapped in asyncio.to_thread — mocking the sync functions directly
  avoids the thread overhead and tests the async adapter logic cleanly.
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_awesome import ingest_awesome_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def make_mock_repo(sha: str) -> MagicMock:
    """Create a mock git.Repo object with the given HEAD commit SHA."""
    mock_repo = MagicMock()
    mock_repo.head.commit.hexsha = sha
    return mock_repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awesome_first_run_extracts_all_entries(
    session, source_factory, redis_client
):
    """First run (no last_commit_sha): all README entries must be stored.

    Expects: 3 IntelItems created, last_commit_sha set to current HEAD SHA.
    """
    source = await source_factory(
        id="awesome:test-first-run",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={},  # No last_commit_sha → first run
    )
    ctx = {"redis": redis_client}

    current_sha = "abc123def456"
    mock_repo = make_mock_repo(current_sha)

    # First run: _extract_new_entries returns all entries from README
    mock_entries = [
        {
            "name": "Claude Code CLI",
            "url": "https://github.com/anthropics/claude-code",
            "description": "Official Claude Code CLI",
        },
        {
            "name": "MCP Server SDK",
            "url": "https://github.com/example/mcp-sdk",
            "description": "MCP server SDK",
        },
        {
            "name": "Awesome Resource",
            "url": "https://example.com/resource",
            "description": "A great resource",
        },
    ]

    with patch(
        "src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo
    ) as mock_clone:
        with patch(
            "src.workers.ingest_awesome._extract_new_entries", return_value=mock_entries
        ) as mock_extract:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 3
    urls = {item.url for item in items}
    assert "https://github.com/anthropics/claude-code" in urls
    assert "https://github.com/example/mcp-sdk" in urls
    assert "https://example.com/resource" in urls
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id

    # Verify last_commit_sha was updated
    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.config["last_commit_sha"] == current_sha


@pytest.mark.asyncio
async def test_awesome_first_run_extract_called_with_none_sha(
    session, source_factory, redis_client
):
    """First run: _extract_new_entries must be called with from_sha=None."""
    source = await source_factory(
        id="awesome:test-first-run-sha",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={},
    )
    ctx = {"redis": redis_client}

    current_sha = "abc123def456"
    mock_repo = make_mock_repo(current_sha)

    mock_entries = [
        {
            "name": "Entry",
            "url": "https://example.com/entry",
            "description": "A resource",
        },
    ]

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries", return_value=mock_entries
        ) as mock_extract:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    # Must be called with from_sha=None (first run) and to_sha=current_sha
    mock_extract.assert_called_once_with(mock_repo, None, current_sha)


@pytest.mark.asyncio
async def test_awesome_diff_only_on_incremental_run(
    session, source_factory, redis_client
):
    """Incremental run: only entries from git diff since last_commit_sha are created."""
    previous_sha = "prev111sha"
    current_sha = "curr222sha"

    source = await source_factory(
        id="awesome:test-diff-only",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={"last_commit_sha": previous_sha},
    )
    ctx = {"redis": redis_client}

    mock_repo = make_mock_repo(current_sha)

    # Only 1 new entry in diff
    mock_entries = [
        {
            "name": "New Tool",
            "url": "https://example.com/new-tool",
            "description": "A new tool",
        },
    ]

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries", return_value=mock_entries
        ) as mock_extract:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    # Full README parse on every run (from_sha=None), dedup via check_url_exists
    mock_extract.assert_called_once_with(mock_repo, None, current_sha)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Only 1 new entry (diff-only, not all README entries)
    assert len(items) == 1
    assert items[0].url == "https://example.com/new-tool"


@pytest.mark.asyncio
async def test_awesome_same_sha_skip(session, source_factory, redis_client):
    """If last_commit_sha == current HEAD SHA, no items created, success still recorded."""
    same_sha = "same_sha_111"

    source = await source_factory(
        id="awesome:test-same-sha",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={"last_commit_sha": same_sha},
    )
    ctx = {"redis": redis_client}

    mock_repo = make_mock_repo(same_sha)  # Same SHA as stored

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch("src.workers.ingest_awesome._extract_new_entries") as mock_extract:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    # _extract_new_entries must NOT be called — skip early when SHA matches
    mock_extract.assert_not_called()

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0

    # Source success should still be recorded (last_successful_poll updated)
    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.last_successful_poll is not None


@pytest.mark.asyncio
async def test_awesome_dedup_skips_existing_urls(session, source_factory, redis_client):
    """Pre-existing URL must be skipped; only new entries inserted."""
    source = await source_factory(
        id="awesome:test-dedup",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={},
    )
    # Pre-insert one entry
    existing_url = "https://github.com/anthropics/claude-code"
    existing = IntelItem(
        source_id=source.id,
        external_id=existing_url,
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="Claude Code CLI",
        content="existing",
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=hashlib.sha256(b"existing").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    current_sha = "new_sha_abc"
    mock_repo = make_mock_repo(current_sha)

    mock_entries = [
        {
            "name": "Claude Code CLI",
            "url": "https://github.com/anthropics/claude-code",
            "description": "existing",
        },
        {"name": "New Entry", "url": "https://example.com/new", "description": "new"},
    ]

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries", return_value=mock_entries
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Pre-existing + 1 new = 2 total
    assert len(items) == 2


@pytest.mark.asyncio
async def test_awesome_last_commit_sha_updated_after_diff(
    session, source_factory, redis_client
):
    """After incremental run, last_commit_sha must be updated to current HEAD SHA."""
    previous_sha = "prev_sha_001"
    current_sha = "curr_sha_002"

    source = await source_factory(
        id="awesome:test-sha-update",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={"last_commit_sha": previous_sha},
    )
    ctx = {"redis": redis_client}

    mock_repo = make_mock_repo(current_sha)
    mock_entries = [
        {"name": "New", "url": "https://example.com/new", "description": "new"},
    ]

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries", return_value=mock_entries
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.config["last_commit_sha"] == current_sha


@pytest.mark.asyncio
async def test_awesome_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Git clone/pull failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="awesome:test-error",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_awesome._pull_or_clone",
        side_effect=Exception("Git clone failed"),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Git clone failed"):
                await ingest_awesome_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_awesome_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive awesome-list source must not call _pull_or_clone."""
    source = await source_factory(
        id="awesome:test-inactive",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        is_active=False,
        config={},
    )
    ctx = {"redis": redis_client}

    with patch("src.workers.ingest_awesome._pull_or_clone") as mock_clone:
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_awesome_source(ctx, source.id)

    mock_clone.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests for _extract_new_entries regex parsing (no mocking needed)
# ---------------------------------------------------------------------------


class TestExtractNewEntries:
    """Direct unit tests for _extract_new_entries parsing logic.

    These test the regex and line-parsing without mocking, addressing the
    gap identified by multi-LLM test sufficiency review.
    """

    def _make_mock_repo_first_run(self, readme_content: str):
        """Create a mock repo for first-run (from_sha=None) parsing."""
        import io

        mock_blob = MagicMock()
        mock_blob.data_stream.read.return_value = readme_content.encode("utf-8")

        mock_tree = MagicMock()
        mock_tree.__getitem__ = MagicMock(return_value=mock_blob)

        mock_repo = MagicMock()
        mock_repo.head.commit.tree = mock_tree
        return mock_repo

    def _make_mock_repo_diff(self, diff_output: str):
        """Create a mock repo for incremental (from_sha provided) parsing."""
        mock_repo = MagicMock()
        mock_repo.git.diff.return_value = diff_output
        return mock_repo

    def test_first_run_parses_standard_entries(self):
        from src.workers.ingest_awesome import _extract_new_entries

        readme = (
            "# Awesome Claude Code\n"
            "\n"
            "- [Tool A](https://example.com/tool-a) - A cool tool\n"
            "- [Tool B](https://github.com/user/tool-b) - Another tool\n"
            "- Not a link entry\n"
            "## Section\n"
            "- [Tool C](https://example.com/tool-c)\n"
        )
        repo = self._make_mock_repo_first_run(readme)
        entries = _extract_new_entries(repo, None, "abc123")

        assert len(entries) == 3
        assert entries[0]["name"] == "Tool A"
        assert entries[0]["url"] == "https://example.com/tool-a"
        assert entries[1]["name"] == "Tool B"
        assert entries[2]["name"] == "Tool C"

    def test_first_run_empty_readme(self):
        from src.workers.ingest_awesome import _extract_new_entries

        repo = self._make_mock_repo_first_run("")
        entries = _extract_new_entries(repo, None, "abc123")
        assert entries == []

    def test_first_run_no_list_items(self):
        from src.workers.ingest_awesome import _extract_new_entries

        readme = "# Awesome\n\nJust text, no list items.\n"
        repo = self._make_mock_repo_first_run(readme)
        entries = _extract_new_entries(repo, None, "abc123")
        assert entries == []

    def test_first_run_readme_missing(self):
        from src.workers.ingest_awesome import _extract_new_entries

        mock_repo = MagicMock()
        mock_repo.head.commit.tree.__getitem__ = MagicMock(
            side_effect=KeyError("README.md")
        )
        entries = _extract_new_entries(mock_repo, None, "abc123")
        assert entries == []

    def test_diff_parses_added_lines_only(self):
        from src.workers.ingest_awesome import _extract_new_entries

        diff = (
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,3 +1,5 @@\n"
            " - [Existing](https://example.com/existing)\n"
            "+- [New Tool](https://example.com/new-tool) - Brand new\n"
            "+- [Another](https://example.com/another)\n"
            " - [Old](https://example.com/old)\n"
        )
        repo = self._make_mock_repo_diff(diff)
        entries = _extract_new_entries(repo, "old_sha", "new_sha")

        assert len(entries) == 2
        assert entries[0]["name"] == "New Tool"
        assert entries[0]["url"] == "https://example.com/new-tool"
        assert entries[1]["name"] == "Another"

    def test_diff_empty_diff(self):
        from src.workers.ingest_awesome import _extract_new_entries

        repo = self._make_mock_repo_diff("")
        entries = _extract_new_entries(repo, "sha1", "sha2")
        assert entries == []

    def test_diff_no_url_in_added_lines(self):
        from src.workers.ingest_awesome import _extract_new_entries

        diff = "+- Just plain text without a link\n+- Another plain item\n"
        repo = self._make_mock_repo_diff(diff)
        entries = _extract_new_entries(repo, "sha1", "sha2")
        # Lines start with "+- " but not "+- [", so no entries extracted
        assert entries == []
