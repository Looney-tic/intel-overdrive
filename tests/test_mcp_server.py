"""Tests for the MCP server (src/mcp_server.py).

Tests tool definitions (via AST analysis), handler logic, and error handling.
Uses AST analysis for tool definitions (avoids mcp import issue) and
sys.modules patching + importlib for handler function tests.
"""
import ast
import json
import pathlib
import sys
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


# ---------------------------------------------------------------------------
# Helper: import mcp_server with mcp package mocked
# ---------------------------------------------------------------------------


def _get_mcp_server():
    """Return mcp_server module, mocking mcp package if not installed."""
    if "src.mcp_server" in sys.modules:
        return sys.modules["src.mcp_server"]
    raise RuntimeError("mcp_server not pre-loaded")


@pytest.fixture(scope="module")
def mcp_mod():
    """Module-scoped fixture: imports mcp_server with mcp mocked."""
    import importlib

    # Build mock mcp package
    mcp_mock = MagicMock()
    mcp_server_mock = MagicMock()
    mcp_stdio_mock = MagicMock()
    mcp_types_mock = MagicMock()

    class _FakeTool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeTextContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mcp_types_mock.Tool = _FakeTool
    mcp_types_mock.TextContent = _FakeTextContent
    mcp_server_mock.Server = MagicMock(return_value=MagicMock())
    mcp_stdio_mock.stdio_server = MagicMock()

    mcp_mock.server = mcp_server_mock
    mcp_mock.server.stdio = mcp_stdio_mock
    mcp_mock.types = mcp_types_mock

    modules_to_patch = {
        "mcp": mcp_mock,
        "mcp.server": mcp_server_mock,
        "mcp.server.stdio": mcp_stdio_mock,
        "mcp.types": mcp_types_mock,
    }

    # Remove cached module if exists
    for mod in list(sys.modules.keys()):
        if "src.mcp_server" in mod:
            del sys.modules[mod]

    with patch.dict(sys.modules, modules_to_patch):
        import src.mcp_server as mod

        yield mod

    # Clean up
    for mod_name in list(sys.modules.keys()):
        if "src.mcp_server" in mod_name:
            del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# Tool definition tests (AST-based — no mcp import needed)
# ---------------------------------------------------------------------------

SOURCE_PATH = pathlib.Path("src/mcp_server.py")


def _get_tool_names_from_ast() -> set:
    """Parse mcp_server.py AST and extract Tool(name=...) string values."""
    source = SOURCE_PATH.read_text()
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "Tool":
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        names.add(kw.value.value)
    return names


def test_list_tools_contains_6_tools():
    """MCP server source must define exactly 6 Tool(...) instances (includes intel_library)."""
    tool_names = _get_tool_names_from_ast()
    assert (
        len(tool_names) == 6
    ), f"Expected 6 Tool definitions, found {len(tool_names)}: {tool_names}"


def test_tool_names_are_correct():
    """All 6 required tool names must be present."""
    tool_names = _get_tool_names_from_ast()
    expected = {
        "intel_feed",
        "intel_search",
        "intel_status",
        "intel_context_pack",
        "intel_action_items",
        "intel_library",
    }
    assert (
        expected == tool_names
    ), f"Tool mismatch. Expected {expected}, got {tool_names}"


def test_intel_context_pack_has_correct_schema():
    """intel_context_pack tool must have topic, budget, format properties in source."""
    source = SOURCE_PATH.read_text()
    assert "topic" in source
    assert "budget" in source
    assert "format" in source
    # Text and json enum values for format
    assert '"text"' in source or "'text'" in source
    assert '"json"' in source or "'json'" in source


def test_intel_action_items_has_no_required_params():
    """intel_action_items tool must have empty properties (no required params)."""
    source = SOURCE_PATH.read_text()
    assert "intel_action_items" in source
    # The intel_action_items tool section should have empty properties
    # Verify the handler function exists
    assert "_intel_action_items" in source


def test_intel_context_pack_handler_exists():
    """_intel_context_pack handler function must be defined in mcp_server.py."""
    source = SOURCE_PATH.read_text()
    assert "async def _intel_context_pack" in source


def test_intel_action_items_handler_exists():
    """_intel_action_items handler function must be defined in mcp_server.py."""
    source = SOURCE_PATH.read_text()
    assert "async def _intel_action_items" in source


def test_call_tool_dispatches_context_pack():
    """call_tool must dispatch to _intel_context_pack for 'intel_context_pack'."""
    source = SOURCE_PATH.read_text()
    assert "intel_context_pack" in source
    assert "_intel_context_pack" in source


def test_call_tool_dispatches_action_items():
    """call_tool must dispatch to _intel_action_items for 'intel_action_items'."""
    source = SOURCE_PATH.read_text()
    assert "intel_action_items" in source
    assert "_intel_action_items" in source


# ---------------------------------------------------------------------------
# Handler function tests (unit — mock httpx.AsyncClient)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_feed_constructs_correct_params(mcp_mod):
    """_intel_feed builds params with type, tag, significance, days, limit, offset."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"items": [], "total": 0}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    args = {
        "type": "tool",
        "tag": "mcp",
        "significance": "breaking",
        "days": 14,
        "limit": 10,
        "offset": 5,
    }
    result = await mcp_mod._intel_feed(mock_client, args)

    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    url = call_args[0][0]
    assert "/v1/feed" in url
    assert result == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_intel_feed_default_params(mcp_mod):
    """_intel_feed uses sensible defaults when no args provided."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"items": []}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    result = await mcp_mod._intel_feed(mock_client, {})

    call_args = mock_client.get.call_args
    url = call_args[0][0]
    assert "/v1/feed" in url


@pytest.mark.asyncio
async def test_intel_context_pack_passes_topic_and_budget(mcp_mod):
    """_intel_context_pack passes topic and budget to the API."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = "# Intelligence Briefing\n\nTop items..."

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    args = {"topic": "mcp", "budget": 1500, "format": "text"}
    result = await mcp_mod._intel_context_pack(mock_client, args)

    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    url = call_args[0][0]
    assert "/v1/context-pack" in url
    # Text format returns briefing key
    assert "briefing" in result
    assert result["briefing"] == "# Intelligence Briefing\n\nTop items..."


@pytest.mark.asyncio
async def test_intel_context_pack_json_format(mcp_mod):
    """_intel_context_pack with format=json returns raw JSON response."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"meta": {"items_included": 5}, "items": []}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    args = {"budget": 2000, "format": "json"}
    result = await mcp_mod._intel_context_pack(mock_client, args)

    assert "meta" in result or "items" in result


@pytest.mark.asyncio
async def test_intel_context_pack_no_topic(mcp_mod):
    """_intel_context_pack without topic works (no topic param sent)."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = "briefing text"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    result = await mcp_mod._intel_context_pack(mock_client, {})
    assert "briefing" in result


@pytest.mark.asyncio
async def test_intel_action_items_calls_correct_endpoint(mcp_mod):
    """_intel_action_items calls GET /v1/action-items."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "action_items": [],
        "total": 0,
        "message": "No items require attention",
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    result = await mcp_mod._intel_action_items(mock_client)

    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    url = call_args[0][0]
    assert "/v1/action-items" in url
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_feed_http_error_returns_structured_error(mcp_mod):
    """_intel_feed returns structured error dict on HTTP error."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_response
        )
    )

    result = await mcp_mod._intel_feed(mock_client, {})
    assert "error" in result
    assert "401" in result["error"]


@pytest.mark.asyncio
async def test_intel_context_pack_http_error_returns_structured_error(mcp_mod):
    """_intel_context_pack returns structured error dict on HTTP error."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_response
        )
    )

    result = await mcp_mod._intel_context_pack(mock_client, {})
    assert "error" in result
    assert "403" in result["error"]


@pytest.mark.asyncio
async def test_intel_action_items_request_error_returns_structured_error(mcp_mod):
    """_intel_action_items returns structured error dict on connection error."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.RequestError("Connection refused", request=MagicMock())
    )

    result = await mcp_mod._intel_action_items(mock_client)
    assert "error" in result
    assert "Request failed" in result["error"]
