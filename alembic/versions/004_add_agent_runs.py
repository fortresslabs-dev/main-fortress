"""Add agent_runs table for LangGraph compliance agent tracking.

Revision ID: 004
Revises: 003_add_compliance_tables
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "004_add_agent_runs"
down_revision = "003_add_compliance_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 003 created compliance_requirements with a different schema (int PK,
    # regulation_id FK). Drop it so we can recreate with the correct UUID schema.
    # Using IF EXISTS CASCADE handles both fresh installs and upgrades safely.
    op.execute("DROP TABLE IF EXISTS compliance_requirements CASCADE")

    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", sa.String(100), nullable=False, index=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("regulator", sa.String(50), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("success", sa.Boolean, nullable=True),
        sa.Column("records_extracted", sa.Integer, nullable=True),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("iterations_used", sa.Integer, nullable=True, server_default="0"),
        sa.Column("max_iterations", sa.Integer, nullable=True, server_default="5"),
        sa.Column("errors", JSONB, nullable=True),
        sa.Column("validation_result", JSONB, nullable=True),
        sa.Column("scraper_file_path", sa.Text, nullable=True),
        sa.Column("execution_time_seconds", sa.Float, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "compliance_requirements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=False, index=True),
        sa.Column("requirement_id", sa.String(200), nullable=False),
        sa.Column("requirement_text", sa.Text, nullable=True),
        sa.Column("rule_type", sa.String(100), nullable=True),
        sa.Column("regulator", sa.String(50), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("threshold_amount", sa.Float, nullable=True),
        sa.Column("threshold_currency", sa.String(10), nullable=True),
        sa.Column("extra_fields", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_compliance_requirements_source_req", "compliance_requirements", ["source_id", "requirement_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_compliance_requirements_source_req", table_name="compliance_requirements")
    op.drop_table("compliance_requirements")
    op.drop_table("agent_runs")
