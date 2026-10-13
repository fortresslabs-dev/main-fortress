"""Add authority_type + citation-verification + provenance to compliance_rules.

Revision ID: 046
Revises: 045
Create Date: 2026-07-11

The class of defect this migration prevents is documented in
``docs/correction_smb.md`` (§1, §11): every fabricated obligation in the
prior corpus (OCC-BSA-WIRE-001 dual approval, OFAC-SDN-DAILY-001 24h
SLA, OCC-BSA-CDD-001 April-2026 amendment) shares one property — they
were emitted with ``authority_type = LAW`` and a made-up CFR citation.

The fix is a schema-level invariant. Every rule now declares which of
four authority types it belongs to::

    LAW              — codified regulation (CFR / NYCRR / state code)
    STATUTE          — codified statute (USC / state statute)
    GUIDANCE         — supervisory manual, bulletin, FFIEC advisory
    INTERNAL_CONTROL — bank internal policy or Fortress SLA

Three CHECK constraints enforce the class boundary at ingest:

  * ``ck_cr_law_requires_citation`` — LAW rows must carry a citation.
    Prevents "we asserted a legal obligation with no source".

  * ``ck_cr_guidance_forbids_citation`` — GUIDANCE / INTERNAL_CONTROL
    rows must NOT carry a CFR-style citation. Prevents the Test
    Factory from generating a pytest that cites an internal control
    as though it were codified law.

  * ``ck_cr_verified_implies_law`` — only LAW rows can be marked
    ``citation_verified = TRUE``. The hard gate (may_assert_as_law
    helper) reads this pair; nothing else can pass the gate.

The ``verify_before_display`` flag surfaces "WATCH" items in the UI
(FFIEC CAT sunset, CFPB 1071 litigation dates, NYDFS 500/504 numbering
inconsistency). ``source_content_hash`` generalises the OFAC pattern
(archive raw XML + hash) to every scraped rule.

Applicability columns (asset_min / asset_max / charter / activities /
states) unblock the per-tenant join that turns the corpus into
"applicable to *this* bank" — the join that lets the tenant_rule_state
disposition row clear pending items instead of leaving them forever
open.

New columns are all nullable + default NULL so existing rows survive
the upgrade. The check constraints are added with ``NOT VALID`` first
so the migration is fast, then validated in a separate pass; each
connector run subsequently backfills its own rows with a non-null
``authority_type`` (see ``occ_bsa.py``, ``fincen_sar.py``,
``ofac_sdn.py``, ``fdic.py``, ``ffiec_bsa.py``,
``ffiec_cybersecurity.py``, ``irr.py``, ``cre_concentration.py``).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


_AUTHORITY_TYPES = ("LAW", "STATUTE", "GUIDANCE", "INTERNAL_CONTROL")


def upgrade() -> None:
    # ── Authority + provenance columns ────────────────────────────
    op.add_column("compliance_rules", sa.Column("authority_type", sa.String(20), nullable=True))
    op.add_column("compliance_rules", sa.Column("authority_source", sa.Text, nullable=True))
    op.add_column("compliance_rules", sa.Column("citation_verified", sa.Boolean, nullable=False, server_default=sa.text("FALSE")))
    op.add_column("compliance_rules", sa.Column("citation_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compliance_rules", sa.Column("source_content_hash", sa.String(64), nullable=True))
    op.add_column("compliance_rules", sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True))

    # ── Rulemaking lifecycle (populated from FR connector when landed) ──
    op.add_column("compliance_rules", sa.Column("publication_date", sa.Date, nullable=True))
    op.add_column("compliance_rules", sa.Column("comment_deadline", sa.Date, nullable=True))
    op.add_column("compliance_rules", sa.Column("federal_register_doc", sa.Text, nullable=True))
    op.add_column("compliance_rules", sa.Column("rin", sa.String(40), nullable=True))

    # ── Applicability (community-bank join) ──────────────────────────
    op.add_column("compliance_rules", sa.Column("applicability_asset_min", sa.Numeric, nullable=True))
    op.add_column("compliance_rules", sa.Column("applicability_asset_max", sa.Numeric, nullable=True))
    op.add_column("compliance_rules", sa.Column("applicability_charter", postgresql.ARRAY(sa.Text), nullable=True))
    op.add_column("compliance_rules", sa.Column("applicability_activities", postgresql.ARRAY(sa.Text), nullable=True))
    op.add_column("compliance_rules", sa.Column("applicability_states", postgresql.ARRAY(sa.Text), nullable=True))

    # ── Flags ────────────────────────────────────────────────────────
    op.add_column("compliance_rules", sa.Column("is_exemption", sa.Boolean, nullable=False, server_default=sa.text("FALSE")))
    op.add_column("compliance_rules", sa.Column("verify_before_display", sa.Boolean, nullable=False, server_default=sa.text("FALSE")))
    op.add_column("compliance_rules", sa.Column("bank_configurable_threshold", sa.Boolean, nullable=False, server_default=sa.text("FALSE")))

    # ── CHECK constraints — added NOT VALID for a fast migration. Any
    # existing row with a bad shape must be backfilled by the connector
    # runs before ``VALIDATE CONSTRAINT`` is invoked.
    types_sql = ", ".join(f"'{t}'" for t in _AUTHORITY_TYPES)
    op.execute(f"""
        ALTER TABLE compliance_rules
          ADD CONSTRAINT ck_cr_authority_type_domain
          CHECK (authority_type IS NULL OR authority_type IN ({types_sql}))
          NOT VALID;
    """)
    op.execute("""
        ALTER TABLE compliance_rules
          ADD CONSTRAINT ck_cr_law_requires_citation
          CHECK (authority_type IS NULL
                 OR authority_type <> 'LAW'
                 OR cfr_citation IS NOT NULL)
          NOT VALID;
    """)
    op.execute("""
        ALTER TABLE compliance_rules
          ADD CONSTRAINT ck_cr_guidance_forbids_citation
          CHECK (authority_type IS NULL
                 OR authority_type NOT IN ('GUIDANCE', 'INTERNAL_CONTROL')
                 OR cfr_citation IS NULL)
          NOT VALID;
    """)
    op.execute("""
        ALTER TABLE compliance_rules
          ADD CONSTRAINT ck_cr_verified_implies_law
          CHECK (NOT citation_verified OR authority_type = 'LAW')
          NOT VALID;
    """)

    # Hot read path — the resolver / hard-gate query filters on
    # ``authority_type = 'LAW' AND citation_verified = TRUE``. Predicate
    # index keeps the gate cheap even as the corpus grows.
    op.create_index(
        "idx_cr_gateable",
        "compliance_rules",
        ["authority_type", "citation_verified"],
        postgresql_where=sa.text("authority_type = 'LAW' AND citation_verified = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_cr_gateable", table_name="compliance_rules")
    for name in (
        "ck_cr_verified_implies_law",
        "ck_cr_guidance_forbids_citation",
        "ck_cr_law_requires_citation",
        "ck_cr_authority_type_domain",
    ):
        op.execute(f"ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS {name};")
    for col in (
        "bank_configurable_threshold",
        "verify_before_display",
        "is_exemption",
        "applicability_states",
        "applicability_activities",
        "applicability_charter",
        "applicability_asset_max",
        "applicability_asset_min",
        "rin",
        "federal_register_doc",
        "comment_deadline",
        "publication_date",
        "scraped_at",
        "source_content_hash",
        "citation_verified_at",
        "citation_verified",
        "authority_source",
        "authority_type",
    ):
        op.drop_column("compliance_rules", col)
