"""Standalone embed job for ofac_sdn_vectors.

Runs as its own ECS task (NOT inline in the scraper). Picks up rows where
embedding IS NULL and is_active = TRUE, calls Bedrock Titan v2, writes the
1024-dim vector + embedded_at.

Titan v2's InvokeModel API only accepts a singular `inputText` — there is no
multi-input batch form on that endpoint. To get throughput we fan out via a
semaphore-bounded asyncio.to_thread pool. Bedrock Batch Inference (S3-driven)
is a future optimization if cost/time becomes painful.

Idempotency: only embeds NULL rows, so reruns are safe.
Crash safety: commits every BATCH_COMMIT_SIZE rows; on restart we resume
from the next NULL row.

Run:
  PIPELINE_MODE=pipeline1 python -m src.jobs.embed_sdn_variants
Env:
  AWS_REGION              (default us-east-1)
  SDN_EMBED_CONCURRENCY   (default 5)
  SDN_EMBED_LIMIT         (default 0 = no cap; useful for testing)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import boto3
from sqlalchemy import text

from src.services.database import init_db, close_db, get_db


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_MODEL = "amazon.titan-embed-text-v2:0"
_FETCH_BATCH = 200          # rows per DB fetch
_BATCH_COMMIT_SIZE = 50     # rows per commit (limits replay window on crash)
_CONCURRENCY = int(os.getenv("SDN_EMBED_CONCURRENCY", "5"))
_TOTAL_CAP = int(os.getenv("SDN_EMBED_LIMIT", "0"))


def _embed_sync(client, name: str) -> list[float]:
    """Synchronous Titan call — wrapped in asyncio.to_thread by caller."""
    resp = client.invoke_model(
        modelId=_MODEL,
        body=json.dumps({
            "inputText": name,
            "dimensions": 1024,
            "normalize": True,   # unit-norm so cosine == dot product
        }),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


async def _embed_one(client, semaphore, row_id: int, name: str) -> tuple[int, list[float] | None]:
    async with semaphore:
        try:
            vec = await asyncio.to_thread(_embed_sync, client, name)
            return row_id, vec
        except Exception as exc:
            log.error("embed_failed row_id=%s err=%s", row_id, exc)
            return row_id, None


async def embed_pending(cap: int | None = None) -> int:
    """Embed NULL rows in ofac_sdn_vectors. Returns count embedded.

    `cap` overrides `SDN_EMBED_LIMIT` (module-level `_TOTAL_CAP`). UpdateService
    passes a per-sync cap (default 2000) so auto-heal stays under ~1 minute.
    None or 0 means "no cap" (full backfill — the standalone-task usage).
    """
    effective_cap = cap if cap is not None else _TOTAL_CAP
    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("bedrock-runtime", region_name=region)
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    total = 0

    while True:
        if effective_cap and total >= effective_cap:
            break

        async with get_db() as session:
            rows = (await session.execute(text("""
                SELECT id, name_normalized
                FROM ofac_sdn_vectors
                WHERE embedding IS NULL
                  AND is_active = TRUE
                ORDER BY id
                LIMIT :lim
            """), {"lim": _FETCH_BATCH})).all()

        if not rows:
            break

        # Fan out
        tasks = [_embed_one(client, semaphore, r.id, r.name_normalized) for r in rows]
        results = await asyncio.gather(*tasks)

        # Persist in chunks; each chunk is its own transaction so a crash
        # mid-batch still leaves earlier successes committed.
        for i in range(0, len(results), _BATCH_COMMIT_SIZE):
            chunk = [(rid, vec) for rid, vec in results[i : i + _BATCH_COMMIT_SIZE] if vec is not None]
            if not chunk:
                continue
            async with get_db() as session:
                for row_id, vec in chunk:
                    await session.execute(text("""
                        UPDATE ofac_sdn_vectors
                        SET embedding   = CAST(:vec AS vector),
                            embedded_at = :now
                        WHERE id = :id
                    """), {"vec": str(vec), "now": datetime.now(timezone.utc), "id": row_id})

        embedded_now = sum(1 for _, v in results if v is not None)
        total += embedded_now
        log.info("embed_progress batch=%d cumulative=%d", embedded_now, total)

    log.info("embed_done total=%d", total)
    return total


async def main() -> int:
    await init_db()
    try:
        await embed_pending()
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
