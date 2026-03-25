"""Search command — full-text search across intelligence items."""

from __future__ import annotations

from typing import Optional

import typer

from cli.client import get_client
from cli.main import state
from cli.render import (
    console,
    print_json,
    render_empty_state,
    render_search_table,
    stdout_console,
)


def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(20, help="Max results"),
    offset: int = typer.Option(0, help="Offset for pagination"),
    item_type: Optional[str] = typer.Option(
        None,
        "--type",
        help="Filter by primary_type (tool, skill, update, practice, docs)",
    ),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
    significance: Optional[str] = typer.Option(
        None, "--significance", help="Filter by significance tier"
    ),
    days: Optional[int] = typer.Option(
        None, "--days", help="Limit to items from last N days"
    ),
) -> None:
    """Search intelligence items by keyword."""
    client = get_client(base_url=state.api_url)
    data = client.get(
        "/v1/search",
        q=query,
        limit=limit,
        offset=offset,
        type=item_type,
        tag=tag,
        significance=significance,
        days=days,
    )
    items = data["items"]

    if not items:
        console.print(f"[yellow]No results for '{query}'.[/yellow] Try broader terms.")
        return

    if state.json_mode:
        for item in items:
            print_json(item)
    else:
        render_search_table(items)
        stdout_console.print(
            f"[dim]Showing {len(items)} of {data['total']} results[/dim]"
        )
