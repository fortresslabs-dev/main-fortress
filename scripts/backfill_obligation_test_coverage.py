"""Backfill obligations.has_test_coverage from completed workflow_executions.

The `testing` dimension on the CCO/CRO/CIO readiness gauge counts
obligations with `has_test_coverage = TRUE`. The runtime writeback lands
in `ExecutionAgent._mark_obligations_covered` on every successful run,
but any workflow that completed *before* the writeback wiring shipped
never flipped the flag — so the dashboard shows 0% even for banks that
have real agent-generated PRs in JIRA.

This script closes that historical gap by walking
`workflow_executions WHERE status='completed'` and, for every obligation
whose `regulation_id` matches the execution's `rule_id`, flipping
`has_test_coverage = TRUE` and tagging `test_scenario_ids` with a
synthetic `{jira_key or workflow_id}::backfill` marker so the audit
trail records that the credit came from historical inference rather
than a fresh run.

Default is DRY_RUN — set DRY_RUN=0 to actually write back.

Usage:
    DRY_RUN=1 python -m scripts.backfill_obligation_test_coverage   # preview
    DRY_RUN=0 python -m scripts.backfill_obligation_test_coverage   # apply

Outputs:
- summary of completed workflow executions with a matching rule_id
- per-execution log line with rule_id, obligations touched
- final rollup: workflows scanned, obligations flipped, unmatched
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from typing import Any, Dict, List

import structlog
from sqlalchemy import text as sqla_text

from src.services.database import get_db, init_db

logger = structlog.get_logger(__name__)


async def _completed_workflows(session) -> List[Dict[str, Any]]:
    """Completed executions with a rule_id we can join on."""
    res = await session.execute(
        sqla_text(
            """
            SELECT id::text     AS workflow_id,
                   jira_key,
                   rule_id,
                   completed_at
              FROM workflow_executions
             WHERE status  = 'completed'
               AND rule_id IS NOT NULL
             ORDER BY completed_at ASC NULLS LAST
            """
        )
    )
    return [dict(r._mapping) for r in res]


async def _uncovered_obligations_for_rule(session, rule_id: str) -> List[str]:
    res = await session.execute(
        sqla_text(
            """
            SELECT obligation_id
              FROM obligations
             WHERE is_active         = TRUE
               AND has_test_coverage = FALSE
               AND regulation_id     = :rule_id
            """
        ),
        {"rule_id": rule_id},
    )
    return [r[0] for r in res.all()]


async def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "1") != "0"

    await init_db()

    async with get_db() as session:
        workflows = await _completed_workflows(session)
        logger.info(
            "backfill_start",
            completed_workflows=len(workflows),
            dry_run=dry_run,
        )

        touched_obligations: set[str] = set()
        by_rule: Counter[str] = Counter()
        workflows_with_hits = 0

        for wf in workflows:
            rule_id = wf["rule_id"]
            obligation_ids = await _uncovered_obligations_for_rule(session, rule_id)
            if not obligation_ids:
                continue

            workflows_with_hits += 1
            by_rule[rule_id] += len(obligation_ids)
            new_obligations = [
                oid for oid in obligation_ids if oid not in touched_obligations
            ]
            touched_obligations.update(obligation_ids)

            marker = (
                f"{wf['jira_key'] or wf['workflow_id']}::backfill"
            )
            logger.info(
                "backfill_workflow",
                workflow_id=wf["workflow_id"],
                jira_key=wf["jira_key"],
                rule_id=rule_id,
                obligations=len(obligation_ids),
                new_obligations=len(new_obligations),
                marker=marker,
                dry_run=dry_run,
            )

            if not dry_run and obligation_ids:
                await session.execute(
                    sqla_text(
                        """
                        UPDATE obligations
                           SET has_test_coverage   = TRUE,
                               coverage_updated_at = NOW(),
                               test_scenario_ids   =
                                   COALESCE(test_scenario_ids, '[]'::jsonb)
                                   || CAST(:marker AS jsonb)
                         WHERE obligation_id = ANY(:ids)
                           AND is_active         = TRUE
                        """
                    ),
                    {"ids": obligation_ids, "marker": json.dumps([marker])},
                )

        if not dry_run:
            await session.commit()

        logger.info(
            "backfill_done",
            completed_workflows=len(workflows),
            workflows_with_hits=workflows_with_hits,
            obligations_flipped=len(touched_obligations),
            top_rules=dict(by_rule.most_common(10)),
            dry_run=dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
