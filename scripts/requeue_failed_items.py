"""Requeue failed items and investigate erroring sources.

Usage:
    python scripts/requeue_failed_items.py --dry-run          # Show what would be requeued
    python scripts/requeue_failed_items.py --limit 50         # Requeue up to 50 items
    python scripts/requeue_failed_items.py --deactivate-stuck  # Deactivate sources with 5+ errors
    python scripts/requeue_failed_items.py --reset-errors SRC1 SRC2  # Reset error counts

P1-8: 572 failed items have a path to recovery (requeue to embedded or raw).
P1-9: 15 sources at max consecutive errors can be reviewed and deactivated.

Idempotent: safe to re-run. Dry-run is the default if no action flags are given.
"""

import argparse
import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.core.config import get_settings
from src.models.models import IntelItem, Source
from sqlalchemy import select, update, func


async def show_failed_items(session, limit: int | None = None):
    """Query and display failed items."""
    count_result = await session.execute(
        select(func.count()).select_from(IntelItem).where(IntelItem.status == "failed")
    )
    total_failed = count_result.scalar()

    query = (
        select(IntelItem.id, IntelItem.url, IntelItem.status, IntelItem.embedding)
        .where(IntelItem.status == "failed")
        .order_by(IntelItem.created_at.desc())
    )
    if limit:
        query = query.limit(limit)

    result = await session.execute(query)
    rows = result.all()

    has_embedding = sum(1 for r in rows if r.embedding is not None)
    no_embedding = len(rows) - has_embedding

    print(f"\n--- Failed Items Report ---")
    print(f"Total failed items in DB: {total_failed}")
    print(f"Items inspected (limit={limit or 'all'}): {len(rows)}")
    print(f"  With embeddings (will requeue to 'embedded'): {has_embedding}")
    print(f"  Without embeddings (will requeue to 'raw'): {no_embedding}")

    return rows


async def requeue_items(session, rows, dry_run: bool):
    """Reset failed items to embedded or raw depending on embedding state."""
    requeued_embedded = 0
    requeued_raw = 0

    for row in rows:
        target_status = "embedded" if row.embedding is not None else "raw"
        if dry_run:
            print(f"  [dry-run] {row.id} -> {target_status}")
        else:
            await session.execute(
                update(IntelItem)
                .where(IntelItem.id == row.id)
                .values(status=target_status)
            )
            if target_status == "embedded":
                requeued_embedded += 1
            else:
                requeued_raw += 1

    if not dry_run:
        await session.commit()
        print(f"\nRequeued {requeued_embedded} items to 'embedded'")
        print(f"Requeued {requeued_raw} items to 'raw'")
        print(f"Total requeued: {requeued_embedded + requeued_raw}")
    else:
        print(f"\n[dry-run] Would requeue {len(rows)} items")


async def show_erroring_sources(session):
    """Query and display sources with high consecutive errors."""
    result = await session.execute(
        select(
            Source.id,
            Source.name,
            Source.type,
            Source.consecutive_errors,
            Source.is_active,
            Source.last_successful_poll,
            Source.url,
        )
        .where(Source.consecutive_errors >= 5, Source.is_active == True)  # noqa: E712
        .order_by(Source.consecutive_errors.desc())
    )
    rows = result.all()

    print(f"\n--- Erroring Sources (consecutive_errors >= 5, active) ---")
    print(f"Count: {len(rows)}")
    for row in rows:
        print(
            f"  [{row.consecutive_errors} errors] {row.id} ({row.type})"
            f" — {row.name}"
        )
        print(f"    URL: {row.url}")
        if row.last_successful_poll:
            print(f"    Last success: {row.last_successful_poll}")
        else:
            print(f"    Last success: never")

    return rows


async def deactivate_stuck_sources(session, dry_run: bool):
    """Deactivate sources with 5+ consecutive errors."""
    result = await session.execute(
        select(Source).where(
            Source.consecutive_errors >= 5, Source.is_active == True
        )  # noqa: E712
    )
    sources = result.scalars().all()

    if not sources:
        print("\nNo stuck sources to deactivate.")
        return

    for source in sources:
        if dry_run:
            print(
                f"  [dry-run] Would deactivate: {source.id}"
                f" ({source.consecutive_errors} errors)"
            )
        else:
            source.is_active = False
            print(
                f"  [deactivated] {source.id}" f" ({source.consecutive_errors} errors)"
            )

    if not dry_run:
        await session.commit()
        print(f"\nDeactivated {len(sources)} stuck sources")
    else:
        print(f"\n[dry-run] Would deactivate {len(sources)} sources")


async def reset_source_errors(session, source_ids: list[str], dry_run: bool):
    """Reset consecutive_errors to 0 for specified source IDs."""
    result = await session.execute(select(Source).where(Source.id.in_(source_ids)))
    sources = result.scalars().all()

    found_ids = {s.id for s in sources}
    missing = set(source_ids) - found_ids
    if missing:
        print(f"\nWarning: source IDs not found: {missing}")

    for source in sources:
        if dry_run:
            print(
                f"  [dry-run] Would reset errors: {source.id}"
                f" (currently {source.consecutive_errors})"
            )
        else:
            source.consecutive_errors = 0
            print(f"  [reset] {source.id}: consecutive_errors -> 0")

    if not dry_run:
        await session.commit()
        print(f"\nReset errors for {len(sources)} sources")
    else:
        print(f"\n[dry-run] Would reset errors for {len(sources)} sources")


async def main():
    parser = argparse.ArgumentParser(
        description="Requeue failed items and manage erroring sources"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be changed without making changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N failed items",
    )
    parser.add_argument(
        "--deactivate-stuck",
        action="store_true",
        default=False,
        help="Deactivate sources with 5+ consecutive errors",
    )
    parser.add_argument(
        "--reset-errors",
        nargs="+",
        metavar="SOURCE_ID",
        default=None,
        help="Reset consecutive_errors=0 for specified source IDs",
    )
    args = parser.parse_args()

    # If no action flags given, default to dry-run report
    is_report_only = not args.deactivate_stuck and args.reset_errors is None
    dry_run = args.dry_run or is_report_only

    get_settings()
    await init_db()

    try:
        async with _db.async_session_factory() as session:
            # --- Failed items ---
            rows = await show_failed_items(session, limit=args.limit)
            if rows:
                await requeue_items(session, rows, dry_run=dry_run)

            # --- Erroring sources ---
            await show_erroring_sources(session)

            if args.deactivate_stuck:
                await deactivate_stuck_sources(session, dry_run=args.dry_run)

            if args.reset_errors:
                await reset_source_errors(
                    session, args.reset_errors, dry_run=args.dry_run
                )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
