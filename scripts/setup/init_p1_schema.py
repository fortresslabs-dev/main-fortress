"""Pipeline 1 — Aurora schema bootstrap, sourced from the SQLAlchemy ORM.

SUPERSEDED — the pgvector `embedding`, generated `tsv` column, and the
HNSW/GIN indexes this script installs are now created by alembic migration
023 (`023_compliance_rules_pgvector.py`). New deploys should run
`alembic upgrade head` instead. This script remains only for legacy
reference and the destructive pilot-table-drop path; do not run it against
a populated database (see memory `feedback_init_p1_schema_destructive`).

Runs inside the pipeline1 container as the EcsTaskDefInitP1 one-shot task.
Uses `Base.metadata.create_all()` so the schema can never drift from the ORM
models in `src/models/database.py`.

Reads:
  Secrets Manager: fortress/p1/db-password  (key: password)
  SSM Parameter:   /fortress/pipeline1/AURORA_HOST
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import boto3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Import ORM Base so create_all knows about every model
from src.models.database import Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("init_p1_schema")

# Tables that may have been created by the old hand-written bootstrap with
# divergent column names; drop them so create_all rebuilds from ORM.
_PILOT_DROP_TABLES = [
    "alembic_version",
    "change_record_comments",
    "checkpoint_approvals",
    "client_credentials",
    "compliance_deadlines",
    "compliance_rule_changes",
    "compliance_rules",
    "coverage_snapshots",
    "deleted_rules",
    "enforcement_actions",
    "enforcement_risk_scores",
    "local_sync_status",
    "obligations",
    "ofac_screenings",
    "ofac_sdn_entries",
    "peer_benchmarks",
    "regulatory_change_records",
    "scrape_runs",
    "sync_metadata",
    "sync_versions",
    "transaction_validations",
    "workflow_executions",
]


async def setup_schema() -> None:
    region = os.environ.get("AWS_REGION", "us-east-1")

    sm = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(
        sm.get_secret_value(SecretId="fortress/p1/db-password")["SecretString"]
    )
    password = secret["password"]

    ssm = boto3.client("ssm", region_name=region)
    host = ssm.get_parameter(Name="/fortress/pipeline1/AURORA_HOST")["Parameter"]["Value"]

    # asyncpg accepts the password as a URL component only when URL-safe; the
    # secret excludes URL-reserved chars (pipeline1-data.yml), so direct
    # substitution is fine.
    db_url = (
        f"postgresql+asyncpg://fortress_p1:{password}@{host}:5432/fortress_p1?ssl=require"
    )
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        logger.info("creating extensions")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))

        logger.info("dropping legacy pilot tables (if any)")
        for table_name in _PILOT_DROP_TABLES:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

        logger.info("creating ORM tables")
        await conn.run_sync(Base.metadata.create_all)

        logger.info("adding pgvector + tsvector columns to compliance_rules")
        await conn.execute(text("""
            ALTER TABLE compliance_rules
              ADD COLUMN IF NOT EXISTS embedding vector(1024),
              ADD COLUMN IF NOT EXISTS embed_updated_at TIMESTAMPTZ,
              ADD COLUMN IF NOT EXISTS tsv tsvector
                GENERATED ALWAYS AS (
                  to_tsvector('english',
                    coalesce(rule_text,'') || ' ' ||
                    coalesce(cfr_citation,'') || ' ' ||
                    coalesce(regulator,''))
                ) STORED
        """))

        # HNSW vector index — requires `embedding` column to exist.
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_cr_hnsw
              ON compliance_rules USING hnsw (embedding vector_cosine_ops)
              WITH (m = 16, ef_construction = 64)
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_cr_tsv ON compliance_rules USING GIN (tsv)"
        ))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_cr_embed_stale
              ON compliance_rules (updated_at, embed_updated_at)
              WHERE is_active = TRUE AND embedding IS NOT NULL
        """))

        # scrape_runs is owned by the MCP scrape_monitor (raw SQL, no ORM).
        logger.info("creating scrape_runs (MCP scrape monitor table)")
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                run_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                connector_name   VARCHAR(100) NOT NULL,
                success          BOOLEAN NOT NULL,
                rules_extracted  INTEGER DEFAULT 0,
                rules_changed    INTEGER DEFAULT 0,
                error_message    TEXT DEFAULT '',
                duration_seconds FLOAT DEFAULT 0,
                s3_key           VARCHAR(500) DEFAULT '',
                created_at       TIMESTAMPTZ DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_sr_connector_run "
            "ON scrape_runs (connector_name, run_at DESC)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_sr_success ON scrape_runs (success, run_at DESC)"
        ))

        logger.info("seeding sync_versions row 1")
        await conn.execute(text(
            "INSERT INTO sync_versions (id, current_version) "
            "VALUES (1, 0) ON CONFLICT (id) DO NOTHING"
        ))

        logger.info("stamping alembic_version=019")
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            )
        """))
        await conn.execute(text(
            "INSERT INTO alembic_version (version_num) VALUES ('019') "
            "ON CONFLICT (version_num) DO NOTHING"
        ))

        # Sanity check — print every public table for the logs.
        tables = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ))
        logger.info("schema verification: %d tables", len(list(tables)))

    await engine.dispose()
    logger.info("SCHEMA SETUP COMPLETE")


if __name__ == "__main__":
    asyncio.run(setup_schema())
