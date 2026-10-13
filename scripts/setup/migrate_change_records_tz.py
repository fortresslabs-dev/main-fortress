"""One-shot ALTER for regulatory_change_records timestamp columns.

Converts detected_at / status_updated_at / signed_off_at / created_at from
TIMESTAMP WITHOUT TIME ZONE → TIMESTAMPTZ. The stored values are treated as
UTC (matching ChangeManager's produced datetimes).

Runs as a one-shot Fargate task using EcsTaskDefInitP1 with a command
override:

    aws ecs run-task --task-definition <init-arn> \\
      --overrides '{"containerOverrides":[{"name":"pipeline1-init",
        "command":["python3","-m","scripts.setup.migrate_change_records_tz"]}]}'

Idempotent — Postgres returns no-op on a column that already has the target
type. Bumps alembic_version 019 → 020 on completion.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import boto3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("migrate_change_records_tz")

_COLUMNS = ["detected_at", "status_updated_at", "signed_off_at", "created_at"]


async def run() -> None:
    region = os.environ.get("AWS_REGION", "us-east-1")

    sm = boto3.client("secretsmanager", region_name=region)
    password = json.loads(
        sm.get_secret_value(SecretId="fortress/p1/db-password")["SecretString"]
    )["password"]

    ssm = boto3.client("ssm", region_name=region)
    host = ssm.get_parameter(Name="/fortress/pipeline1/AURORA_HOST")["Parameter"]["Value"]

    db_url = (
        f"postgresql+asyncpg://fortress_p1:{password}@{host}:5432/fortress_p1?ssl=require"
    )
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        for col in _COLUMNS:
            logger.info("altering column %s", col)
            await conn.execute(text(f"""
                ALTER TABLE regulatory_change_records
                ALTER COLUMN {col} TYPE TIMESTAMPTZ
                USING {col} AT TIME ZONE 'UTC'
            """))

        logger.info("stamping alembic_version=020")
        await conn.execute(text(
            "UPDATE alembic_version SET version_num='020' WHERE version_num='019'"
        ))

        for col in _COLUMNS:
            row = (await conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='regulatory_change_records' AND column_name=:c"
            ), {"c": col})).fetchone()
            logger.info("verified %s → %s", col, row[0] if row else "MISSING")

    await engine.dispose()
    logger.info("MIGRATION COMPLETE")


if __name__ == "__main__":
    asyncio.run(run())
