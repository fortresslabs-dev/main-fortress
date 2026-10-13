"""Add control_obligation_mappings — bank-scoped obligation↔control link.

Revision ID: 044
Revises: 043
Create Date: 2026-07-05

Ships the schema for the P2 controls fix called out in the readiness
audit (``docs/examination_readiness_parameter_audit.md`` §P2). Pre-R11
the ``control_mapping`` dim query was::

    pct(
      COUNT(obligations WHERE regulator IS NOT NULL AND regulator<>'unknown'),
      COUNT(obligations WHERE is_active=TRUE),
      70,
    )

which measured *attribution* (does the obligation carry a regulator
label?) — not *control coverage*. R8 already renamed the widget label
to "Obligation attribution" so the read matched the query. R11 gives
the widget a real join through which it can re-earn the "Control
mapping" label — for every bank whose ops has authored control ↔
obligation links.

Design choice — dedicated table vs FK on ``ControlRuleMapping``:
    ``ControlRuleMapping`` joins controls to rules (regulation-level,
    ingested from scraping). Obligations are the sentence-level
    extractions *inside* those rules, so a control might satisfy some
    obligations of a rule and not others. Adding an ``obligation_id``
    column to ``ControlRuleMapping`` would turn that table into a
    polymorphic mapping (either→rule or either→obligation) with a
    nullable disambiguator — hard to query, hard to constrain. A
    dedicated ``control_obligation_mappings`` table keeps each join's
    semantics clean and lets the two tables evolve independently
    (e.g. R11 gets ``effective_from`` / ``rationale`` fields that the
    rule mapping doesn't need).

Scope + audit shape:
  * ``bank_id`` — mappings are bank-authored (control-owner writes
    them), so scoping is a hard requirement. Matches the pattern
    already in ``ControlRuleMapping.bank_id``.
  * ``mapping_strength`` — reuses the ``full`` / ``partial`` /
    ``related`` vocabulary from ``ControlRuleMapping`` so the frontend
    can render both mappings with the same colour ramp.
  * ``rationale`` (TEXT NULL) — free-text audit evidence for *why*
    this control satisfies this obligation. Examiners will ask.
  * ``effective_from`` / ``effective_to`` — mid-year rule changes can
    make a mapping stop applying without a delete. Windowing matches
    the R10 registry so the two data sources share a temporal
    contract.
  * ``superseded_at`` — soft-delete for a superseded mapping (matches
    ``ControlRuleMapping.superseded_at``). Preserves the audit trail
    of the *previous* mapping instead of hard-deleting.
  * ``(bank_id, control_id, obligation_id)`` unique — one live
    mapping per triple; a re-mapping ``supersedes`` the old row and
    inserts a new one.

Per ``feedback_p1_alembic_blocks_rollback`` rollback to a pre-044
image requires ``alembic stamp 043`` first. Nothing else references
this table yet so the drop is a clean single-op downgrade.
"""
from alembic import op
import sqlalchemy as sa


revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "control_obligation_mappings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bank_id",        sa.String(80),  nullable=False),
        sa.Column("control_id",     sa.String(80),  nullable=False),
        sa.Column("obligation_id",  sa.String(200), nullable=False),
        sa.Column("mapping_strength", sa.String(10), nullable=False,
                  server_default=sa.text("'full'")),
        sa.Column("rationale",      sa.Text,        nullable=True),
        sa.Column("created_by",     sa.String(120), nullable=True),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("effective_from", sa.Date,        nullable=True),
        sa.Column("effective_to",   sa.Date,        nullable=True),
        sa.Column("superseded_at",  sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["control_id"], ["controls.control_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["obligation_id"], ["obligations.obligation_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "bank_id", "control_id", "obligation_id",
            name="uq_com_bank_control_obligation",
        ),
        sa.CheckConstraint(
            "mapping_strength IN ('full','partial','related')",
            name="ck_com_mapping_strength",
        ),
    )

    # Hot read path — the resolver counts distinct obligation_ids per
    # bank where the mapping is live (not superseded, not out of
    # effective window). Composite covers the WHERE + GROUP BY.
    op.create_index(
        "idx_com_bank_obligation_live",
        "control_obligation_mappings",
        ["bank_id", "obligation_id"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # Ownership drilldown — "which controls does this bank map to
    # which obligations?" — a natural CCO follow-up from the widget.
    op.create_index(
        "idx_com_bank_control",
        "control_obligation_mappings",
        ["bank_id", "control_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_com_bank_control",
                  table_name="control_obligation_mappings")
    op.drop_index("idx_com_bank_obligation_live",
                  table_name="control_obligation_mappings")
    op.drop_table("control_obligation_mappings")
