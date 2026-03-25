"""Library commands: topics, topic detail, search, recommend."""

from __future__ import annotations

import typer

from cli.client import get_client
from cli.render import print_json, print_error, console

library_app = typer.Typer(help="Browse the knowledge library")


@library_app.command("topics")
def topics() -> None:
    """List all library topics."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    data = client.get("/v1/library/topics")
    if state.json_mode:
        print_json(data)
    else:
        for t in data.get("topics", []):
            count = t.get("item_count", 0)
            console.print(
                f"  [bold]{t['topic']}[/bold] ({count} items) — {t.get('label', '')}"
            )


@library_app.command("topic")
def topic(
    name: str = typer.Argument(..., help="Topic name (e.g. mcp, ai-agents)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max items"),
) -> None:
    """Show items for a specific topic."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    data = client.get(f"/v1/library/topic/{name}", limit=limit)
    if state.json_mode:
        print_json(data)
    else:
        console.print(
            f"\n[bold]{data.get('topic', name)}[/bold] — {data.get('description', '')}\n"
        )
        for item in data.get("items", []):
            score = item.get("evergreen_score", 0)
            console.print(f"  [{score:.1f}] [bold]{item['title']}[/bold]")
            if item.get("summary"):
                console.print(f"       {item['summary'][:120]}")
            console.print(f"       [dim]{item.get('url', '')}[/dim]")


@library_app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results"),
) -> None:
    """Search the knowledge library."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    data = client.get("/v1/library/search", q=query, limit=limit)
    if state.json_mode:
        print_json(data)
    else:
        results = data.get("results", [])
        if not results:
            console.print("[dim]No results found.[/dim]")
            return
        for r in results:
            console.print(f"  [bold]{r['title']}[/bold] [{r.get('entry_type', '')}]")
            if r.get("tldr"):
                console.print(f"    {r['tldr']}")
            console.print(
                f"    [dim]{r.get('topic_path', '')} | score: {r.get('match_score', 0):.2f}[/dim]"
            )


@library_app.command("recommend")
def recommend(
    limit: int = typer.Option(5, "--limit", "-n", help="Max results"),
) -> None:
    """Get profile-matched library recommendations."""
    from cli.main import state

    client = get_client(base_url=state.api_url)
    try:
        data = client.get("/v1/library/recommend", limit=limit)
    except SystemExit:
        raise
    except Exception:
        print_error(
            "Failed to get recommendations. Set your profile first: overdrive-intel profile --sync"
        )
        raise typer.Exit(1)

    if state.json_mode:
        print_json(data)
    else:
        entries = data.get("entries", [])
        matched = data.get("profile_tags_matched", [])
        if matched:
            console.print(f"[dim]Profile tags matched: {', '.join(matched)}[/dim]\n")
        for r in entries:
            console.print(f"  [bold]{r['title']}[/bold] [{r.get('entry_type', '')}]")
            if r.get("tldr"):
                console.print(f"    {r['tldr']}")
