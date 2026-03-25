#!/usr/bin/env python3
"""Check for breaking changes in the AI coding ecosystem affecting project dependencies.

Used by the GitHub Action (action.yml) and can be run standalone.
Reads dependencies from pyproject.toml or package.json, queries the overdrive-intel
feed for breaking changes, and filters results to items matching project dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Check for breaking changes affecting your project dependencies"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Lookback window in days (default: 7)",
    )
    parser.add_argument(
        "--fail-on-breaking",
        type=str,
        default="false",
        help='Exit with code 1 if breaking changes found (default: "false")',
    )
    return parser.parse_args(argv)


def detect_dependencies(project_dir: str = ".") -> list[str]:
    """Detect project dependencies from pyproject.toml or package.json.

    Returns a list of normalized package names (lowercase, stripped of version specifiers).
    """
    deps: list[str] = []

    pyproject_path = Path(project_dir) / "pyproject.toml"
    if pyproject_path.exists():
        deps.extend(_parse_pyproject(pyproject_path))

    package_json_path = Path(project_dir) / "package.json"
    if package_json_path.exists():
        deps.extend(_parse_package_json(package_json_path))

    return deps


def _parse_pyproject(path: Path) -> list[str]:
    """Parse dependencies from pyproject.toml."""
    try:
        import tomllib
    except ImportError:
        # Python < 3.11 fallback
        try:
            import tomli as tomllib  # type: ignore[no-redefine]
        except ImportError:
            print("Warning: Cannot parse pyproject.toml (tomllib/tomli not available)")
            return []

    with open(path, "rb") as f:
        data = tomllib.load(f)

    raw_deps = data.get("project", {}).get("dependencies", [])
    return [_normalize_dep_name(dep) for dep in raw_deps]


def _parse_package_json(path: Path) -> list[str]:
    """Parse dependencies from package.json."""
    with open(path) as f:
        data = json.load(f)

    deps: list[str] = []
    for section in ("dependencies", "devDependencies"):
        section_deps = data.get(section, {})
        if isinstance(section_deps, dict):
            deps.extend(section_deps.keys())

    return [name.lower() for name in deps]


def _normalize_dep_name(dep_spec: str) -> str:
    """Extract and normalize package name from a dependency specifier.

    Examples:
        'requests>=2.0' -> 'requests'
        'SQLAlchemy[asyncio]>=2.0.36' -> 'sqlalchemy'
        'pydantic-settings==2.7.0' -> 'pydantic-settings'
    """
    # Strip extras (e.g., [asyncio])
    name = dep_spec.split("[")[0]
    # Strip version specifiers
    for sep in (">=", "<=", "==", "!=", "~=", ">", "<", ";"):
        name = name.split(sep)[0]
    return name.strip().lower()


def fetch_breaking_feed(days: int) -> list[dict]:
    """Fetch breaking changes from overdrive-intel feed.

    Returns parsed JSON list of feed items.
    """
    cmd = [
        "overdrive-intel",
        "feed",
        "--days",
        str(days),
        "--type",
        "update",
        "--significance",
        "breaking",
        "--json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print(
            "Error: overdrive-intel CLI not found. "
            "Install it with: pip install overdrive-intel"
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: overdrive-intel feed command timed out after 30s")
        sys.exit(1)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "api_key" in stderr.lower() or "unauthorized" in stderr.lower():
            print(
                "Error: OVERDRIVE_API_KEY not set or invalid. "
                "Set it as an environment variable or GitHub secret."
            )
            sys.exit(1)
        print(f"Error: overdrive-intel feed failed: {stderr}")
        sys.exit(1)

    # Parse JSON output (may be JSON array or JSON lines)
    stdout = result.stdout.strip()
    if not stdout:
        return []

    try:
        data = json.loads(stdout)
        if isinstance(data, list):
            return data
        return [data]
    except json.JSONDecodeError:
        # Try JSON lines format
        items = []
        for line in stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return items


def filter_by_dependencies(items: list[dict], deps: list[str]) -> list[dict]:
    """Filter feed items to those matching project dependencies.

    Matches dependency names against item tags and title (case-insensitive).
    """
    if not deps:
        return []

    dep_set = {d.lower() for d in deps}
    matches = []

    for item in items:
        # Check tags
        tags = item.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = [tags]

        tag_match = any(tag.lower() in dep_set for tag in tags)

        # Check title for dependency mentions
        title = item.get("title", "").lower()
        title_match = any(dep in title for dep in dep_set)

        if tag_match or title_match:
            matches.append(item)

    return matches


def format_report(matches: list[dict]) -> str:
    """Format matching items as a human-readable report."""
    if not matches:
        return "No breaking changes found affecting your dependencies."

    lines = [f"Found {len(matches)} breaking change(s) affecting your dependencies:\n"]
    for i, item in enumerate(matches, 1):
        title = item.get("title", "Unknown")
        url = item.get("url", "")
        significance = item.get("significance", "breaking")
        tags = item.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = [tags]
        tag_str = ", ".join(tags) if tags else "N/A"

        lines.append(f"  {i}. [{significance.upper()}] {title}")
        if url:
            lines.append(f"     URL: {url}")
        lines.append(f"     Tags: {tag_str}")
        lines.append("")

    return "\n".join(lines)


def set_github_output(name: str, value: str) -> None:
    """Set a GitHub Actions output variable."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            if "\n" in value:
                # Use delimiter-based multiline output
                import uuid

                delimiter = f"ghadelimiter_{uuid.uuid4()}"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns exit code."""
    args = parse_args(argv)

    # Detect project dependencies
    deps = detect_dependencies()
    if not deps:
        print("No pyproject.toml or package.json found. Nothing to check.")
        set_github_output("breaking_count", "0")
        set_github_output("report", "No project files found.")
        return 0

    print(f"Detected {len(deps)} dependencies from project files.")

    # Fetch breaking changes
    items = fetch_breaking_feed(args.days)
    print(f"Found {len(items)} breaking change(s) in the last {args.days} day(s).")

    # Filter by project dependencies
    matches = filter_by_dependencies(items, deps)
    print(f"Matched {len(matches)} breaking change(s) to your dependencies.")

    # Generate report
    report = format_report(matches)
    print("\n" + report)

    # Set GitHub Action outputs
    set_github_output("breaking_count", str(len(matches)))
    set_github_output("report", report)

    # Exit code based on --fail-on-breaking flag
    fail_on_breaking = args.fail_on_breaking.lower() == "true"
    if fail_on_breaking and len(matches) > 0:
        print(f"\nFailing action: {len(matches)} breaking change(s) found.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
