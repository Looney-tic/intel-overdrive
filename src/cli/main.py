"""Intel Overdrive CLI — root Typer app with global options."""

from __future__ import annotations

import sys
from dataclasses import dataclass

import typer

from cli.config import get_api_url

__version__ = "0.1.0"


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"overdrive-intel {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="overdrive-intel",
    help="Intel Overdrive CLI — Claude Code ecosystem intelligence",
    no_args_is_help=True,
)


@dataclass
class AppState:
    """Shared state populated by root callback, read by all commands."""

    json_mode: bool = False
    api_url: str = ""


state = AppState()


@app.callback()
def main(
    json: bool = typer.Option(False, "--json", help="Force JSON output"),
    api_url: str = typer.Option(
        None,
        "--api-url",
        envvar="OVERDRIVE_API_URL",
        help="API base URL",
    ),
    version: bool = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Intel Overdrive CLI — Claude Code ecosystem intelligence."""
    state.json_mode = json or not sys.stdout.isatty()
    state.api_url = api_url or get_api_url()


# -- Sub-apps (real implementations) --

from cli.auth import auth_app  # noqa: E402

app.add_typer(auth_app, name="auth")

from cli.feedback import feedback_app  # noqa: E402
from cli.profile import profile  # noqa: E402
from cli.alerts import alerts_app  # noqa: E402

app.add_typer(feedback_app, name="feedback")
app.add_typer(alerts_app, name="alerts")
app.command()(profile)


# -- Read commands (Plan 02) --

from cli.feed import feed  # noqa: E402
from cli.info import info  # noqa: E402
from cli.search import search  # noqa: E402
from cli.status import status  # noqa: E402

app.command()(feed)
app.command()(search)
app.command()(info)
app.command()(status)

from cli.hook import hook_app  # noqa: E402
from cli.library import library_app  # noqa: E402
from cli.setup import setup_app  # noqa: E402
from cli.admin import admin_app  # noqa: E402

app.add_typer(hook_app, name="hook")
app.add_typer(library_app, name="library")
app.add_typer(setup_app, name="setup")
app.add_typer(admin_app, name="admin")
