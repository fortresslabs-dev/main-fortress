"""Backfill enforcement_actions.regulations_cited via the LLM extractor
(option a) so the enforcement-risk scorer has regulation attribution to work
with — which lights up readiness `ra_currency` and the /dashboard/risk view.

Only touches rows whose regulations_cited is empty AND whose regex extraction
also found nothing (enforcement press releases rarely cite CFRs verbatim). The
LLM is constrained to the distinct compliance_rules.cfr_citation vocabulary, so
output always maps back to a rule and nothing is invented; actions that don't
clearly concern any corpus regulation stay [] (honest, not fabricated).

Runs on the app DB layer (DATABASE_URL / settings) + Bedrock. Intended to run
in P1 where the full enforcement source text lives; locally it mostly yields []
because the sample enforcement data is CFPB-consumer, not BSA/AML.

Usage:
    python -m scripts.backfill_enforcement_citations --dry-run
    python -m scripts.backfill_enforcement_citations --limit 200
    python -m scripts.backfill_enforcement_citations --limit 200 --run-scorer
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("backfill_enforcement_citations")


async def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill enforcement regulations_cited (LLM)")
    ap.add_argument("--limit", type=int, default=100, help="max actions to process")
    ap.add_argument("--dry-run", action="store_true", help="extract + report; write nothing")
    ap.add_argument("--run-scorer", action="store_true",
                    help="run EnforcementRiskScorer after the backfill")
    args = ap.parse_args(argv)

    from sqlalchemy import text as sql
    from src.services.database import init_db, close_db, get_db
    from src.services.enforcement_actions import extract_regulations_cited_llm

    await init_db()
    try:
        async with get_db() as s:
            cands = [r[0] for r in (await s.execute(sql(
                "SELECT DISTINCT cfr_citation FROM compliance_rules "
                "WHERE cfr_citation IS NOT NULL AND cfr_citation <> ''"))).all()]
            rows = (await s.execute(sql("""
                SELECT id, regulator, action_type,
                       COALESCE(violation_summary, '') AS vs
                FROM enforcement_actions
                WHERE is_active
                  AND (regulations_cited IS NULL
                       OR jsonb_array_length(regulations_cited) = 0)
                  AND violation_summary IS NOT NULL
                ORDER BY action_date DESC NULLS LAST
                LIMIT :lim
            """), {"lim": args.limit})).all()

        logger.info("candidates=%d actions_to_process=%d", len(cands), len(rows))
        matched = updated = 0
        for rid, reg, atype, vs in rows:
            cites = await extract_regulations_cited_llm(
                vs, cands, regulator=reg, action_type=atype)
            if not cites:
                continue
            matched += 1
            logger.info("action=%s -> %s", rid, cites)
            if not args.dry_run:
                async with get_db() as s:  # commits on clean exit
                    await s.execute(sql(
                        "UPDATE enforcement_actions SET regulations_cited = CAST(:c AS jsonb) "
                        "WHERE id = :id"), {"c": json.dumps(cites), "id": str(rid)})
                updated += 1

        logger.info("backfill_complete processed=%d matched=%d updated=%d dry_run=%s",
                    len(rows), matched, updated, args.dry_run)

        if args.run_scorer and not args.dry_run:
            from src.services.enforcement_risk_scorer import EnforcementRiskScorer
            async with get_db() as s:
                n = await EnforcementRiskScorer().run(s)
            logger.info("scorer rules_updated=%d", n)
    finally:
        await close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
