"""One-shot: emit synthetic 'added' change records for currently-active SDN variants.

Run on P1 ONCE after alembic upgrade to 029. Purpose: bootstrap
regulatory_change_records so a fresh P2 calling /sync/sdn-changes with
`since=epoch` sees the full active SDN baseline as 'added' events — not just
the new lifecycle events emitted by the loader from this point forward.

Idempotent: _log_sdn_change writes with ON CONFLICT (change_ref) DO NOTHING,
so re-runs are safe. The change_ref hash for 'added' uses prefix 'ADD' which
will not collide with any pre-existing 'OFAC-DEACT-*' refs.

The backfilled rows carry detected_at = now() — they describe "as of <today>,
these variants are active", not the historical date each variant first
appeared. That's the right semantic for P2 onboarding.

Usage:
    PIPELINE_MODE=pipeline1 python -m scripts.setup.backfill_sdn_change_records
    PIPELINE_MODE=pipeline1 python -m scripts.setup.backfill_sdn_change_records --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from sqlalchemy import text

from src.services.database import close_db, get_db, init_db
from src.services.ofac_sdn_loader import _log_sdn_change

logger = structlog.get_logger(__name__)


async def _count_active_variants(session) -> int:
    return await session.scalar(text(
        "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active = TRUE"
    )) or 0


async def _count_existing_added_refs(session) -> int:
    return await session.scalar(text(
        "SELECT COUNT(*) FROM regulatory_change_records "
        "WHERE regulator = 'OFAC' AND change_type = 'added' "
        "AND change_ref LIKE 'OFAC-ADD-%'"
    )) or 0


async def _iter_active_variants(session, batch_size: int = 1000):
    """Yield active variant dicts in (sdn_number, name_normalized) order."""
    offset = 0
    while True:
        rows = (await session.execute(text("""
            SELECT sdn_number, name_variant, name_normalized, is_primary,
                   entity_type, programs, country
            FROM ofac_sdn_vectors
            WHERE is_active = TRUE
            ORDER BY sdn_number, name_normalized
            LIMIT :lim OFFSET :off
        """), {"lim": batch_size, "off": offset})).mappings().all()
        if not rows:
            return
        for r in rows:
            yield dict(r)
        offset += batch_size


async def backfill(dry_run: bool) -> dict[str, int]:
    await init_db()
    try:
        async with get_db() as session:
            total = await _count_active_variants(session)
            existing = await _count_existing_added_refs(session)
            logger.info(
                "backfill_start",
                active_variants=total,
                existing_add_refs=existing,
                dry_run=dry_run,
            )

            if dry_run:
                # Project work without writing: per-variant ON CONFLICT will
                # skip the refs that already exist, so the upper bound is
                # max(0, total - existing). The lower bound is 0 (if every
                # variant already has an OFAC-ADD-* row from a prior loader
                # run). Report both.
                return {
                    "active_variants": total,
                    "existing_add_refs": existing,
                    "projected_insert_upper_bound": max(0, total - existing),
                    "written": 0,
                }

            written = 0
            async for variant in _iter_active_variants(session):
                await _log_sdn_change(session, "added", variant)
                written += 1
                # Commit periodically — a single 30k-row transaction is fine
                # but smaller batches are friendlier to monitoring.
                if written % 1000 == 0:
                    await session.commit()
                    logger.info("backfill_progress", written=written, total=total)

            await session.commit()

            after = await _count_existing_added_refs(session)
            stats = {
                "active_variants": total,
                "existing_add_refs_before": existing,
                "existing_add_refs_after": after,
                "log_calls": written,
                "net_inserted": after - existing,
            }
            logger.info("backfill_complete", **stats)
            return stats
    finally:
        await close_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be written; make no changes.")
    args = parser.parse_args()

    stats = asyncio.run(backfill(args.dry_run))
    print("\nResult:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
