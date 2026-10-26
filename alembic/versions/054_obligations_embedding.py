"""obligations.embedding — per-obligation Titan v2 vectors (item 2)

Adds a 1024-dim pgvector column on obligations so retrieval can operate at the
*obligation* grain (the testable unit), not just whole rules, and so Agent 4
can pull on-topic related obligations as grounding context. Backfill the column
with the same Titan Embed v2 path used for compliance_rules.embedding
(see docs/runbooks/embedding-backfill.md).

Revision ID: 052
Revises: 051
"""
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension already present (compliance_rules.embedding). Add the
    # column nullable so existing rows survive; backfill populates it.
    op.execute("ALTER TABLE obligations ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    # HNSW for cosine similarity on the obligation grain. Small table, plain
    # (non-concurrent) build is fine; make it concurrent in prod if needed.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_obligations_embedding_hnsw "
        "ON obligations USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_obligations_embedding_hnsw")
    op.execute("ALTER TABLE obligations DROP COLUMN IF EXISTS embedding")
