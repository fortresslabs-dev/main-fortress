"""Add ofac_list_versions for OFAC freshness anchor.

Revision ID: 038
Revises: 037
Create Date: 2026-06-21

One row per (list_name, version_id) — version_id comes from the
``<Publish_Date>`` / ``<Version>`` elements inside SDN_ADVANCED.XML when
present, falling back to the source URL's ``Last-Modified`` header. The
SHA-256 of the raw XML is the tamper-evident anchor surfaced by the
ofac_attestation export (design doc §5.1, Q15).

Additive change — no existing rows touched. Per
``feedback_p1_alembic_blocks_rollback`` rolling back to a pre-038 image
requires stamping Aurora down first:

    python -m alembic stamp 037
    # then deploy old image / task def

Until the corresponding loader change (record_list_version) lands, the
table stays empty; downstream consumers must handle the no-row case.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ofac_list_versions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # 'sdn' or 'cons' — Specially Designated Nationals vs Consolidated Sanctions.
        # Short string is fine; CHECK constraint enforces vocab at DB level so a
        # typo in the loader can't silently shadow real data.
        sa.Column("list_name", sa.String(16), nullable=False),
        # Publish_Date attribute on SDN_ADVANCED.XML root, or Last-Modified
        # header from the source URL when the XML omits it. Stored as text
        # (not TIMESTAMPTZ) because OFAC's format isn't guaranteed parseable
        # and the raw value is what we want to attest to.
        sa.Column("version_id", sa.String(64), nullable=False),
        # Parsed publish timestamp — best-effort. NULL when we couldn't parse
        # version_id into a datetime.
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("source_url", sa.Text, nullable=False),
        # Hex-encoded SHA-256 of the raw XML bytes — 64 chars.
        sa.Column("sha256_of_xml", sa.String(64), nullable=False),
        # FK is nullable: loader may run outside the scrape_runs lifecycle
        # (e.g. one-off backfill) and we still want the version row.
        sa.Column("scrape_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("scrape_runs.id", ondelete="SET NULL"),
                  nullable=True),
        sa.UniqueConstraint("list_name", "version_id",
                            name="uq_ofac_list_versions_list_version"),
        sa.CheckConstraint("list_name IN ('sdn', 'cons')",
                           name="ck_ofac_list_versions_list_name"),
        sa.CheckConstraint("char_length(sha256_of_xml) = 64",
                           name="ck_ofac_list_versions_sha256_len"),
    )

    # Hot path is "latest version of list X" — index supports the ORDER BY
    # ingested_at DESC LIMIT 1 used by the freshness anchor reader.
    op.create_index(
        "ix_ofac_list_versions_list_ingested",
        "ofac_list_versions",
        ["list_name", sa.text("ingested_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_ofac_list_versions_list_ingested",
                  table_name="ofac_list_versions")
    op.drop_table("ofac_list_versions")
