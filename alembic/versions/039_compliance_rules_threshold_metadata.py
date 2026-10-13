"""Add threshold_default + threshold_rationale + threshold_source_citation to compliance_rules.

Revision ID: 039
Revises: 038
Create Date: 2026-06-21

SR 11-7 model-risk governance requires every numeric threshold used by a
validator to carry documented rationale and a source citation. Until now
those values lived as Python constants in
``src/services/compliance_rule_resolver.py`` ``_FALLBACK_THRESHOLDS`` —
which means the source of truth was code, not data, and the resolver
couldn't be audited as a system-of-record.

This migration adds three nullable columns. Backfill (seeding the
existing 6 fallback rule classes from the resolver constants) is handled
by ``scripts/setup/seed_threshold_defaults.py`` so the migration stays
fast and reversible.

Why JSONB for threshold_default: a single rule may carry several named
sub-thresholds (e.g. wire_transfer has ctr / dual_approval / travel),
mirroring the resolver's nested shape:

    {"ctr_threshold": 10000, "dual_approval_threshold": 10000,
     "travel_rule_threshold": 3000}

Additive change — no existing rows touched. Per
``feedback_p1_alembic_blocks_rollback`` rolling back to a pre-039 image
requires stamping Aurora down first:

    python -m alembic stamp 038
    # then deploy old image / task def
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compliance_rules",
        sa.Column("threshold_default", JSONB, nullable=True),
    )
    op.add_column(
        "compliance_rules",
        sa.Column("threshold_rationale", sa.Text, nullable=True),
    )
    op.add_column(
        "compliance_rules",
        sa.Column("threshold_source_citation", sa.Text, nullable=True),
    )

    # Hot read path is "rules with documented thresholds" — partial index
    # keeps the scan footprint small as the table grows past the seeded
    # fallback set.
    op.execute("""
        CREATE INDEX idx_cr_has_threshold
        ON compliance_rules ((threshold_default IS NOT NULL))
        WHERE threshold_default IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cr_has_threshold")
    op.drop_column("compliance_rules", "threshold_source_citation")
    op.drop_column("compliance_rules", "threshold_rationale")
    op.drop_column("compliance_rules", "threshold_default")
