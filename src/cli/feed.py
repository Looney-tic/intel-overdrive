"""Feed command — list recent intelligence items with filters."""

from __future__ import annotations

from typing import Optional

import typer

from cli.client import get_client
from cli.main import state
from cli.render import print_json, render_empty_state, render_feed_table, stdout_console


def feed(
    days: int = typer.Option(7, help="Number of days to look back"),
    item_type: Optional[str] = typer.Option(
        None, "--type", help="Filter by type (skill, tool, update, practice, docs)"
    ),
    tag: Optional[str] = typer.Option(None, help="Filter by tag"),
    limit: int = typer.Option(20, help="Max items"),
    offset: int = typer.Option(0, help="Offset for pagination"),
    since: Optional[str] = typer.Option(
        None, "--since", help="Return only items newer than ISO8601 timestamp"
    ),
    sort: Optional[str] = typer.Option(
        None, "--sort", help="Sort order: significance or score"
    ),
    new: bool = typer.Option(
        False, "--new", help="Only show unseen items since last check"
    ),
) -> None:
    """Show recent intelligence items from the feed."""
    client = get_client(base_url=state.api_url)
    data = client.get(
        "/v1/feed",
        days=days,
        type=item_type,
        tag=tag,
        limit=limit,
        offset=offset,
        since=since,
        sort=sort,
        new=new if new else None,
    )
    items = data["items"]

    if not items:
        render_empty_state("feed")
        return

    if state.json_mode:
        for item in items:
            print_json(item)
    else:
        render_feed_table(items)
        stdout_console.print(
            f"[dim]Showing {len(items)} of {data['total']} "
            f"(offset {data['offset']})[/dim]"
        )
