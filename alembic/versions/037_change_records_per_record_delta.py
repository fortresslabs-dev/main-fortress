"""Add per-record change_delta to regulatory_change_records.

Revision ID: 037
Revises: 036
Create Date: 2026-06-18

Until now the change-detail drawer surfaced `change_delta` from
`compliance_rules` — which is overwritten on every rescrape. Any change
record older than the rule's *latest* mutation displayed the wrong
added/removed sentences. This migration adds a per-record JSONB column so
each row holds the diff that was computed at detection time.

Existing rows stay NULL; the dashboard treats NULL as "diff not
preserved" so the misleading fallback is gone. New records populated by
`ChangeManager.record_change()` going forward.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regulatory_change_records",
        sa.Column("change_delta", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("regulatory_change_records", "change_delta")
