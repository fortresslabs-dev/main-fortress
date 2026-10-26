"""Purge non-action "noise" rows from enforcement_actions.

The enforcement landing-page scraper (legacy path) chunked index pages by
heading, turning site navigation ("Upcoming Events", "Featured Publications",
"View All …") into enforcement_actions rows — all pointing at the same landing
URL, with no institution, no penalty, and no citation. This removes those rows.

Criteria (all must hold — the same hard-signal test as
`enforcement_actions._is_enforcement_noise`): institution_type is unknown/null
AND penalty_amount_usd IS NULL AND regulations_cited is empty/null. A real
enforcement action almost always carries at least one of those signals, so this
is conservative; anything legitimately dropped is re-ingested by the fixed
index→detail crawl on the next run.

Safe by default (dry-run). Pass --apply to delete.

Usage:
    python -m scripts.purge_enforcement_noise                 # dry-run: count + sample
    python -m scripts.purge_enforcement_noise --apply         # delete
    python -m scripts.purge_enforcement_noise --regulator OCC # scope to one regulator
"""
from __future__ import annotations

import argparse
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("purge_enforcement_noise")

_WHERE = """
    (institution_type IS NULL OR institution_type = 'unknown')
    AND penalty_amount_usd IS NULL
    AND (regulations_cited IS NULL OR regulations_cited = '[]'::jsonb)
"""


async def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Purge enforcement_actions noise rows")
    ap.add_argument("--apply", action="store_true", help="delete the rows (default: dry-run)")
    ap.add_argument("--regulator", help="restrict to one regulator (e.g. OCC)")
    args = ap.parse_args(argv)

    from sqlalchemy import text as sql
    from src.services.database import init_db, close_db, get_db

    where = _WHERE
    params: dict = {}
    if args.regulator:
        where += " AND regulator = :reg"
        params["reg"] = args.regulator

    await init_db()
    try:
        async with get_db() as s:
            total = (await s.execute(sql(
                f"SELECT count(*) FROM enforcement_actions WHERE {where}"), params)).scalar_one()
            logger.info("noise_rows_matched count=%d regulator=%s", total, args.regulator or "ALL")

            if total:
                sample = (await s.execute(sql(
                    f"""SELECT regulator, source_url, left(coalesce(violation_summary,''), 70) AS s
                        FROM enforcement_actions WHERE {where}
                        ORDER BY regulator LIMIT 8"""), params)).all()
                for r in sample:
                    logger.info("  sample: %-6s %s | %s", r.regulator, r.source_url, r.s)

            if not args.apply:
                logger.info("dry-run — nothing deleted. Re-run with --apply to delete %d rows.", total)
                return 0

            deleted = (await s.execute(sql(
                f"DELETE FROM enforcement_actions WHERE {where}"), params)).rowcount
            await s.commit()
            logger.info("deleted %d noise rows", deleted)
    finally:
        await close_db()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(_main()))
