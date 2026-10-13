"""Create ofac_sdn_vectors with pgvector + pg_trgm + tsvector.

Revision ID: 021
Revises: 020
Create Date: 2026-05-21

One row per (sdn_number, name_normalized) — primary name + each alias gets
its own row so the screener can match on aliases (TC-ALIAS-001) without
post-join gymnastics. HNSW is built in 022 after the first embed backfill;
empty HNSW indexes update slowly on every insert.

Safety columns (is_active, missed_pulls, last_seen_at, deactivated_at) drive
N-strikes soft-deactivation in src/services/ofac_sdn_loader.py. They are
NEVER hard-deleted — a single corrupt scrape must not unblock a real SDN.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR


revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions (idempotent) ──────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # ── Table ────────────────────────────────────────────────────────────
    op.create_table(
        "ofac_sdn_vectors",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("sdn_number", sa.String(50), nullable=False),
        sa.Column("name_variant", sa.Text, nullable=False),
        sa.Column("name_normalized", sa.Text, nullable=False),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("programs", sa.JSON, nullable=True, server_default=sa.text("'[]'::json")),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("nationality", sa.String(100), nullable=True),
        # tsv is auto-maintained by trigger below — declared here so SQLAlchemy
        # is aware of the column type when reflecting.
        sa.Column("tsv", TSVECTOR, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("missed_pulls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("sdn_number", "name_normalized", name="uq_sdn_vectors_sdn_norm"),
    )

    # ── embedding column added via raw SQL (Vector type needs pgvector
    #    Python package at import time, which we keep out of the ORM)
    op.execute("ALTER TABLE ofac_sdn_vectors ADD COLUMN embedding vector(1024)")

    # ── btree indexes ────────────────────────────────────────────────────
    op.create_index("ix_sdn_vectors_sdn_number", "ofac_sdn_vectors", ["sdn_number"])
    # Partial index — the screener only ever needs active rows; this keeps
    # the scan footprint small even when the table accumulates years of
    # deactivated entries.
    op.execute("""
        CREATE INDEX ix_sdn_vectors_active
        ON ofac_sdn_vectors (sdn_number)
        WHERE is_active = TRUE
    """)

    # ── GIN trigram on normalized name ───────────────────────────────────
    # Used via `%` operator (index-accelerated). Bare similarity() does NOT
    # use this index — see review item §4 — so the screener must use `%`.
    op.execute("""
        CREATE INDEX ix_sdn_vectors_trgm
        ON ofac_sdn_vectors
        USING GIN (name_normalized gin_trgm_ops)
    """)

    # ── GIN full-text on tsv ─────────────────────────────────────────────
    op.execute("""
        CREATE INDEX ix_sdn_vectors_tsv
        ON ofac_sdn_vectors
        USING GIN (tsv)
    """)

    # ── tsv trigger (simple config — no stemming for proper nouns) ───────
    op.execute("""
        CREATE OR REPLACE FUNCTION ofac_sdn_vectors_tsv_trigger()
        RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('simple', NEW.name_normalized);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trig_ofac_sdn_vectors_tsv
        BEFORE INSERT OR UPDATE OF name_normalized
        ON ofac_sdn_vectors
        FOR EACH ROW EXECUTE FUNCTION ofac_sdn_vectors_tsv_trigger()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trig_ofac_sdn_vectors_tsv ON ofac_sdn_vectors")
    op.execute("DROP FUNCTION IF EXISTS ofac_sdn_vectors_tsv_trigger")
    op.drop_table("ofac_sdn_vectors")
