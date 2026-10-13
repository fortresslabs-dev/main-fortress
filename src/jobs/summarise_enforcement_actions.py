"""One-shot: backfill enforcement_actions.summary_pretty via Bedrock.

Designed for the M040 backfill (design doc §11.1 Q16). Picks rows where
``summary_pretty IS NULL AND is_active = TRUE``, rewrites
``violation_summary`` into board-readable English via Claude Sonnet 4.x,
writes back. Idempotent: only NULL rows touched.

Cost gate (per Q16):
  * Hard ceiling of $50 per run (configurable via ENF_SUMMARY_USD_CAP).
  * Per-row cost estimated upfront from input/output token counts using
    Sonnet 4.6 list pricing. Run aborts before InvokeModel if the
    projected spend exceeds the ceiling.
  * Default batch size = 500 (configurable via ENF_SUMMARY_LIMIT).

Failure mode: if Bedrock returns AccessDeniedException or
ResourceNotFoundException (see feedback_bedrock_anthropic_use_case_form
+ feedback_bedrock_marketplace_iam), the row is left NULL and the
board-report renderer falls back to violation_summary verbatim. We log
the exception class so ops can fix the one-time pre-flight gate
(scripts/setup/verify_bedrock_ready.py).

Run:
  PIPELINE_MODE=pipeline1 python -m src.jobs.summarise_enforcement_actions
  PIPELINE_MODE=pipeline1 python -m src.jobs.summarise_enforcement_actions --dry-run
Env:
  AWS_REGION              (default us-east-1)
  BEDROCK_MODEL           (default us.anthropic.claude-sonnet-4-v1:0)
  ENF_SUMMARY_LIMIT       (default 500)
  ENF_SUMMARY_USD_CAP     (default 50.0)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import boto3
from sqlalchemy import text

from src.services.database import close_db, get_db, init_db


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Sonnet 4.6 list pricing in USD per 1M tokens. Override via env if AWS
# price drops or we move to a smaller model.
_PRICE_IN = float(os.getenv("BEDROCK_PRICE_IN_PER_M", "3.0"))
_PRICE_OUT = float(os.getenv("BEDROCK_PRICE_OUT_PER_M", "15.0"))

# Conservative per-row estimate: violation_summary ~ 1.8k input tokens
# (long regulator-language), plus ~250 tokens of prompt, ~400 output.
_EST_TOKENS_IN = 2050
_EST_TOKENS_OUT = 400

# Per memory feedback_bedrock_inference_profile_prefix: Sonnet 4.x is
# INFERENCE_PROFILE-only in us-east-1; bare IDs throw ValidationException.
_DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-v1:0"

_PROMPT_TEMPLATE = (
    "You rewrite regulatory enforcement-action language into one to two "
    "plain-English sentences suitable for a bank board of directors. "
    "Avoid jargon. Keep the institution name, action type, and penalty "
    "amount (if any). Do not add facts not in the source. Source:\n\n"
    "Regulator: {regulator}\n"
    "Action date: {action_date}\n"
    "Action type: {action_type}\n"
    "Penalty: {penalty}\n"
    "Source summary: {summary}\n\n"
    "Reply with ONLY the rewritten sentence(s), no preamble."
)


def _estimate_cost_per_row() -> float:
    return (_EST_TOKENS_IN * _PRICE_IN + _EST_TOKENS_OUT * _PRICE_OUT) / 1_000_000


def _invoke(client, model_id: str, prompt: str) -> str:
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = client.invoke_model(
        modelId=model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    # Bedrock Anthropic envelope: {"content": [{"type": "text", "text": "..."}], ...}
    for block in payload.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return ""


def _format_penalty(amount: Any) -> str:
    if amount is None:
        return "none disclosed"
    try:
        return f"${float(amount):,.0f}"
    except (TypeError, ValueError):
        return str(amount)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="select rows + estimate cost without invoking Bedrock")
    parser.add_argument("--limit", type=int,
                        default=int(os.getenv("ENF_SUMMARY_LIMIT", "500")))
    parser.add_argument("--usd-cap", type=float,
                        default=float(os.getenv("ENF_SUMMARY_USD_CAP", "50.0")))
    args = parser.parse_args()

    model_id = os.getenv("BEDROCK_MODEL") or _DEFAULT_MODEL
    region = os.getenv("AWS_REGION", "us-east-1")

    await init_db()

    cost_per_row = _estimate_cost_per_row()
    max_rows_by_cost = int(args.usd_cap / cost_per_row) if cost_per_row > 0 else args.limit
    effective_limit = min(args.limit, max_rows_by_cost)

    log.info(
        "enf_summary_plan model=%s region=%s requested_limit=%d "
        "cost_per_row_usd=%.4f usd_cap=%.2f effective_limit=%d",
        model_id, region, args.limit, cost_per_row, args.usd_cap, effective_limit,
    )
    if effective_limit < args.limit:
        log.warning(
            "enf_summary_cost_cap_active capped=%d/%d (saves $%.2f)",
            effective_limit, args.limit,
            (args.limit - effective_limit) * cost_per_row,
        )

    summarised = failed = 0
    async with get_db() as session:
        rows = (await session.execute(text("""
            SELECT id, regulator, action_date, action_type, violation_summary,
                   penalty_amount_usd
            FROM enforcement_actions
            WHERE is_active = TRUE
              AND summary_pretty IS NULL
              AND violation_summary IS NOT NULL
              AND char_length(violation_summary) > 0
            ORDER BY action_date DESC NULLS LAST
            LIMIT :lim
        """), {"lim": effective_limit})).all()

        log.info("enf_summary_selected count=%d", len(rows))
        if args.dry_run:
            est = len(rows) * cost_per_row
            log.info("enf_summary_dry_run estimated_usd=%.2f", est)
            await close_db()
            return 0

        client = boto3.client("bedrock-runtime", region_name=region)
        for row in rows:
            prompt = _PROMPT_TEMPLATE.format(
                regulator=row.regulator,
                action_date=row.action_date.isoformat() if row.action_date else "unknown",
                action_type=row.action_type,
                penalty=_format_penalty(row.penalty_amount_usd),
                summary=(row.violation_summary or "").strip()[:8000],
            )
            try:
                pretty = _invoke(client, model_id, prompt)
            except Exception as exc:  # noqa: BLE001 — Bedrock surfaces many classes
                log.error("enf_summary_invoke_failed id=%s err=%s", row.id, exc.__class__.__name__)
                failed += 1
                continue

            if not pretty:
                failed += 1
                continue

            await session.execute(text(
                "UPDATE enforcement_actions SET summary_pretty = :p WHERE id = :i"
            ), {"p": pretty, "i": row.id})
            summarised += 1

            if summarised % 25 == 0:
                await session.commit()
                log.info("enf_summary_progress committed=%d failed=%d", summarised, failed)

        await session.commit()

    log.info("enf_summary_done summarised=%d failed=%d", summarised, failed)
    await close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
