"""Re-extract penalty_amount_usd on suspect rows in enforcement_actions.

Suspect = penalty_amount_usd > $1B (the largest real banking penalty on
record is BNP Paribas at $8.97B in 2014; legitimate values above $1B are
single digits per year, so anything above the threshold in our table is
almost certainly the asset-confusion bug fixed in this branch).

Default is DRY_RUN — set DRY_RUN=0 (or unset) to actually write back.

Usage:
    DRY_RUN=1 python -m scripts.backfill_enforcement_penalties   # preview
    DRY_RUN=0 python -m scripts.backfill_enforcement_penalties   # apply

ECS RunTask form is identical to backfill_ofac_sdn_vectors.py.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Any, Dict, List

import structlog
from sqlalchemy import text as sqla_text

from src.services.database import get_db

logger = structlog.get_logger(__name__)

SUSPECT_THRESHOLD = Decimal("1000000000")  # $1B


async def _suspect_rows(session) -> List[Dict[str, Any]]:
    res = await session.execute(
        sqla_text(
            """
            SELECT id, action_ref, regulator, penalty_amount_usd, violation_summary
              FROM enforcement_actions
             WHERE is_active = TRUE
               AND penalty_amount_usd IS NOT NULL
               AND penalty_amount_usd > :threshold
             ORDER BY penalty_amount_usd DESC
            """
        ),
        {"threshold": SUSPECT_THRESHOLD},
    )
    return [dict(r._mapping) for r in res]


async def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "1") != "0"
    # The eval/extractor path must hit live Bedrock — mock mode would
    # blank every row to None which is worse than the current bug.
    os.environ["LLM_MOCK_MODE"] = "false"

    from src.extraction.penalty import extract_penalty

    async with get_db() as session:
        rows = await _suspect_rows(session)
        logger.info("backfill_start", suspect_rows=len(rows), dry_run=dry_run)

        updated = 0
        for row in rows:
            text = row.get("violation_summary") or ""
            if not text:
                logger.warning(
                    "skip_no_summary",
                    action_ref=row["action_ref"],
                )
                continue
            try:
                result = await extract_penalty(text)
            except Exception as exc:
                logger.warning(
                    "extract_failed_skip",
                    action_ref=row["action_ref"],
                    error=str(exc),
                )
                continue

            old_amount = row["penalty_amount_usd"]
            new_amount = (
                None
                if (result.amount_usd is None or result.confidence < 0.5)
                else result.amount_usd
            )
            logger.info(
                "backfill_row",
                action_ref=row["action_ref"],
                regulator=row["regulator"],
                old_amount_usd=str(old_amount),
                new_amount_usd=str(new_amount) if new_amount is not None else None,
                confidence=result.confidence,
                is_penalty_not_asset=result.is_penalty_not_asset,
                source_span=result.source_span,
                dry_run=dry_run,
            )
            if not dry_run:
                await session.execute(
                    sqla_text(
                        """
                        UPDATE enforcement_actions
                           SET penalty_amount_usd = :new_amount
                         WHERE id = :id
                        """
                    ),
                    {"new_amount": new_amount, "id": row["id"]},
                )
                updated += 1

        if not dry_run:
            await session.commit()
        logger.info(
            "backfill_done",
            updated=updated,
            scanned=len(rows),
            dry_run=dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
