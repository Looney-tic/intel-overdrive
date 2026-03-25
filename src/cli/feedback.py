"""Feedback commands: report false positives (noise) and false negatives (miss)."""

from __future__ import annotations

import uuid

import typer

from cli.client import get_client
from cli.render import print_error, print_json, print_success

feedback_app = typer.Typer(help="Report false positives or false negatives")


@feedback_app.command("miss")
def report_miss(
    url: str = typer.Argument(..., help="URL of missed item"),
    notes: str = typer.Option(None, help="Additional context"),
) -> None:
    """Report a missed item (false negative)."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    payload: dict = {"report_type": "miss", "url": url}
    if notes is not None:
        payload["notes"] = notes

    response = client.post("/v1/feedback", json=payload)
    if state.json_mode:
        print_json(response)
    else:
        print_success("Feedback recorded. Thank you!")


@feedback_app.command("noise")
def report_noise(
    item_id: str = typer.Argument(..., help="Item ID to report as noise"),
    notes: str = typer.Option(None, help="Additional context"),
) -> None:
    """Report a noisy/irrelevant item (false positive)."""
    from cli.main import state

    # Validate UUID format
    try:
        uuid.UUID(item_id)
    except ValueError:
        print_error(f"Invalid UUID: '{item_id}'")
        raise typer.Exit(1)

    client = get_client(base_url=state.api_url)
    payload: dict = {"report_type": "noise", "item_id": item_id}
    if notes is not None:
        payload["notes"] = notes

    response = client.post("/v1/feedback", json=payload)
    if state.json_mode:
        print_json(response)
    else:
        print_success("Feedback recorded. Thank you!")
