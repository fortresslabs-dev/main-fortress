"""Add embed_updated_at to compliance_rules for stale embedding detection.

Revision ID: 019
Revises: 018
Create Date: 2026-05-11

Allows reconcile_rules.py to find rules where rule_text changed
after the embedding was last generated (Category 4 invalid rules).
Non-breaking — column is nullable, existing rows get NULL.
"""
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE compliance_rules
        ADD COLUMN IF NOT EXISTS embed_updated_at TIMESTAMPTZ
    """)
    # Predicate intentionally omits `embedding IS NOT NULL` so this migration
    # is self-contained on a fresh DB. The embedding column itself is created
    # by migration 023 (after extensions are guaranteed to be installed).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cr_embed_stale
        ON compliance_rules (updated_at, embed_updated_at)
        WHERE is_active = TRUE
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cr_embed_stale")
    op.execute("ALTER TABLE compliance_rules DROP COLUMN IF EXISTS embed_updated_at")
