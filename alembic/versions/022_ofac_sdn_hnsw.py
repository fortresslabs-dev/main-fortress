"""Build HNSW index on ofac_sdn_vectors.embedding after backfill.

Revision ID: 022
Revises: 021
Create Date: 2026-05-21

Runs CREATE INDEX CONCURRENTLY so this can be applied online against a
populated table without blocking the embed job or the screener. Only run
this AFTER the initial embedding backfill — building HNSW on an empty
table works but incremental updates per-insert are an order of magnitude
slower than bulk-build.

m=16, ef_construction=64 — sized for ~50k rows (current SDN ~30k + headroom).
Bump ef_search at screen time via `SET LOCAL hnsw.ef_search = 100`.
"""
from alembic import op


revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY must run outside a transaction
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sdn_vectors_embedding
            ON ofac_sdn_vectors
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_sdn_vectors_embedding")
