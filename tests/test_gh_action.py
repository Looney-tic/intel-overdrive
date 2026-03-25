"""Tests for GitHub Action check_breaking.py script and MCP server.json."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from check_breaking import (
    detect_dependencies,
    filter_by_dependencies,
    format_report,
    main,
)


class TestParsePyprojectDependencies:
    """Test 1: Parse pyproject.toml dependencies."""

    def test_parse_pyproject_dependencies(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\n"
            'name = "test-project"\n'
            "dependencies = [\n"
            '    "requests>=2.0",\n'
            '    "SQLAlchemy[asyncio]>=2.0.36",\n'
            '    "pydantic-settings==2.7.0",\n'
            '    "httpx>=0.28.1",\n'
            "]\n"
        )
        deps = detect_dependencies(str(tmp_path))
        assert "requests" in deps
        assert "sqlalchemy" in deps  # normalized lowercase, extras stripped
        assert "pydantic-settings" in deps
        assert "httpx" in deps
        assert len(deps) == 4


class TestParsePackageJsonDependencies:
    """Test 2: Parse package.json dependencies."""

    def test_parse_package_json_dependencies(self, tmp_path: Path) -> None:
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps(
                {
                    "name": "test-project",
                    "dependencies": {
                        "react": "^18.0.0",
                        "next": "^14.0.0",
                    },
                    "devDependencies": {
                        "typescript": "^5.0.0",
                        "eslint": "^8.0.0",
                    },
                }
            )
        )
        deps = detect_dependencies(str(tmp_path))
        assert "react" in deps
        assert "next" in deps
        assert "typescript" in deps
        assert "eslint" in deps
        assert len(deps) == 4


class TestNoProjectFile:
    """Test 3: No project file exits cleanly."""

    def test_no_project_file_exits_cleanly(self, tmp_path: Path) -> None:
        deps = detect_dependencies(str(tmp_path))
        assert deps == []

    def test_main_no_project_file_exit_0(self, tmp_path: Path) -> None:
        """Running main() in a directory with no project files returns 0."""
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            exit_code = main(["--days", "7", "--fail-on-breaking", "false"])
            assert exit_code == 0
        finally:
            os.chdir(original_dir)


class TestFilterBreakingByDeps:
    """Test 4: Filter breaking changes by project dependencies."""

    def test_filter_breaking_by_deps(self) -> None:
        items = [
            {"title": "React 19 breaking changes", "tags": ["react", "frontend"]},
            {"title": "SQLAlchemy 3.0 migration guide", "tags": ["sqlalchemy", "orm"]},
            {"title": "New Claude model released", "tags": ["anthropic", "claude"]},
            {"title": "httpx drops Python 3.10 support", "tags": ["httpx", "python"]},
            {"title": "VS Code extension API changes", "tags": ["vscode"]},
        ]
        deps = ["react", "sqlalchemy", "httpx", "pydantic"]

        matches = filter_by_dependencies(items, deps)
        assert len(matches) == 3
        titles = [m["title"] for m in matches]
        assert "React 19 breaking changes" in titles
        assert "SQLAlchemy 3.0 migration guide" in titles
        assert "httpx drops Python 3.10 support" in titles


class TestNoBreakingExit0:
    """Test 5: No breaking changes returns exit code 0."""

    @patch("check_breaking.fetch_breaking_feed")
    def test_no_breaking_exit_0(self, mock_feed: MagicMock, tmp_path: Path) -> None:
        mock_feed.return_value = []
        # Create a pyproject.toml so deps are detected
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\ndependencies = ["requests>=2.0"]\n'
        )
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            exit_code = main(["--days", "7", "--fail-on-breaking", "true"])
            assert exit_code == 0
        finally:
            os.chdir(original_dir)


class TestBreakingWithFailFlag:
    """Test 6: Breaking changes with --fail-on-breaking=true exits 1."""

    @patch("check_breaking.fetch_breaking_feed")
    def test_breaking_with_fail_flag_exit_1(
        self, mock_feed: MagicMock, tmp_path: Path
    ) -> None:
        mock_feed.return_value = [
            {"title": "requests 3.0 breaking API", "tags": ["requests"]},
        ]
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\ndependencies = ["requests>=2.0"]\n'
        )
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            exit_code = main(["--days", "7", "--fail-on-breaking", "true"])
            assert exit_code == 1
        finally:
            os.chdir(original_dir)


class TestBreakingWithoutFailFlag:
    """Test 7: Breaking changes with --fail-on-breaking=false exits 0."""

    @patch("check_breaking.fetch_breaking_feed")
    def test_breaking_without_fail_flag_exit_0(
        self, mock_feed: MagicMock, tmp_path: Path
    ) -> None:
        mock_feed.return_value = [
            {"title": "requests 3.0 breaking API", "tags": ["requests"]},
        ]
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\ndependencies = ["requests>=2.0"]\n'
        )
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            exit_code = main(["--days", "7", "--fail-on-breaking", "false"])
            assert exit_code == 0
        finally:
            os.chdir(original_dir)


class TestServerJsonValidSchema:
    """Test 8: server.json has valid schema and required fields."""

    def test_server_json_valid_schema(self) -> None:
        server_json_path = (
            Path(__file__).parent.parent / "overdrive-intel-mcp" / "server.json"
        )
        assert server_json_path.exists(), f"server.json not found at {server_json_path}"

        with open(server_json_path) as f:
            data = json.load(f)

        # Required top-level fields
        assert "$schema" in data, "Missing $schema field"
        assert "name" in data, "Missing name field"
        assert "description" in data, "Missing description field"
        assert "version" in data, "Missing version field"
        assert "packages" in data, "Missing packages field"

        # Schema URL is correct
        assert "modelcontextprotocol.io" in data["$schema"]

        # Packages structure
        assert isinstance(data["packages"], list)
        assert len(data["packages"]) >= 1
        pkg = data["packages"][0]
        assert pkg["registry"] == "npm"
        assert pkg["name"] == "overdrive-intel-mcp"

        # Tools structure — consolidated to single overdrive_intel tool in Plan 23-01
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 1

        tool_names = {t["name"] for t in data["tools"]}
        expected_tools = {"overdrive_intel"}
        assert (
            tool_names == expected_tools
        ), f"Tool mismatch: {tool_names} != {expected_tools}"
