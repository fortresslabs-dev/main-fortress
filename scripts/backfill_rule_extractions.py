"""Backfill compliance_rules + obligations via the regex-first → LLM-fallback
extraction pipeline. Single source of truth: this delegates to
`src.services.obligation_extractor.extract_for_rule`, which is the same
entry point the live `ObligationExtractor.run()` calls during scrapes.

Per-rule decision tree (see `extract_for_rule`):
  1. Regex pass. If it returns BOTH a primary CFR AND at least one
     obligation — no Bedrock call. Regex hits are preserved verbatim.
  2. Else, if `LLM_EXTRACTION_MODE in (fallback, shadow)`, the LLM
     extractor (`src.extraction.rule.extract_rule`) is invoked to fill
     the empty slots. The LLM never overwrites a regex hit.

Default is DRY_RUN — set DRY_RUN=0 to write back.

Usage:
    DRY_RUN=1 python -m scripts.backfill_rule_extractions   # preview
    DRY_RUN=0 python -m scripts.backfill_rule_extractions   # apply

    # Single regulator (typical for staged rollout):
    DRY_RUN=0 REGULATOR=FFIEC python -m scripts.backfill_rule_extractions

    # Smoke-test batch:
    DRY_RUN=0 LIMIT=10 python -m scripts.backfill_rule_extractions

    # Override extraction mode for this run (overrides task-def env):
    LLM_EXTRACTION_MODE=fallback DRY_RUN=0 python -m scripts.backfill_rule_extractions

Outputs:
  - per-row JSON: rule_id, regulator, used_llm, primary_cfr, obligations
  - summary: scanned, written, regex_sufficient, llm_invoked, errors,
             by-regulator breakdown
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import text as sqla_text

from src.extraction.bedrock_extractor import _emit_extraction_metric
from src.services.database import get_db, init_db
from src.services.obligation_extractor import (
    ExtractedObligation,
    extract_for_rule,
)

logger = structlog.get_logger(__name__)


async def _candidate_rules(
    session,
    regulator: Optional[str],
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Pick rules where regex output is known-or-likely insufficient.

    A rule is a candidate when:
      - cfr_citation IS NULL, OR
      - obligation_count IS NULL OR = 0

    Rules already cited AND with obligations are skipped — the regex
    pass would re-derive identical output, and the backfill should be
    cheap to re-run (idempotent).
    """
    params: Dict[str, Any] = {}
    where_extra = ""
    if regulator:
        where_extra = " AND regulator = :regulator"
        params["regulator"] = regulator
    limit_sql = ""
    if limit:
        limit_sql = " LIMIT :limit"
        params["limit"] = limit

    sql = f"""
        SELECT rule_id, regulator, cfr_citation, obligation_count,
               category, rule_text
          FROM compliance_rules
         WHERE is_active = TRUE
           AND rule_text IS NOT NULL
           AND rule_text <> ''
           AND (cfr_citation IS NULL OR COALESCE(obligation_count, 0) = 0)
           {where_extra}
         ORDER BY regulator, rule_id
         {limit_sql}
    """
    res = await session.execute(sqla_text(sql), params)
    return [dict(r._mapping) for r in res]


async def _persist(
    session,
    rule: Dict[str, Any],
    obligations: List[ExtractedObligation],
    primary_cfr: Optional[str],
    used_llm: bool,
) -> Dict[str, bool]:
    """Write extraction back. UPSERT semantics; safe to re-run.

    Only writes cfr_citation when:
      - the column is currently NULL, AND
      - the cite came from the LLM fallback (regex hits in rule_text are
        discoverable; not overwriting a NULL with regex preserves the
        invariant that connector-emitted values are the highest authority).
    """
    updated = {
        "cfr_citation": False,
        "obligation_count": False,
        "obligations_replaced": False,
    }
    rule_id = rule["rule_id"]

    if used_llm and primary_cfr and rule["cfr_citation"] is None:
        await session.execute(
            sqla_text(
                "UPDATE compliance_rules "
                "   SET cfr_citation = :c "
                " WHERE rule_id = :rid AND cfr_citation IS NULL"
            ),
            {"c": primary_cfr, "rid": rule_id},
        )
        updated["cfr_citation"] = True

    await session.execute(
        sqla_text(
            "UPDATE compliance_rules "
            "   SET obligation_count = :n "
            " WHERE rule_id = :rid"
        ),
        {"n": len(obligations), "rid": rule_id},
    )
    updated["obligation_count"] = True

    if obligations:
        regulator = rule["regulator"] or "UNKNOWN"
        category = rule["category"] or "general"
        for idx, (sentence, obl_type, cite, sec_ref) in enumerate(obligations, 1):
            obligation_id = f"{rule_id}::OBL::{idx:03d}"
            await session.execute(
                sqla_text("""
                    INSERT INTO obligations (
                        id, obligation_id, regulation_id, regulator, category,
                        source_text, obligation_type, section_reference,
                        cfr_citation, has_test_coverage, extracted_at, is_active
                    ) VALUES (
                        gen_random_uuid(), :oid, :rid, :reg, :cat,
                        :txt, :otype, :sec, :cfr, FALSE, NOW(), TRUE
                    )
                    ON CONFLICT (obligation_id) DO UPDATE SET
                        source_text       = EXCLUDED.source_text,
                        obligation_type   = EXCLUDED.obligation_type,
                        section_reference = EXCLUDED.section_reference,
                        cfr_citation      = EXCLUDED.cfr_citation,
                        extracted_at      = NOW(),
                        is_active         = TRUE
                """),
                {
                    "oid": obligation_id,
                    "rid": rule_id,
                    "reg": regulator,
                    "cat": category,
                    "txt": sentence,
                    "otype": obl_type,
                    "sec": sec_ref,
                    "cfr": cite,
                },
            )
        updated["obligations_replaced"] = True

    return updated


async def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "1") != "0"
    regulator = os.environ.get("REGULATOR") or None
    limit_env = os.environ.get("LIMIT")
    limit = int(limit_env) if limit_env else None
    # Mode resolution is delegated to extract_for_rule via env. Echo the
    # resolved value so backfill logs make the mode auditable.
    from src.services.obligation_extractor import _extraction_mode
    mode = _extraction_mode()

    await init_db()

    async with get_db() as session:
        rules = await _candidate_rules(session, regulator, limit)
        logger.info(
            "backfill_start",
            scanned=len(rules),
            dry_run=dry_run,
            mode=mode,
            regulator=regulator,
            limit=limit,
        )

        stats: Counter[str] = Counter()
        by_regulator: Counter[str] = Counter()
        started = datetime.utcnow()

        for rule in rules:
            stats["scanned"] += 1
            reg_dim = {"Regulator": (rule["regulator"] or "UNKNOWN")[:64]}

            try:
                obligations, primary_cfr, used_llm = await extract_for_rule(
                    rule["rule_text"],
                    rule["regulator"],
                    rule["cfr_citation"],
                )
            except Exception as exc:
                # extract_for_rule swallows Bedrock failures internally
                # and falls back to regex. Any exception here is from the
                # regex layer or an unexpected programming error.
                logger.error(
                    "backfill_extract_failed",
                    rule_id=rule["rule_id"],
                    regulator=rule["regulator"],
                    error=str(exc),
                    error_class=type(exc).__name__,
                )
                _emit_extraction_metric(
                    "BackfillRuleErrors", "RuleExtraction",
                    dimensions={**reg_dim, "Phase": "extract"},
                )
                stats["errors"] += 1
                continue

            if used_llm:
                stats["llm_invoked"] += 1
            else:
                # Either regex was sufficient OR LLM was disabled/failed
                # (the metric for each was already emitted inside
                # extract_for_rule). Track at the backfill layer too.
                if primary_cfr is not None and obligations:
                    stats["regex_sufficient"] += 1
                else:
                    stats["regex_insufficient_no_llm"] += 1

            by_regulator[rule["regulator"] or "UNKNOWN"] += 1

            log_payload = {
                "rule_id": rule["rule_id"],
                "regulator": rule["regulator"],
                "used_llm": used_llm,
                "primary_cfr": primary_cfr,
                "obligations": len(obligations),
                "dry_run": dry_run,
            }

            if dry_run:
                logger.info("backfill_dry_run", **log_payload)
                continue

            updates = await _persist(
                session, rule, obligations, primary_cfr, used_llm,
            )
            stats["written"] += 1
            logger.info("backfill_written", updates=updates, **log_payload)

        if not dry_run:
            await session.commit()

        elapsed_s = round((datetime.utcnow() - started).total_seconds(), 1)
        logger.info(
            "backfill_done",
            mode=mode,
            scanned=stats["scanned"],
            written=stats["written"],
            regex_sufficient=stats["regex_sufficient"],
            llm_invoked=stats["llm_invoked"],
            regex_insufficient_no_llm=stats["regex_insufficient_no_llm"],
            errors=stats["errors"],
            by_regulator=dict(by_regulator),
            elapsed_s=elapsed_s,
            dry_run=dry_run,
        )
        # Summary metrics — one EMF record per counter so CW Insights
        # queries can chart trends across runs.
        for metric, value in [
            ("BackfillRulesScanned", stats["scanned"]),
            ("BackfillRulesWritten", stats["written"]),
            ("BackfillRegexSufficient", stats["regex_sufficient"]),
            ("BackfillLlmInvoked", stats["llm_invoked"]),
            ("BackfillErrors", stats["errors"]),
        ]:
            if value:
                _emit_extraction_metric(
                    metric, "RuleExtraction",
                    dimensions={"DryRun": str(dry_run)}, value=value,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
