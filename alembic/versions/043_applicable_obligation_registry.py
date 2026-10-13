"""Add applicable_obligation_registry — per-charter/per-tier target obligation set.

Revision ID: 043
Revises: 042
Create Date: 2026-07-05

Ships the schema for the P1 registry called out in the readiness audit
(``docs/examination_readiness_parameter_audit.md`` §P1). Pre-R10 the
Obligation-inventory widget's denominator was ``COUNT(compliance_rules)``
— i.e. what the scraper picked up — so a 100% score meant "every ingested
rule has a CFR citation," not "every obligation this bank owes is on
file." The audit's verdict:

> Ship an applicable-obligation registry (per charter, per asset tier)
> and change the denominator. Until then, present the metric as
> "Ingestion completeness" — honest, and defensible.

This migration ships the *schema* (idempotent, empty at rollout). The
denominator switch in ``src/api/dashboard_api.py`` reads the registry at
request time and, when it has rows for the caller's bank scope, uses it
as the denominator — otherwise falls back to the pre-R10 count and the
widget label degrades honestly to "Ingestion completeness."

Registry rows are population-time data (charter templates, per-tier
delta sets) authored by compliance ops. R10 does not seed the table —
that's a follow-on runbook, so the schema can land ahead of the
authored content without pinning the widget to zero on any bank whose
registry hasn't been populated yet.

Scope key semantics:
  * ``bank_charter`` — coarse charter class (``national``, ``state_member``,
    ``state_nonmember``, ``credit_union``, ``savings``, ``holding``).
    Deliberately kept broader than the OCC/FDIC/FRB regulator split so
    community banks with dual-regulator overlap share a template.
  * ``asset_tier`` — regulator-derived size band (``community`` <$10B,
    ``regional`` $10B–$50B, ``midsize`` $50B–$250B, ``large`` $250B–$1T,
    ``gsib`` ≥$1T). Matches ``EnforcementAction.asset_tier`` vocabulary.
  * ``(bank_charter, asset_tier, cfr_citation, obligation_key)`` is the
    natural key — a bank scope can legitimately reference the same CFR
    citation for two distinct obligations (e.g. a single 12 CFR § 1005
    section carries multiple named obligations).

Per ``feedback_p1_alembic_blocks_rollback`` rollback to a pre-043 image
requires ``alembic stamp 042`` first — nothing else references this
table yet so the drop is a clean single-op downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "applicable_obligation_registry",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("bank_charter",   sa.String(30),  nullable=False),
        sa.Column("asset_tier",     sa.String(20),  nullable=False),
        sa.Column("cfr_citation",   sa.String(200), nullable=False),
        sa.Column("regulator",      sa.String(50),  nullable=False),
        sa.Column("obligation_key", sa.String(200), nullable=False),
        sa.Column("description",    sa.Text,        nullable=True),
        sa.Column("is_required",    sa.Boolean,     nullable=False,
                  server_default=sa.text("TRUE")),
        sa.Column("source",         sa.String(60),  nullable=False),
        sa.Column("effective_from", sa.Date,        nullable=True),
        sa.Column("effective_to",   sa.Date,        nullable=True),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.UniqueConstraint(
            "bank_charter", "asset_tier", "cfr_citation", "obligation_key",
            name="uq_registry_scope_citation_key",
        ),
    )

    # Hot read path is "required rows for (charter, tier) as of today" —
    # covers the denominator query in dashboard_api.py without a seq scan.
    op.create_index(
        "idx_registry_scope_required",
        "applicable_obligation_registry",
        ["bank_charter", "asset_tier", "is_required"],
    )
    # CFR-citation lookup for the matched-numerator join. Registry is
    # denormalised on citation so a canonical citation is safe as a key.
    op.create_index(
        "idx_registry_cfr_citation",
        "applicable_obligation_registry",
        ["cfr_citation"],
    )


def downgrade() -> None:
    op.drop_index("idx_registry_cfr_citation",
                  table_name="applicable_obligation_registry")
    op.drop_index("idx_registry_scope_required",
                  table_name="applicable_obligation_registry")
    op.drop_table("applicable_obligation_registry")
