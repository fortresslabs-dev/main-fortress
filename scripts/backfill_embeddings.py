"""Backfill Titan Embed v2 (1024-dim) vectors for compliance_rules and/or
obligations rows that are missing them.

Idempotent & additive: only rows WHERE embedding IS NULL are touched, so it is
always safe to re-run (a no-op once everything is embedded). Uses the app DB
layer (DATABASE_URL / settings), so it runs unchanged locally (docker compose),
on P1, or inside a bank's P2. Needs Bedrock access to
`amazon.titan-embed-text-v2:0` in AWS_REGION.

The obligations target requires migration 052 (adds `obligations.embedding`).

Usage:
    python -m scripts.backfill_embeddings --target both
    python -m scripts.backfill_embeddings --target rules --dry-run
    python -m scripts.backfill_embeddings --target obligations

Exit code: non-zero if any embedding call failed (so EventBridge/CI surface it).

Supersedes the ad-hoc scaffolds and the embed phase in
scraper_scheduler._embed_and_store_rules. See docs/runbooks/embedding-backfill.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("backfill_embeddings")

TITAN_MODEL = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 1024

# (select-missing, update-one, label). Text source mirrors
# scraper_scheduler._embed_and_store_rules: rule_text + cfr_citation + category
# for rules; source_text for obligations.
_TARGETS = {
    "rules": (
        "SELECT rule_id AS id, "
        "       concat_ws(' ', rule_text, cfr_citation, category) AS body "
        "FROM compliance_rules "
        "WHERE embedding IS NULL AND is_active = TRUE AND rule_text IS NOT NULL",
        "UPDATE compliance_rules SET embedding = CAST(:emb AS vector), updated_at = now() "
        "WHERE rule_id = :id",
    ),
    "obligations": (
        "SELECT obligation_id AS id, source_text AS body "
        "FROM obligations "
        "WHERE embedding IS NULL AND is_active = TRUE AND source_text IS NOT NULL",
        "UPDATE obligations SET embedding = CAST(:emb AS vector) WHERE obligation_id = :id",
    ),
}


def _bedrock_client():
    import boto3
    return boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _embed(client, body: str) -> list:
    resp = client.invoke_model(
        modelId=TITAN_MODEL,
        body=json.dumps({"inputText": (body or "")[:8000], "dimensions": EMBED_DIM, "normalize": True}),
    )
    return json.loads(resp["body"].read())["embedding"]


async def _backfill(target: str, dry_run: bool) -> dict:
    from sqlalchemy import text as sqla_text
    from src.services.database import get_db

    select_sql, update_sql = _TARGETS[target]
    async with get_db() as session:
        rows = (await session.execute(sqla_text(select_sql))).fetchall()

    missing = len(rows)
    logger.info("backfill_start target=%s missing=%d dry_run=%s", target, missing, dry_run)
    if dry_run or missing == 0:
        return {"target": target, "missing": missing, "embedded": 0, "failed": 0, "dry_run": dry_run}

    client = _bedrock_client()
    embedded = failed = 0
    for row in rows:
        try:
            emb = _embed(client, row.body)
            async with get_db() as session:  # get_db commits on clean exit
                await session.execute(sqla_text(update_sql), {"emb": str(emb), "id": row.id})
            embedded += 1
            if embedded % 25 == 0:
                logger.info("backfill_progress target=%s %d/%d", target, embedded, missing)
        except Exception as exc:
            failed += 1
            logger.warning("embed_failed target=%s id=%s error=%s", target, row.id, str(exc)[:160])

    logger.info("backfill_complete target=%s embedded=%d failed=%d", target, embedded, failed)
    return {"target": target, "missing": missing, "embedded": embedded, "failed": failed, "dry_run": False}


async def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill Titan v2 embeddings (rules/obligations)")
    ap.add_argument("--target", choices=["rules", "obligations", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true", help="count missing rows; embed nothing")
    args = ap.parse_args(argv)

    from src.services.database import init_db, close_db
    await init_db()
    try:
        targets = ["rules", "obligations"] if args.target == "both" else [args.target]
        results = []
        for t in targets:
            results.append(await _backfill(t, args.dry_run))
    finally:
        await close_db()

    for r in results:
        logger.info("result %s", json.dumps(r))
    return 1 if any(r["failed"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
