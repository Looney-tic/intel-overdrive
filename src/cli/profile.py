"""Profile command: scan local environment and sync to server."""

from __future__ import annotations

from pathlib import Path

import typer

from cli.client import get_client
from cli.render import console, print_error, print_json, print_success


def scan_claude_profile() -> dict:
    """Scan local environment for tech stack and Claude skills.

    Detects tech stack from file presence in CWD (and parents),
    and Claude skills from ~/.claude/skills/ and ~/.claude/overdrive/.
    """
    tech_set: set[str] = set()
    skills_list: list[str] = []

    # Detect tech stack from CWD
    cwd = Path.cwd()
    marker_map = {
        "pyproject.toml": "python",
        "requirements.txt": "python",
        "setup.py": "python",
        "package.json": "nodejs",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "Gemfile": "ruby",
        "pom.xml": "java",
        "build.gradle": "java",
        "composer.json": "php",
    }
    for marker, lang in marker_map.items():
        if (cwd / marker).exists():
            tech_set.add(lang)

    # Scan CLAUDE.md for additional language hints
    claude_md = cwd / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text().lower()
        lang_keywords = {
            "python": "python",
            "typescript": "typescript",
            "javascript": "javascript",
            "rust": "rust",
            "golang": "go",
            "ruby": "ruby",
        }
        for keyword, lang in lang_keywords.items():
            if keyword in content:
                tech_set.add(lang)

    # Detect skills from ~/.claude/
    claude_dir = Path.home() / ".claude"
    skills_dir = claude_dir / "skills"
    if skills_dir.is_dir():
        for entry in skills_dir.iterdir():
            if entry.is_dir():
                skills_list.append(entry.name)

    overdrive_dir = claude_dir / "overdrive"
    if overdrive_dir.is_dir():
        for entry in overdrive_dir.iterdir():
            if entry.is_dir() and entry.name not in ("bin", "templates", "references"):
                skills_list.append(entry.name)

    return {"tech_stack": sorted(tech_set), "skills": sorted(skills_list)}


def profile(
    sync: bool = typer.Option(
        False, "--sync", help="Scan local environment and sync profile to server"
    ),
) -> None:
    """View or sync your developer profile."""
    from cli.main import state

    if not sync:
        # Fetch and display current server-side profile
        try:
            client = get_client(base_url=state.api_url)
            response = client.get("/v1/profile")
            if state.json_mode:
                print_json(response)
            else:
                profile_data = response.get("profile", response)
                tech = ", ".join(profile_data.get("tech_stack", [])) or "(none)"
                skills = ", ".join(profile_data.get("skills", [])) or "(none)"
                console.print(f"Tech stack: [cyan]{tech}[/cyan]")
                console.print(f"Skills: [cyan]{skills}[/cyan]")
                console.print(
                    "\nRun [bold]overdrive-intel profile --sync[/bold] to update from local environment."
                )
        except SystemExit:
            raise
        except Exception as exc:
            print_error(f"Failed to fetch profile: {exc}")
            raise typer.Exit(1)
        return

    # Opt-in gate (CLI-06: explicit confirmation before scanning)
    if not typer.confirm(
        "This will scan ~/.claude/ directories and send your tech stack "
        "and skill inventory to the server. Continue?"
    ):
        return

    profile_data = scan_claude_profile()

    # Show what was detected before sending (in TTY mode)
    if not state.json_mode:
        tech = ", ".join(profile_data["tech_stack"]) or "(none)"
        skills = ", ".join(profile_data["skills"]) or "(none)"
        console.print(f"Detected tech stack: [cyan]{tech}[/cyan]")
        console.print(f"Detected skills: [cyan]{skills}[/cyan]")

    try:
        client = get_client(base_url=state.api_url)
        response = client.post("/v1/profile", json=profile_data)
        if state.json_mode:
            print_json(response)
        else:
            print_success(response.get("message", "Profile synced."))
    except SystemExit:
        raise
    except Exception as exc:
        print_error(f"Failed to sync profile: {exc}")
        raise typer.Exit(1)
