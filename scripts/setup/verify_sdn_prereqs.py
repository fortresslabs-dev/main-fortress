"""Aurora prerequisites probe for the OFAC SDN vector store rollout.

Run this once before applying migration 021. It reports:
  - whether the `vector`, `pg_trgm`, and `unaccent` extensions are installed
    (and tries to create them if missing — needs rds_superuser)
  - how many SDN entities are currently in `ofac_sdn_entries` (sizes the embed job)
  - rough variant count estimate (entity + aliases) so you can budget Bedrock calls

Run:
  PIPELINE_MODE=pipeline1 python -m scripts.setup.verify_sdn_prereqs
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from src.services.database import init_db, close_db, get_db


_REQUIRED_EXTENSIONS = ("vector", "pg_trgm", "unaccent")


async def _check_extension(session, name: str) -> tuple[bool, str]:
    row = (await session.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = :n"),
        {"n": name},
    )).first()
    if row:
        return True, f"installed (v{row[0]})"

    try:
        await session.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{name}"'))
        return True, "created"
    except Exception as exc:
        return False, f"missing — CREATE failed: {exc}"


async def _count_entities(session) -> int:
    row = (await session.execute(
        text("SELECT count(*) FROM ofac_sdn_entries WHERE is_active = TRUE")
    )).first()
    return int(row[0]) if row else 0


async def _estimate_variants(session) -> int:
    """1 row per primary name + 1 per alias. Aliases are stored as JSON arrays."""
    row = (await session.execute(text("""
        SELECT COALESCE(SUM(
            1 + COALESCE(jsonb_array_length(aliases::jsonb), 0)
        ), 0)
        FROM ofac_sdn_entries
        WHERE is_active = TRUE
    """))).first()
    return int(row[0]) if row else 0


async def main() -> int:
    await init_db()
    try:
        async with get_db() as session:
            print("─" * 60)
            print("Extension checks")
            print("─" * 60)
            all_ok = True
            for ext in _REQUIRED_EXTENSIONS:
                ok, status = await _check_extension(session, ext)
                marker = "OK " if ok else "FAIL"
                print(f"  [{marker}] {ext:<10} → {status}")
                all_ok = all_ok and ok

            print()
            print("─" * 60)
            print("Embed job sizing")
            print("─" * 60)

            entities = await _count_entities(session)
            variants = await _estimate_variants(session)
            print(f"  active SDN entities:   {entities:>7,}")
            print(f"  estimated variants:    {variants:>7,}  (primary + aliases)")
            # Titan v2: $0.00002 / 1k input tokens; avg ~10 tokens per name → ~$0.0000002/row
            est_cost = variants * 10 / 1000 * 0.00002
            print(f"  Bedrock cost estimate: ${est_cost:>7.2f}  (Titan v2, ~10 tok/name)")
            # 50ms/row sequential, 5x semaphore → variants / 5 * 0.05s
            est_seconds = variants / 5 * 0.05
            print(f"  embed runtime (5 in-flight): ~{est_seconds/60:.1f} min")

        if not all_ok:
            print()
            print("One or more extensions are missing and could not be created.")
            print("Ask infra to enable them on the Aurora parameter group:")
            print("  shared_preload_libraries = vector")
            print("  ALTER USER fortress WITH ROLE rds_superuser;  -- one-time")
            return 1

        print()
        print("All prerequisites satisfied — safe to run `alembic upgrade head`.")
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
