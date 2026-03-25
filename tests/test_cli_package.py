"""
CLI package smoke tests.

Tests that overdrive-intel CLI is installable, --help works, and
pyproject.toml packaging configuration is correct.
"""
import importlib
from pathlib import Path

import tomllib
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

PYPROJECT_PATH = Path(__file__).parent.parent / "pyproject.toml"


def _load_pyproject() -> dict:
    """Load and parse pyproject.toml."""
    return tomllib.loads(PYPROJECT_PATH.read_text())


# -- Original CLI smoke tests --


def test_help_exits_zero():
    """CLI-01: --help exits 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert (
        "overdrive-intel" in result.output.lower()
        or "overdrive" in result.output.lower()
    )


def test_help_shows_all_commands():
    """CLI-01: --help lists all registered commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["feed", "search", "info", "status", "auth", "profile", "feedback"]:
        assert cmd in result.output, f"Command '{cmd}' not found in --help output"


def test_version_flag():
    """CLI-01: --version prints version string."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# -- Packaging verification tests (16-01) --


def test_build_system_section():
    """16-01: pyproject.toml has [build-system] with hatchling backend."""
    data = _load_pyproject()
    assert "build-system" in data, "Missing [build-system] section"
    bs = data["build-system"]
    assert "requires" in bs, "Missing build-system.requires"
    assert any(
        "hatchling" in r for r in bs["requires"]
    ), "hatchling not in build-system.requires"
    assert (
        bs.get("build-backend") == "hatchling.build"
    ), f"build-backend is '{bs.get('build-backend')}', expected 'hatchling.build'"


def test_wheel_packages_list():
    """16-01: [tool.hatch.build.targets.wheel] lists all required src packages."""
    data = _load_pyproject()
    packages = (
        data.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("packages", [])
    )
    required = {
        "src/cli",
        "src/api",
        "src/core",
        "src/models",
        "src/services",
        "src/workers",
    }
    actual = set(packages)
    missing = required - actual
    assert not missing, f"Missing packages in wheel config: {missing}"


def test_entry_point_module_importable():
    """16-01: cli.main module is importable and has an 'app' attribute."""
    mod = importlib.import_module("cli.main")
    assert hasattr(mod, "app"), "cli.main has no 'app' attribute"


def test_entry_point_app_is_typer():
    """16-01: cli.main.app is a Typer instance."""
    import typer

    mod = importlib.import_module("cli.main")
    assert isinstance(
        mod.app, typer.Typer
    ), f"cli.main.app is {type(mod.app).__name__}, expected Typer"
