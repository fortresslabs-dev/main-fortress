"""
Resync P2 (testbank) from P1 (Fortress production).

Wipes every data-bearing table in the P2 Aurora/Postgres replica, then
triggers UpdateService.sync_all() which goes through _initial_sync (since
local_sync_status is empty → version 0) and re-pulls:
  - 224 compliance_rules + 1024-dim embeddings + tsvector
  - ofac_sdn_entries + ofac_sdn_vectors (variants, embeddings auto-heal)
  - obligations, enforcement_actions, enforcement_risk_scores,
    compliance_deadlines, regulatory_change_records, peer_benchmarks

Preserves: alembic_version (schema state), the schema itself.

Usage (inside the pipeline2 container):
    docker compose -f docker-compose.pipeline2.yml exec pipeline2 \\
        python -m scripts.setup.resync_p2_from_p1

Required env (already present in P2 container):
    DATABASE_URL, FORTRESS_API_URL, FORTRESS_API_KEY, CUSTOMER_ID
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text as sqla_text


# Order is irrelevant — TRUNCATE ... CASCADE handles FK dependencies in one tx.
# alembic_version is intentionally omitted (we keep the schema).
_TABLES_TO_WIPE = (
    # P2 working state
    "checkpoint_approvals",
    "workflow_executions",
    "transaction_validations",
    "ofac_screenings",
    "coverage_snapshots",
    "change_record_comments",
    # Sync bookkeeping (forces next sync_all() → _initial_sync)
    "local_sync_status",
    "sync_metadata",
    "sync_versions",
    "deleted_rules",
    # P1-mirrored regulatory data
    "regulatory_change_records",
    "compliance_deadlines",
    "enforcement_risk_scores",
    "enforcement_actions",
    "peer_benchmarks",
    "obligations",
    "compliance_rule_changes",
    "ofac_sdn_vectors",
    "ofac_sdn_entries",
    "compliance_rules",
    # Misc
    "client_credentials",
)


async def _snapshot(session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tbl in _TABLES_TO_WIPE:
        try:
            res = await session.execute(sqla_text(f"SELECT COUNT(*) FROM {tbl}"))
            counts[tbl] = int(res.scalar() or 0)
        except Exception as exc:
            counts[tbl] = -1
            print(f"  [skip] {tbl}: {exc}", file=sys.stderr)
    return counts


async def _wipe(session) -> None:
    stmt = "TRUNCATE TABLE " + ", ".join(_TABLES_TO_WIPE) + " RESTART IDENTITY CASCADE"
    await session.execute(sqla_text(stmt))
    await session.commit()


async def main() -> int:
    confirm = os.getenv("RESYNC_CONFIRM", "").lower()
    if confirm not in ("yes", "y", "1", "true"):
        print(
            "Refusing to run without RESYNC_CONFIRM=yes.\n"
            "This will TRUNCATE every data table in the P2 DB.",
            file=sys.stderr,
        )
        return 2

    # Hard guard: never run against a DATABASE_URL that looks like P1 prod
    db_url = os.getenv("DATABASE_URL", "")
    if "fortress-p1" in db_url or "pipeline1" in db_url:
        print(
            f"Refusing: DATABASE_URL looks like P1 ({db_url!r}). "
            "This script is testbank P2 only.",
            file=sys.stderr,
        )
        return 3

    from src.services.database import get_db
    from src.services.update_service import UpdateService

    print(f"DATABASE_URL     = {db_url}")
    print(f"FORTRESS_API_URL = {os.getenv('FORTRESS_API_URL')}")
    print(f"CUSTOMER_ID      = {os.getenv('CUSTOMER_ID')}")
    print()

    # 1. Snapshot
    print("── Before ──────────────────────────────────────────────")
    async with get_db() as session:
        before = await _snapshot(session)
    for tbl, n in before.items():
        print(f"  {tbl:<32} {n:>10}")

    # 2. Wipe
    print("\n── Truncating ──────────────────────────────────────────")
    async with get_db() as session:
        await _wipe(session)
    print("  done.")

    # 3. Trigger initial sync
    print("\n── Sync from P1 ────────────────────────────────────────")
    svc = UpdateService()
    if not svc._customer_id or not svc._api_key:
        print(
            "CUSTOMER_ID and/or FORTRESS_API_KEY not set — UpdateService cannot run.",
            file=sys.stderr,
        )
        return 4
    result = await svc.sync_all()
    for k, v in result.items():
        print(f"  {k:<24} {v}")

    # 4. Verify
    print("\n── After ───────────────────────────────────────────────")
    async with get_db() as session:
        after = await _snapshot(session)
    for tbl, n in after.items():
        delta = ""
        if before.get(tbl, 0) >= 0 and n >= 0:
            delta = f"  (was {before[tbl]})"
        print(f"  {tbl:<32} {n:>10}{delta}")

    return 0 if result.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
