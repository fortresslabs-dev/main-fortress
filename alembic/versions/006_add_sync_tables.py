"""Add sync tables for Option 3 master-replica architecture.

Revision ID: 006
Revises: 005
Create Date: 2026-04-03

Adds:
  - hash & version columns to compliance_rules
  - sync_versions (global version counter)
  - deleted_rules (tombstone table)
  - sync_metadata (per-customer tracking — Pipeline 1)
  - local_sync_status (local sync log — Pipeline 2)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add columns to compliance_rules ─────────────────────────────
    op.add_column("compliance_rules", sa.Column("hash", sa.String(64), nullable=True))
    op.add_column("compliance_rules", sa.Column("version", sa.BigInteger(), nullable=True))
    op.create_index("ix_rules_version", "compliance_rules", ["version"])

    # ── Global sync version counter (Pipeline 1) ────────────────────
    op.create_table(
        "sync_versions",
        sa.Column("id", sa.Integer(), primary_key=True, default=1),
        sa.Column("current_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    # Seed the singleton row
    op.execute("INSERT INTO sync_versions (id, current_version) VALUES (1, 0)")

    # ── Deleted rules tombstone (Pipeline 1) ────────────────────────
    op.create_table(
        "deleted_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_id", sa.String(100), nullable=False, index=True),
        sa.Column("regulator", sa.String(50), nullable=True),
        sa.Column("geography", sa.String(10), nullable=True),
        sa.Column("deleted_at_version", sa.BigInteger(), nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Per-customer sync metadata (Pipeline 1) ────────────────────
    op.create_table(
        "sync_metadata",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("customer_id", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("last_sync_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_sync_timestamp", sa.DateTime(), nullable=True),
        sa.Column("sync_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Local sync status log (Pipeline 2) ──────────────────────────
    op.create_table(
        "local_sync_status",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sync_version", sa.BigInteger(), nullable=False),
        sa.Column("sync_timestamp", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("rules_added", sa.Integer(), server_default="0"),
        sa.Column("rules_updated", sa.Integer(), server_default="0"),
        sa.Column("rules_deleted", sa.Integer(), server_default="0"),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="SUCCESS"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sync_type", sa.String(20), nullable=False, server_default="incremental"),
    )


def downgrade() -> None:
    op.drop_table("local_sync_status")
    op.drop_table("sync_metadata")
    op.drop_table("deleted_rules")
    op.drop_table("sync_versions")
    op.drop_index("ix_rules_version", table_name="compliance_rules")
    op.drop_column("compliance_rules", "version")
    op.drop_column("compliance_rules", "hash")
