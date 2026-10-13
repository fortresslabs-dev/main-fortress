"""Add editorial severity grade to compliance_rules.

Revision ID: 051
Revises: 050
Create Date: 2026-10-09

The FinCEN 31 CFR Part 1010 obligation map (``fincen_part1010`` connector,
seeded from ``bsa.aml.latest``) grades every obligation Critical / High /
Medium / Low. There was no home for that grade: ``enforcement_risk_score``
is a *computed* 0–1 metric that the EnforcementRiskScorer overwrites each
cycle, and KPI ``criticality`` is never persisted.

This adds a dedicated, source-of-record ``severity`` column so the grade
survives the scrape cycle and can drive CCO-dashboard prioritisation and
delta-sync to the bank side (P2). Nullable + no server default so existing
rows and connectors that don't emit a grade are unaffected. A CHECK
constraint pins the vocabulary; added ``NOT VALID`` so the migration is fast
and legacy NULL rows are untouched, then validated in the same pass (no
existing non-null rows exist yet).

Chains on the ``050`` reconciliation placeholder — see
``049_reconcile_placeholder`` for why the deployed DB was two revisions ahead
of this working copy.
"""
from alembic import op
import sqlalchemy as sa


revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


_SEVERITIES = ("Critical", "High", "Medium", "Low")


def upgrade() -> None:
    op.add_column("compliance_rules", sa.Column("severity", sa.String(20), nullable=True))

    vocab = ", ".join(f"'{s}'" for s in _SEVERITIES)
    op.execute(f"""
        ALTER TABLE compliance_rules
          ADD CONSTRAINT ck_cr_severity_vocab
          CHECK (severity IS NULL OR severity IN ({vocab}))
          NOT VALID;
    """)
    op.execute("ALTER TABLE compliance_rules VALIDATE CONSTRAINT ck_cr_severity_vocab;")

    op.create_index("idx_cr_severity", "compliance_rules", ["severity"])


def downgrade() -> None:
    op.drop_index("idx_cr_severity", table_name="compliance_rules")
    op.execute("ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_cr_severity_vocab;")
    op.drop_column("compliance_rules", "severity")
