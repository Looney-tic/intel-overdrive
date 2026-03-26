"""Output rendering: Rich tables for TTY, NDJSON for piped/--json mode."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# stderr console for status/error messages — keeps stdout clean for JSON piping
console = Console(stderr=True)

# stdout console for Rich table output in TTY mode
stdout_console = Console()


def print_json(data: dict | list) -> None:
    """Print data as NDJSON (one JSON object per line) to stdout."""
    if isinstance(data, list):
        for item in data:
            print(json.dumps(item, default=str))
    else:
        print(json.dumps(data, default=str))


def print_error(msg: str) -> None:
    """Print styled error to stderr."""
    console.print(f"[red]Error:[/red] {msg}")


def print_success(msg: str) -> None:
    """Print styled success message to stderr."""
    console.print(f"[green]{msg}[/green]")


def print_warning(msg: str) -> None:
    """Print styled warning to stderr."""
    console.print(f"[yellow]{msg}[/yellow]")


def render_feed_table(items: list[dict[str, Any]]) -> None:
    """Render feed items as a Rich table to stdout."""
    table = Table(title="Intel Overdrive Feed", show_lines=False)
    table.add_column("Title", style="cyan", no_wrap=False, ratio=4)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Tags", style="green", no_wrap=True)
    table.add_column("Score", justify="right", style="yellow", no_wrap=True)
    table.add_column("Date", style="dim", no_wrap=True)

    for item in items:
        tags = ", ".join(item.get("tags", [])[:3])
        score = f"{item.get('relevance_score', 0):.2f}"
        date = str(item.get("created_at", ""))[:10]
        table.add_row(
            item.get("title", ""), item.get("primary_type", ""), tags, score, date
        )

    stdout_console.print(table)


def render_search_table(items: list[dict[str, Any]]) -> None:
    """Render search results as a Rich table to stdout."""
    table = Table(title="Search Results", show_lines=False)
    table.add_column("Title", style="cyan", no_wrap=False, ratio=4)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Rank", justify="right", style="yellow", no_wrap=True)
    table.add_column("Score", justify="right", style="yellow", no_wrap=True)
    table.add_column("Date", style="dim", no_wrap=True)

    for item in items:
        rank = f"{item.get('rank', 0):.1f}"
        score = f"{item.get('relevance_score', 0):.2f}"
        date = str(item.get("created_at", ""))[:10]
        table.add_row(
            item.get("title", ""), item.get("primary_type", ""), rank, score, date
        )

    stdout_console.print(table)


def render_status_table(
    sources: list[dict[str, Any]], spend_remaining: float, health: str
) -> None:
    """Render system status: sources table + health/spend panel."""
    table = Table(title="Source Status", show_lines=False)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Active", no_wrap=True)
    table.add_column("Last Poll", style="dim", no_wrap=True)
    table.add_column("Errors", justify="right", style="red", no_wrap=True)
    table.add_column("Interval", justify="right", no_wrap=True)

    for src in sources:
        active = "[green]Yes[/green]" if src.get("is_active") else "[red]No[/red]"
        last_poll = str(src.get("last_successful_poll", "never"))[:19]
        errors = str(src.get("consecutive_errors", 0))
        interval = f"{src.get('poll_interval_seconds', 0)}s"
        table.add_row(
            src.get("name", ""),
            src.get("type", ""),
            active,
            last_poll,
            errors,
            interval,
        )

    stdout_console.print(table)
    stdout_console.print(
        Panel(
            f"Pipeline health: [bold]{health}[/bold]\n"
            f"Daily spend remaining: [yellow]${spend_remaining:.2f}[/yellow]",
            title="System",
        )
    )


def render_info_panel(item: dict[str, Any]) -> None:
    """Render a single intel item as a detailed Rich panel."""
    lines = [
        f"[bold cyan]{item.get('title', 'Untitled')}[/bold cyan]",
        "",
        f"[dim]ID:[/dim] {item.get('id', '')}",
        f"[dim]URL:[/dim] {item.get('url', '')}",
        f"[dim]Type:[/dim] {item.get('primary_type', '')}",
        f"[dim]Status:[/dim] {item.get('status', '')}",
        f"[dim]Tags:[/dim] {', '.join(item.get('tags', []))}",
        "",
        f"[dim]Relevance:[/dim] {item.get('relevance_score', 0):.2f}  "
        f"[dim]Quality:[/dim] {item.get('quality_score', 0):.2f}  "
        f"[dim]Confidence:[/dim] {item.get('confidence_score', 0):.2f}",
        f"[dim]Created:[/dim] {item.get('created_at', '')}",
    ]

    quality_details = item.get("quality_score_details")
    if quality_details:
        sub_parts = []
        for key in ("maintenance", "security", "compatibility"):
            val = quality_details.get(key)
            if val is not None:
                sub_parts.append(f"{key}: {val:.2f}")
        if sub_parts:
            lines.append(f"[dim]Quality sub-scores:[/dim] {', '.join(sub_parts)}")

    excerpt = item.get("excerpt")
    if excerpt:
        lines.extend(["", f"[bold]Excerpt:[/bold]", excerpt])

    summary = item.get("summary")
    if summary:
        lines.extend(["", f"[bold]Summary:[/bold]", summary])

    stdout_console.print(Panel("\n".join(lines), title="Intel Item"))


def render_empty_state(command: str) -> None:
    """Print helpful suggestion when no results are returned."""
    if command == "search":
        hint = "Try a broader query or different keywords"
    else:
        hint = f"Try: [bold]overdrive-intel {command} --days 30[/bold] or use different filters"
    console.print(f"[yellow]No results found.[/yellow] {hint}")
