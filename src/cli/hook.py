"""CLI subcommand for Claude Code hook management.

Provides `overdrive-intel hook install` which:
1. Copies the hook script to ~/.local/bin/
2. Patches ~/.claude/settings.json to register the SessionStart hook
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

hook_app = typer.Typer(help="Claude Code hook management")


def _find_hook_script() -> Path:
    """Locate the hook script in the package distribution."""
    # Check common locations: relative to this file, package root, or installed path
    candidates = [
        Path(__file__).resolve().parent.parent.parent
        / "scripts"
        / "overdrive-intel-hook.sh",
        Path.home() / ".local" / "bin" / "overdrive-intel-hook.sh",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find overdrive-intel-hook.sh. "
        "Ensure the package was installed correctly."
    )


@hook_app.command()
def install() -> None:
    """Install overdrive-intel as a Claude Code SessionStart hook."""
    settings_path = Path.home() / ".claude" / "settings.json"

    # Load or create settings
    if settings_path.exists():
        with open(settings_path) as f:
            settings = json.load(f)
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = {}

    # Ensure hooks.SessionStart list exists
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    # Check for existing registration
    for entry in settings["hooks"]["SessionStart"]:
        hooks_list = entry.get("hooks", [])
        for h in hooks_list:
            if "overdrive-intel-hook.sh" in h.get("command", ""):
                typer.echo("Hook already installed")
                return

    # Append new hook entry
    hook_entry = {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": "bash ~/.local/bin/overdrive-intel-hook.sh",
            }
        ],
    }
    settings["hooks"]["SessionStart"].append(hook_entry)

    # Write settings back
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    # Copy hook script to ~/.local/bin/
    dest_dir = Path.home() / ".local" / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "overdrive-intel-hook.sh"

    try:
        source = _find_hook_script()
        shutil.copy2(source, dest)
        typer.echo(f"Installed overdrive-intel SessionStart hook")
        typer.echo(f"  Script: {dest}")
        typer.echo(f"  Config: {settings_path}")
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
