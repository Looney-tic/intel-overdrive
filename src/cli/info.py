"""Info command — fetch full details for a single intelligence item."""

from __future__ import annotations

import uuid as uuid_mod

import typer

from cli.client import get_client
from cli.main import state
from cli.render import print_error, print_json, render_info_panel


def info(
    identifier: str = typer.Argument(..., help="Item UUID, name, or URL"),
) -> None:
    """Show detailed information about a specific item."""
    client = get_client(base_url=state.api_url)

    # Detect UUID vs free-text identifier
    try:
        uuid_mod.UUID(identifier)
        is_uuid = True
    except ValueError:
        is_uuid = False

    if is_uuid:
        item = client.get(f"/v1/info/{identifier}")
    else:
        # Search-then-fetch: find the item by name/URL, then fetch full details
        search_data = client.get("/v1/search", q=identifier, limit=1)
        search_items = search_data["items"]
        if not search_items:
            print_error(f"No item found matching '{identifier}'")
            raise typer.Exit(1)
        item_id = search_items[0]["id"]
        item = client.get(f"/v1/info/{item_id}")

    if state.json_mode:
        print_json(item)
    else:
        render_info_panel(item)
