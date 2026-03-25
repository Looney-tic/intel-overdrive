"""Alert management commands: configure Slack webhooks and view alert status."""

from __future__ import annotations

import typer

from cli.client import get_client
from cli.render import print_error, print_json, print_success

alerts_app = typer.Typer(help="Manage alert rules and Slack notifications")


@alerts_app.command("set-slack")
def set_slack(
    webhook_url: str = typer.Argument(..., help="Slack incoming webhook URL"),
) -> None:
    """Configure Slack webhook for alert delivery (ALERT-06)."""
    from cli.main import state  # lazy import to avoid circular

    client = get_client(base_url=state.api_url)
    response = client.post(
        "/v1/alerts/slack-webhook", json={"webhook_url": webhook_url}
    )
    if state.json_mode:
        print_json(response)
    else:
        print_success("Slack webhook configured successfully.")


@alerts_app.command("status")
def alerts_status() -> None:
    """Show active alert rules and delivery status (ALERT-07)."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    response = client.get("/v1/alerts/status")
    if state.json_mode:
        print_json(response)
    else:
        # APIClient.get() already returns dict, not httpx.Response
        data = response
        rules = data.get("rules", [])
        if not rules:
            typer.echo("No active alert rules.")
            return
        typer.echo(f"Active alert rules: {len(rules)}")
        for rule in rules:
            channels = rule.get("delivery_channels", {})
            has_slack = "slack_webhook" in channels
            cooldown_label = (
                "ON COOLDOWN"
                if rule.get("is_on_cooldown")
                else f"{rule['cooldown_minutes']}min"
            )
            last_fired = rule.get("last_fired_at", "never")
            typer.echo(
                f"  - {rule['name']} (keywords: {', '.join(rule['keywords'])})"
                f" [Slack: {'Yes' if has_slack else 'No'}]"
                f" [Cooldown: {cooldown_label}]"
                f" [Last fired: {last_fired}]"
            )


@alerts_app.command("list")
def alerts_list() -> None:
    """List all configured alert rules (ALERT-07)."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    response = client.get("/v1/alerts/status")
    if state.json_mode:
        print_json(response)
    else:
        data = response
        rules = data.get("rules", [])
        if not rules:
            typer.echo("No alert rules configured.")
            return
        typer.echo(f"Alert rules ({len(rules)}):")
        for rule in rules:
            channels = ", ".join(rule.get("delivery_channels", {}).keys()) or "none"
            typer.echo(
                f"  [{rule.get('id', '?')}] {rule['name']}"
                f" — keywords: {', '.join(rule['keywords'])}"
                f" — channels: {channels}"
            )
