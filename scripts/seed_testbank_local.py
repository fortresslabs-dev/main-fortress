"""Local adapter for seed_testbank_dashboard.

The production seed (scripts/seed_testbank_dashboard.py) connects to Aurora with
discrete AURORA_HOST/DB_NAME/... env vars and ssl="require". Local docker Postgres
has no TLS, so this wrapper reuses the exact same seeding functions but opens a
plain (no-SSL) asyncpg connection pointed at the local container.

Run inside the pipeline2 image with the repo mounted:

  docker compose run --rm \
    -e SEED_DB_HOST=postgres -e SEED_DB_NAME=fortress_p2 \
    -e SEED_DB_USER=fortress -e SEED_DB_PASSWORD=fortress_dev \
    -v "$PWD":/app pipeline2 python -m scripts.seed_testbank_local
"""
from __future__ import annotations

import asyncio
import os

import asyncpg

from scripts.seed_testbank_dashboard import (
    _seed_bank_profile,
    _seed_bsa_officer,
    _seed_controls,
    _seed_control_rule_mappings,
    _seed_findings,
    _seed_ofac_validation,
    _seed_dispositions,
)


async def main() -> int:
    conn = await asyncpg.connect(
        host=os.environ.get("SEED_DB_HOST", "postgres"),
        port=int(os.environ.get("SEED_DB_PORT", "5432")),
        database=os.environ.get("SEED_DB_NAME", "fortress_p2"),
        user=os.environ.get("SEED_DB_USER", "fortress"),
        password=os.environ.get("SEED_DB_PASSWORD", "fortress_dev"),
        ssl=None,  # local container has no TLS (prod uses ssl="require")
    )
    try:
        async with conn.transaction():
            await _seed_bank_profile(conn)
            await _seed_bsa_officer(conn)
            controls_n = await _seed_controls(conn)
            mappings_n = await _seed_control_rule_mappings(conn)
            findings_n = await _seed_findings(conn)
            await _seed_ofac_validation(conn)
            dispositions_n = await _seed_dispositions(conn)

        print(
            f"seed_testbank_local: bank_profile=1 officers=1 "
            f"controls={controls_n} mappings={mappings_n} "
            f"findings={findings_n} ofac_validations=1 "
            f"dispositions={dispositions_n}"
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
