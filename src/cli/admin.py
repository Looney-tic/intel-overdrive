"""Admin commands — operator-only, requires DATABASE_URL (direct DB access)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import typer

from cli.render import console, print_error, print_success, print_json

admin_app = typer.Typer(help="Admin commands (requires DATABASE_URL)")


def _get_engine():
    """Create async engine from DATABASE_URL env var."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print_error("DATABASE_URL not set. Admin commands require direct DB access.")
        raise typer.Exit(1)
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(db_url)


@admin_app.command("users")
def list_users(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List all users with usage stats."""

    async def _run():
        from sqlalchemy import text

        engine = _get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                SELECT
                    u.id, u.email, u.is_active, u.tier,
                    u.created_at,
                    COUNT(k.id) AS key_count,
                    SUM(k.usage_count) AS total_requests,
                    MAX(k.last_used_at) AS last_active
                FROM users u
                LEFT JOIN api_keys k ON k.user_id = u.id AND k.is_active = true
                GROUP BY u.id
                ORDER BY last_active DESC NULLS LAST
            """
                )
            )
            rows = result.mappings().all()
        await engine.dispose()
        return [dict(r) for r in rows]

    users = asyncio.run(_run())

    if json_output:
        for u in users:
            for k in ("id", "created_at", "last_active"):
                if u.get(k) and hasattr(u[k], "isoformat"):
                    u[k] = u[k].isoformat()
        print_json({"users": users, "total": len(users)})
        return

    console.print(f"\n[bold]Users ({len(users)})[/bold]\n")
    for u in users:
        active_mark = (
            "[green]active[/green]" if u["is_active"] else "[red]inactive[/red]"
        )
        tier = u["tier"] or "free"
        reqs = u["total_requests"] or 0
        last = ""
        if u["last_active"]:
            last_dt = u["last_active"]
            if hasattr(last_dt, "strftime"):
                last = last_dt.strftime("%Y-%m-%d %H:%M")
            else:
                last = str(last_dt)
        else:
            last = "never"
        console.print(
            f"  {active_mark} [bold]{u['email']}[/bold] "
            f"({tier}) — {reqs} requests, {u['key_count']} keys, last: {last}"
        )
    console.print()


@admin_app.command("user")
def user_detail(
    email: str = typer.Argument(..., help="User email"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show detailed info for a specific user."""

    async def _run():
        from sqlalchemy import text

        engine = _get_engine()
        async with engine.begin() as conn:
            user_result = await conn.execute(
                text("SELECT * FROM users WHERE email = :email"), {"email": email}
            )
            user = user_result.mappings().first()
            if not user:
                return None, []

            keys_result = await conn.execute(
                text(
                    "SELECT id, key_prefix, name, is_active, usage_count, last_used_at, created_at "
                    "FROM api_keys WHERE user_id = :uid ORDER BY created_at"
                ),
                {"uid": user["id"]},
            )
            keys = [dict(r) for r in keys_result.mappings().all()]
        await engine.dispose()
        return dict(user), keys

    user, keys = asyncio.run(_run())

    if not user:
        print_error(f"User not found: {email}")
        raise typer.Exit(1)

    if json_output:
        for k in ("id", "created_at"):
            if user.get(k) and hasattr(user[k], "isoformat"):
                user[k] = user[k].isoformat()
        for key in keys:
            for k in ("last_used_at", "created_at"):
                if key.get(k) and hasattr(key[k], "isoformat"):
                    key[k] = key[k].isoformat()
        print_json({"user": user, "keys": keys})
        return

    console.print(f"\n[bold]{user['email']}[/bold]")
    console.print(f"  ID: {user['id']}")
    console.print(f"  Active: {'yes' if user['is_active'] else 'no'}")
    console.print(f"  Tier: {user.get('tier') or 'free'}")
    console.print(f"  Profile: {user.get('profile') or 'not set'}")
    console.print(f"  Created: {user['created_at']}")
    console.print(f"\n  [bold]API Keys ({len(keys)}):[/bold]")
    for key in keys:
        status = "[green]active[/green]" if key["is_active"] else "[red]revoked[/red]"
        console.print(
            f"    {status} {key['key_prefix']}... "
            f"({key.get('name') or 'unnamed'}) — "
            f"{key['usage_count']} requests"
        )
    console.print()


@admin_app.command("revoke")
def revoke_user(
    email: str = typer.Argument(..., help="User email to deactivate"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Deactivate a user and revoke all their API keys."""
    if not confirm:
        typer.confirm(f"Deactivate user {email} and revoke all keys?", abort=True)

    async def _run():
        from sqlalchemy import text

        engine = _get_engine()
        async with engine.begin() as conn:
            user_result = await conn.execute(
                text(
                    "UPDATE users SET is_active = false WHERE email = :email RETURNING id"
                ),
                {"email": email},
            )
            user = user_result.fetchone()
            if not user:
                return False, 0

            keys_result = await conn.execute(
                text("UPDATE api_keys SET is_active = false WHERE user_id = :uid"),
                {"uid": user[0]},
            )
            return True, keys_result.rowcount
        await engine.dispose()

    found, key_count = asyncio.run(_run())

    if not found:
        print_error(f"User not found: {email}")
        raise typer.Exit(1)

    print_success(f"Deactivated {email} — {key_count} key(s) revoked.")


@admin_app.command("stats")
def stats(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show system-wide usage statistics."""

    async def _run():
        from sqlalchemy import text

        engine = _get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                SELECT
                    (SELECT COUNT(*) FROM users WHERE is_active = true) AS active_users,
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT COUNT(*) FROM api_keys WHERE is_active = true) AS active_keys,
                    (SELECT SUM(usage_count) FROM api_keys) AS total_requests,
                    (SELECT COUNT(*) FROM api_keys WHERE last_used_at >= NOW() - INTERVAL '24 hours') AS keys_active_24h,
                    (SELECT COUNT(*) FROM intel_items WHERE status = 'processed') AS total_items,
                    (SELECT COUNT(*) FROM intel_items WHERE created_at >= NOW() - INTERVAL '24 hours') AS items_24h,
                    (SELECT COUNT(*) FROM sources WHERE is_active = true) AS active_sources
            """
                )
            )
            return dict(result.mappings().first())
        await engine.dispose()

    data = asyncio.run(_run())

    if json_output:
        print_json(data)
        return

    console.print("\n[bold]System Stats[/bold]\n")
    console.print(
        f"  Users: {data['active_users']} active / {data['total_users']} total"
    )
    console.print(
        f"  API Keys: {data['active_keys']} active, {data['keys_active_24h']} used in last 24h"
    )
    console.print(f"  Total Requests: {data['total_requests'] or 0}")
    console.print(
        f"  Items: {data['total_items']} processed, {data['items_24h']} in last 24h"
    )
    console.print(f"  Sources: {data['active_sources']} active")
    console.print()
