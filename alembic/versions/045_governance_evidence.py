"""Add governance_evidence — bank-scoped canonical governance artifact store.

Revision ID: 045
Revises: 044
Create Date: 2026-07-05

Ships the schema for the P7 governance-evidence fix called out in the
readiness audit (``docs/examination_readiness_parameter_audit.md``
§P7). Pre-R12 the ``governance`` dim query was::

    pct(rules_with_risk, rules_total, 80)

which reused the *ra_currency* numerator (`rules_with_risk`) with a
different default — two dimensions moving in lockstep on the same
signal, both mislabeled. The audit's diagnosis:

> Reuses the risk-score coverage query. Governance evidence and
> risk-assessment currency are different concepts (board minutes,
> committee charters, BSA officer designation, training records vs.
> up-to-date risk scores) but share a query. Ship a board/committee
> artifact store and split the widget.

This migration ships the *schema* (empty at rollout). The resolver
in ``src/api/dashboard_api.py`` reads the table at request time and,
when the caller's bank has one or more live evidence rows, uses the
count of distinct canonical evidence types as the numerator (of
``_CANONICAL_GOVERNANCE_TYPES = 8``). Otherwise the widget label
degrades to "Risk-attribution proxy" — honest for the pre-registry
query the widget still runs.

Canonical evidence types (examiner-driven — the artifacts a BSA
examiner asks for on day one):
  * ``board_bsa_acknowledgement`` — annual board resolution
    acknowledging BSA program status.
  * ``board_ofac_acknowledgement`` — annual board acknowledgement of
    OFAC sanctions program.
  * ``bsa_officer_designation`` — BSA officer appointment letter.
  * ``bsa_committee_charter`` — BSA/AML compliance committee charter.
  * ``bsa_committee_minutes`` — recurring committee minutes
    (measured as "current-quarter minutes on file").
  * ``bsa_training_records`` — annual training completion (bank-wide).
  * ``bsa_policy_manual`` — current BSA policy manual.
  * ``enterprise_risk_assessment`` — signed enterprise risk
    assessment.

Table shape mirrors R11's ``control_obligation_mappings``:
  * ``bank_id`` — bank-scoped, hard requirement.
  * ``evidence_type`` — CHECK-constrained to the canonical set.
  * ``artifact_ref`` (VARCHAR NULL) — pointer to the underlying doc
    (S3 URI, doc-id, URL). Optional so ops can bulk-seed and
    backfill.
  * ``source`` / ``attested_by`` — audit-trail fields.
  * ``effective_from`` / ``effective_to`` — mid-year rotation
    (an annual training record superseded by the next year's).
  * ``superseded_at`` — soft-delete for a re-issued evidence row.

Uniqueness:
  * Partial UNIQUE on ``(bank_id, evidence_type) WHERE
    superseded_at IS NULL`` — at most one *live* row per (bank,
    type). Superseded rows can accumulate freely.
  * A hard UNIQUE would prevent re-issuing an artifact of the same
    type without hard-deleting the prior row, which loses the audit
    trail — same failure mode R11 avoided with its partial index.

Per ``feedback_p1_alembic_blocks_rollback`` rollback to a pre-045
image requires ``alembic stamp 044`` first. Nothing else references
this table yet so the drop is a clean single-op downgrade.
"""
from alembic import op
import sqlalchemy as sa


revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


_CANONICAL_TYPES = (
    "board_bsa_acknowledgement",
    "board_ofac_acknowledgement",
    "bsa_officer_designation",
    "bsa_committee_charter",
    "bsa_committee_minutes",
    "bsa_training_records",
    "bsa_policy_manual",
    "enterprise_risk_assessment",
)


def upgrade() -> None:
    check_expr = "evidence_type IN (" + ", ".join(
        f"'{t}'" for t in _CANONICAL_TYPES
    ) + ")"

    op.create_table(
        "governance_evidence",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("bank_id",        sa.String(80),  nullable=False),
        sa.Column("evidence_type",  sa.String(60),  nullable=False),
        sa.Column("artifact_ref",   sa.String(500), nullable=True),
        sa.Column("source",         sa.String(120), nullable=True),
        sa.Column("attested_by",    sa.String(120), nullable=True),
        sa.Column("notes",          sa.Text,        nullable=True),
        sa.Column("effective_from", sa.Date,        nullable=True),
        sa.Column("effective_to",   sa.Date,        nullable=True),
        sa.Column("superseded_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",     sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(check_expr, name="ck_ge_evidence_type"),
    )

    # Partial UNIQUE — at most one live row per (bank, type). Superseded
    # rows can accumulate freely as an audit trail of prior artifacts.
    op.create_index(
        "uq_ge_bank_type_live",
        "governance_evidence",
        ["bank_id", "evidence_type"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # Hot read path — resolver counts distinct evidence types per bank
    # where the row is live and in effective window.
    op.create_index(
        "idx_ge_bank_type_live",
        "governance_evidence",
        ["bank_id", "evidence_type"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_ge_bank_type_live", table_name="governance_evidence")
    op.drop_index("uq_ge_bank_type_live",  table_name="governance_evidence")
    op.drop_table("governance_evidence")
