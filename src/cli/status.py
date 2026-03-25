"""Status command — show system health, source status, and spend."""

from __future__ import annotations

from cli.client import get_client
from cli.main import state
from cli.render import print_json, render_status_table


def status() -> None:
    """Show pipeline health, source status, and remaining spend."""
    client = get_client(base_url=state.api_url)
    data = client.get("/v1/status")

    if state.json_mode:
        print_json(data)
    else:
        render_status_table(
            data["sources"],
            data["daily_spend_remaining"],
            data["pipeline_health"],
        )
