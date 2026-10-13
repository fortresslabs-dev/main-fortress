"""One-shot: rewrite legacy JSON-dump OFAC change_summary rows as human text.

Pre-fix, `_log_sdn_change` wrote `json.dumps({name_variant, ...})` into
`regulatory_change_records.change_summary`, leaving the CCO dashboard's
change inbox showing raw JSON envelopes as row titles. This script rewrites
those rows in place: `change_summary` becomes a single human sentence and
`semantic_diff` carries the ` · `-separated detail line — matching what
`format_sdn_change_text()` now emits for new events.

Idempotent. Selects only rows whose `change_summary` begins with `{` and
parses as JSON. Rows already in the new format are skipped.

Safe on both P1 and P2 Aurora — the table schema is shared. P1 is the
source of truth; running on P2 only fixes already-synced rows. After the
P1 backfill runs and the next `/sync/sdn-changes` cycle completes, P2
will catch up via the regular sync path — running this on P2 too is just
a speed-up so users see the fix without waiting for the next cycle.

Usage:
    PIPELINE_MODE=pipeline1 python -m scripts.setup.backfill_ofac_change_summaries
    PIPELINE_MODE=pipeline2 python -m scripts.setup.backfill_ofac_change_summaries
    add --dry-run to report counts without writing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog
from sqlalchemy import text

from src.services.database import close_db, get_db, init_db
from src.services.ofac_sdn_loader import format_sdn_change_text

logger = structlog.get_logger(__name__)


_BATCH = 500


async def _count_legacy(session) -> int:
    return await session.scalar(text("""
        SELECT COUNT(*) FROM regulatory_change_records
        WHERE regulator = 'OFAC'
          AND change_summary LIKE '{%'
    """)) or 0


async def _iter_legacy(session, batch_size: int = _BATCH):
    """Yield legacy OFAC rows. Uses change_ref as a stable keyset cursor."""
    last_ref = ""
    while True:
        rows = (await session.execute(text("""
            SELECT id, change_ref, change_type, change_summary
            FROM regulatory_change_records
            WHERE regulator = 'OFAC'
              AND change_summary LIKE '{%'
              AND change_ref > :last_ref
            ORDER BY change_ref ASC
            LIMIT :lim
        """), {"last_ref": last_ref, "lim": batch_size})).mappings().all()
        if not rows:
            return
        for r in rows:
            yield dict(r)
        last_ref = rows[-1]["change_ref"]


async def backfill(dry_run: bool) -> dict[str, int]:
    await init_db()
    try:
        async with get_db() as session:
            legacy = await _count_legacy(session)
            logger.info("backfill_start", legacy_rows=legacy, dry_run=dry_run)

            if dry_run or legacy == 0:
                return {"legacy_rows": legacy, "rewritten": 0, "skipped_unparseable": 0}

            rewritten = 0
            skipped = 0
            async for row in _iter_legacy(session):
                try:
                    variant = json.loads(row["change_summary"])
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                if not isinstance(variant, dict):
                    skipped += 1
                    continue

                summary, diff = format_sdn_change_text(row["change_type"], variant)
                await session.execute(text("""
                    UPDATE regulatory_change_records
                    SET change_summary = :summary,
                        semantic_diff  = :diff
                    WHERE id = :id
                """), {"summary": summary, "diff": diff, "id": row["id"]})
                rewritten += 1
                if rewritten % 1000 == 0:
                    await session.commit()
                    logger.info("backfill_progress", rewritten=rewritten, legacy=legacy)

            await session.commit()
            remaining = await _count_legacy(session)
            stats = {
                "legacy_rows_before":   legacy,
                "rewritten":            rewritten,
                "skipped_unparseable":  skipped,
                "legacy_rows_after":    remaining,
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
