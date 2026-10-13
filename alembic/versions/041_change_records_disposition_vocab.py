"""Add disposition_vocab_version to regulatory_change_records.

Revision ID: 041
Revises: 040
Create Date: 2026-06-21

The change_log export (design doc §5.1, §4 type 5) freezes the
vocabulary used to describe a disposition (acknowledged /
requires_action / not_applicable). Per Q18 vocab_versions is the
registry; this column records WHICH version was in force when the
change was first recorded.

When the dashboard renders an export, it pins each row to the vocab
that was current at change-record time, not today's vocab — so an
artifact rendered six months later still reads correctly against the
historical taxonomy.

Additive change. Rollback (per feedback_p1_alembic_blocks_rollback):
    python -m alembic stamp 040
"""
from alembic import op
import sqlalchemy as sa


revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regulatory_change_records",
        sa.Column("disposition_vocab_version", sa.String(32), nullable=True),
    )
    # No CHECK on the value — vocab_versions is the source of truth and
    # lives in P2 (added as M029p2). Reusing the same version_id across
    # P1 and P2 keeps the cross-pipeline join simple.
    op.create_index(
        "ix_regch_vocab_version",
        "regulatory_change_records",
        ["disposition_vocab_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_regch_vocab_version", table_name="regulatory_change_records")
    op.drop_column("regulatory_change_records", "disposition_vocab_version")
