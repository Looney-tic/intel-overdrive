"""Tests for intel_library MCP tool in src/mcp_server.py.

Uses AST analysis (no mcp package import) and sys.modules patching
for handler function tests. Pattern from Phase 14 (test_mcp_server.py).
"""

import ast
import json
import pathlib
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


SOURCE_PATH = pathlib.Path("src/mcp_server.py")


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


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


def _get_required_fields_from_ast(tool_name: str) -> list:
    """Extract required fields list for a named Tool from AST source text."""
    source = SOURCE_PATH.read_text()
    # Find the 'required' list associated with the intel_library tool
    # by looking for the tool's name followed by its inputSchema
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "Tool":
                name_val = None
                required_fields = []
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        name_val = kw.value.value
                    if kw.arg == "inputSchema":
                        # Walk the inputSchema dict for "required" key
                        schema = kw.value
                        if isinstance(schema, ast.Dict):
                            for k, v in zip(schema.keys, schema.values):
                                if (
                                    isinstance(k, ast.Constant)
                                    and k.value == "required"
                                    and isinstance(v, ast.List)
                                ):
                                    required_fields = [
                                        elt.value
                                        for elt in v.elts
                                        if isinstance(elt, ast.Constant)
                                    ]
                if name_val == tool_name:
                    return required_fields
    return []


# ---------------------------------------------------------------------------
# Tool definition tests (AST-based)
# ---------------------------------------------------------------------------


def test_mcp_intel_library_tool_defined():
    """mcp_server.py must define an 'intel_library' Tool."""
    tool_names = _get_tool_names_from_ast()
    assert (
        "intel_library" in tool_names
    ), f"'intel_library' not found in tool names: {tool_names}"


def test_mcp_intel_library_description():
    """intel_library description must contain 'how to' and 'best practices'."""
    source = SOURCE_PATH.read_text()
    # Both keywords appear in the intel_library description block
    assert "how to" in source.lower()
    assert "best practices" in source.lower()
    # Verify handler exists
    assert "intel_library" in source
    assert "_intel_library" in source


def test_mcp_intel_library_required_query():
    """intel_library inputSchema must have 'query' in required list."""
    required = _get_required_fields_from_ast("intel_library")
    assert "query" in required, f"'query' not in required fields: {required}"


def test_mcp_tool_count():
    """MCP server must define exactly 6 tools (was 5, now includes intel_library)."""
    tool_names = _get_tool_names_from_ast()
    assert (
        len(tool_names) == 6
    ), f"Expected 6 Tool definitions, found {len(tool_names)}: {tool_names}"


# ---------------------------------------------------------------------------
# Module-scoped fixture for handler tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mcp_mod():
    """Imports mcp_server with mcp package mocked (same pattern as test_mcp_server.py)."""
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

    for mod in list(sys.modules.keys()):
        if "src.mcp_server" in mod:
            del sys.modules[mod]

    with patch.dict(sys.modules, modules_to_patch):
        import src.mcp_server as mod

        yield mod

    for mod_name in list(sys.modules.keys()):
        if "src.mcp_server" in mod_name:
            del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# Handler function tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_library_search_calls_correct_endpoint(mcp_mod):
    """_intel_library calls GET /v1/library/search with query param."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "items": [],
        "total": 0,
        "query_understood": False,
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    args = {"query": "MCP security best practices", "limit": 5}
    result = await mcp_mod._intel_library(mock_client, args)

    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    url = call_args[0][0]
    assert "/v1/library/search" in url

    # Empty search returns empty result
    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_intel_library_topic_fallback(mcp_mod):
    """_intel_library falls back to topic endpoint when search returns empty and topic given."""
    # First call: search returns empty
    mock_search_response = MagicMock()
    mock_search_response.raise_for_status = MagicMock()
    mock_search_response.json.return_value = {"items": [], "total": 0}

    # Second call: topic returns items
    mock_topic_response = MagicMock()
    mock_topic_response.raise_for_status = MagicMock()
    mock_topic_response.json.return_value = {
        "topic": "mcp",
        "items": [
            {
                "id": "abc",
                "title": "MCP Item",
                "summary": "A summary",
                "evergreen_score": 0.9,
            }
        ],
        "total": 1,
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[mock_search_response, mock_topic_response])

    args = {"query": "mcp best practices", "topic": "mcp", "limit": 5}
    result = await mcp_mod._intel_library(mock_client, args)

    # Should have made 2 calls (search + fallback topic)
    assert mock_client.get.call_count == 2

    # Second call should be to /v1/library/topic/mcp
    second_call_url = mock_client.get.call_args_list[1][0][0]
    assert "/v1/library/topic/mcp" in second_call_url

    # Result should include fallback info
    assert result.get("fallback") == "topic/mcp"


@pytest.mark.asyncio
async def test_intel_library_http_error_returns_structured_error(mcp_mod):
    """_intel_library returns structured error dict on HTTP error."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_response
        )
    )

    result = await mcp_mod._intel_library(mock_client, {"query": "test"})
    assert "error" in result
    assert "401" in result["error"]


@pytest.mark.asyncio
async def test_intel_library_request_error_returns_structured_error(mcp_mod):
    """_intel_library returns structured error dict on connection error."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.RequestError("Connection refused", request=MagicMock())
    )

    result = await mcp_mod._intel_library(mock_client, {"query": "mcp patterns"})
    assert "error" in result
    assert "Request failed" in result["error"]
