"""Backfill compliance_rules.cfr_citation from rule_text where NULL.

The `inventory` dimension on the CCO dashboard counts rules that have
cfr_citation set. Rules scraped by connectors that didn't emit the field
(or that ran before the field existed) sit with NULL forever — there is
no UPDATE path elsewhere in the codebase.

This script scans rule_text using the SAME regex the obligation extractor
already uses (`obligation_extractor._CFR_RE`) and writes the first hit
into cfr_citation. It only touches rows where cfr_citation IS NULL —
existing values are not overwritten.

Default is DRY_RUN — set DRY_RUN=0 to actually write back.

Usage:
    DRY_RUN=1 python -m scripts.backfill_cfr_citations   # preview
    DRY_RUN=0 python -m scripts.backfill_cfr_citations   # apply

Outputs:
- per-row JSON log line with rule_id, regulator, extracted citation
- summary: scanned, matched, updated, by-regulator breakdown
"""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import text as sqla_text

from src.services.database import get_db, init_db

logger = structlog.get_logger(__name__)

# Deliberately broader than obligation_extractor._CFR_RE — accepts
# "C.F.R.", "CFR", "C.F.R", with or without the § symbol. The extractor's
# regex is kept strict for precision; this backfill is one-shot and
# benefits from higher recall against legacy connector output.
#
# Two shapes are covered per pattern:
#   1. Section shape: 31 CFR § 1010.311(b)(2)  |  31 CFR 1010.311
#   2. Part shape:    12 CFR Part 345  (used heavily by CRA/FDIC rules)
_CFR_RE = re.compile(
    r"(\d+\s+C\.?F\.?R\.?\s*(?:§\s*)?"
    r"(?:Part\s+\d+|[\d]+(?:\.[\d]+)*(?:\([a-z0-9]+\))*)"
    r")",
    re.IGNORECASE,
)

# USC citations (12 U.S.C. § 5311, 31 U.S.C. 5318(g)(1), 15 USC 1691).
# Enforcement actions frequently cite USC (BSA is codified at 12 USC 5311),
# and R3+R5's back-propagation join normalises `[.\s§]` — so a USC value in
# cfr_citation is joinable against a USC citation on enforcement_actions
# without any join-side change.
_USC_RE = re.compile(
    r"(\d+\s+U\.?S\.?C\.?\s*(?:§\s*)?"
    r"[\d]+(?:\([a-z0-9]+\))*"
    r")",
    re.IGNORECASE,
)


def _extract_citation(rule_text: str) -> Optional[str]:
    """Return the first CFR hit, else the first USC hit, else None."""
    if not rule_text:
        return None
    m = _CFR_RE.search(rule_text)
    if m:
        return m.group(1).strip()
    m = _USC_RE.search(rule_text)
    if m:
        return m.group(1).strip()
    return None


async def _missing_rows(session) -> List[Dict[str, Any]]:
    res = await session.execute(
        sqla_text(
            """
            SELECT rule_id, regulator, rule_text
              FROM compliance_rules
             WHERE is_active = TRUE
               AND cfr_citation IS NULL
               AND rule_text IS NOT NULL
               AND rule_text <> ''
            """
        )
    )
    return [dict(r._mapping) for r in res]


async def _regulator_gap(session) -> List[Dict[str, Any]]:
    """Diagnostic: which connectors are dropping cfr_citation on the floor."""
    res = await session.execute(
        sqla_text(
            """
            SELECT regulator,
                   COUNT(*)                                    AS total,
                   COUNT(*) FILTER (WHERE cfr_citation IS NULL) AS missing
              FROM compliance_rules
             WHERE is_active = TRUE
             GROUP BY regulator
             ORDER BY missing DESC
            """
        )
    )
    return [dict(r._mapping) for r in res]


async def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "1") != "0"

    # Standalone script — async_session_maker is only built by init_db(),
    # which the API server calls on startup. CLI runs must do it themselves.
    await init_db()

    async with get_db() as session:
        # Pre-flight diagnostic — surfaces which connectors are the source
        # of the gap so Track B (connector audit) has a target list.
        gap = await _regulator_gap(session)
        for row in gap:
            logger.info(
                "regulator_cfr_gap",
                regulator=row["regulator"],
                total=row["total"],
                missing=row["missing"],
            )

        rows = await _missing_rows(session)
        logger.info("backfill_start", scanned=len(rows), dry_run=dry_run)

        matched = 0
        by_regulator: Counter[str] = Counter()
        by_shape: Counter[str] = Counter()  # cfr | usc — recall diagnostic
        for row in rows:
            citation = _extract_citation(row["rule_text"] or "")
            if not citation:
                continue
            shape = "usc" if "U.S.C" in citation.upper() or "USC" in citation.upper() else "cfr"
            matched += 1
            by_regulator[row["regulator"] or "UNKNOWN"] += 1
            by_shape[shape] += 1
            logger.info(
                "backfill_row",
                rule_id=row["rule_id"],
                regulator=row["regulator"],
                cfr_citation=citation,
                shape=shape,
                dry_run=dry_run,
            )
            if not dry_run:
                await session.execute(
                    sqla_text(
                        """
                        UPDATE compliance_rules
                           SET cfr_citation = :citation
                         WHERE rule_id = :rule_id
                           AND cfr_citation IS NULL
                        """
                    ),
                    {"citation": citation, "rule_id": row["rule_id"]},
                )

        if not dry_run:
            await session.commit()

        logger.info(
            "backfill_done",
            scanned=len(rows),
            matched=matched,
            updated=(matched if not dry_run else 0),
            unmatched=len(rows) - matched,
            by_regulator=dict(by_regulator),
            by_shape=dict(by_shape),
            dry_run=dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
