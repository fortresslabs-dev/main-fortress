"""Move compliance_rules pgvector + tsvector setup into alembic.

Revision ID: 023
Revises: 022
Create Date: 2026-05-27

Until this migration, `embedding vector(1024)`, the generated `tsv` column,
and the `idx_cr_hnsw` / `idx_cr_tsv` indexes were only created by the
destructive side-script `scripts/setup/init_p1_schema.py`. P1 prod has them
because that script ran once at bootstrap; a fresh P2 Aurora doesn't.

Everything here is idempotent (`IF NOT EXISTS`), so applying this against
P1 prod is a no-op.
"""
from alembic import op


revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.execute("""
        ALTER TABLE compliance_rules
          ADD COLUMN IF NOT EXISTS embedding vector(1024),
          ADD COLUMN IF NOT EXISTS tsv tsvector
            GENERATED ALWAYS AS (
              to_tsvector('english',
                coalesce(rule_text,'') || ' ' ||
                coalesce(cfr_citation,'') || ' ' ||
                coalesce(regulator,''))
            ) STORED
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cr_hnsw
          ON compliance_rules USING hnsw (embedding vector_cosine_ops)
          WITH (m = 16, ef_construction = 64)
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cr_tsv "
        "ON compliance_rules USING GIN (tsv)"
    )


def downgrade() -> None:
    # M6 — this downgrade is irreversibly destructive: dropping `embedding`
    # discards every compliance-rule vector and forces a multi-hour Titan
    # re-embed before the resolver can return non-degraded results. Require
    # an explicit ack so a misfired `alembic downgrade` can't wipe prod
    # silently. Set FORTRESS_ALLOW_DESTRUCTIVE_DOWNGRADE=1 to proceed.
    import os
    if os.environ.get("FORTRESS_ALLOW_DESTRUCTIVE_DOWNGRADE") != "1":
        raise RuntimeError(
            "alembic downgrade past 023 drops compliance_rules.embedding and "
            "discards ~25k pgvector rows. Re-running upgrade requires a full "
            "Titan re-embed. Set FORTRESS_ALLOW_DESTRUCTIVE_DOWNGRADE=1 if "
            "this is intentional."
        )
    op.execute("DROP INDEX IF EXISTS idx_cr_tsv")
    op.execute("DROP INDEX IF EXISTS idx_cr_hnsw")
    op.execute("ALTER TABLE compliance_rules DROP COLUMN IF EXISTS tsv")
    op.execute("ALTER TABLE compliance_rules DROP COLUMN IF EXISTS embedding")
