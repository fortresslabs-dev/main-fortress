"""Add citation_verified_source discriminator to compliance_rules.

Revision ID: 047
Revises: 046
Create Date: 2026-07-13

Migration 046 introduced ``citation_verified`` as a single boolean. The
verification report in the ``feature/rule_correction`` branch history
(Bug 3) showed this shape can't distinguish an eCFR-section verification
from a chapter, appendix, USC, or state-code one — every non-eCFR-section
verifier has to write into the same True, losing provenance.

This migration adds a nullable discriminator column so the gate stays a
cheap boolean read while the DB still records *how* verification
happened. The column is not part of the gate predicate — it is
diagnostic and audit-trail material.

Values are free-form short strings written by each verifier, e.g.::

    "ecfr_section"     — src/services/ecfr_client.py, kind=section
    "ecfr_appendix"    — src/services/ecfr_client.py, kind=appendix
    "ecfr_chapter"     — src/services/ecfr_client.py, kind=chapter
    "usc_lii"          — hypothetical USC verifier (future)
    "nydfs_500"        — state-code verifier (future)

A partial index on ``citation_verified_source`` supports "which
verifier is silent this week?" ops queries without inflating the hot
gate index (``idx_cr_gateable``).
"""
from alembic import op
import sqlalchemy as sa


revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compliance_rules",
        sa.Column("citation_verified_source", sa.String(40), nullable=True),
    )
    # Cross-check invariant: if verified, we must know by whom. Added
    # NOT VALID so the migration is fast; connectors backfill on next run.
    op.execute("""
        ALTER TABLE compliance_rules
          ADD CONSTRAINT ck_cr_verified_source_present
          CHECK (
            NOT citation_verified
            OR citation_verified_source IS NOT NULL
          )
          NOT VALID;
    """)
    op.create_index(
        "idx_cr_verifier_source",
        "compliance_rules",
        ["citation_verified_source"],
        postgresql_where=sa.text("citation_verified_source IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_cr_verifier_source", table_name="compliance_rules")
    op.execute(
        "ALTER TABLE compliance_rules "
        "DROP CONSTRAINT IF EXISTS ck_cr_verified_source_present;"
    )
    op.drop_column("compliance_rules", "citation_verified_source")
