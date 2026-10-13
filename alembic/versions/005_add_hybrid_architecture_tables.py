"""Add compliance rules, OFAC SDN, validation audit tables for hybrid architecture

Revision ID: 005
Revises: 004_add_agent_runs
Create Date: 2025-01-01 00:00:00.000000

Pipeline 1 tables: compliance_rules, ofac_sdn_entries, compliance_rule_changes
Pipeline 2 tables: transaction_validations, ofac_screenings
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "005"
down_revision = "004_add_agent_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy ofac_sdn_entries from migration 003 (different schema) before recreating
    op.execute("DROP TABLE IF EXISTS ofac_sdn_entries CASCADE")

    # ── Pipeline 1: Compliance Rules (Fortress Cloud) ───────────────

    op.create_table(
        "compliance_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_id", sa.String(100), nullable=False, index=True),
        sa.Column("regulator", sa.String(50), nullable=False, index=True),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("geography", sa.String(10), nullable=False, server_default="usa"),
        sa.Column("domain", sa.String(100), nullable=True),
        sa.Column("rule_type", sa.String(50), nullable=True),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("threshold_amount", sa.Float(), nullable=True),
        sa.Column("threshold_currency", sa.String(3), nullable=True, server_default="USD"),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("effective_date", sa.DateTime(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("test_template", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("rule_id", "regulator", "geography", name="uq_rule_regulator_geo"),
    )
    op.create_index("ix_rules_geo_reg_domain", "compliance_rules", ["geography", "regulator", "domain"])

    op.create_table(
        "ofac_sdn_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sdn_number", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("entity_name", sa.String(500), nullable=False, index=True),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("programs", sa.JSON(), server_default="[]"),
        sa.Column("aliases", sa.JSON(), server_default="[]"),
        sa.Column("addresses", sa.JSON(), server_default="[]"),
        sa.Column("id_numbers", sa.JSON(), server_default="[]"),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "compliance_rule_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_id", sa.String(100), nullable=False, index=True),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.func.now(), index=True),
    )

    # ── Pipeline 2: Validation Audit Logs (Customer Docker) ────────

    op.create_table(
        "transaction_validations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("validation_type", sa.String(50), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("violations_count", sa.Integer(), server_default="0"),
        sa.Column("violations", sa.JSON(), server_default="[]"),
        sa.Column("request_metadata", sa.JSON(), server_default="{}"),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_validations_type_action", "transaction_validations", ["validation_type", "action"])

    op.create_table(
        "ofac_screenings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("match_count", sa.Integer(), server_default="0"),
        sa.Column("matched_entries", sa.JSON(), server_default="[]"),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ofac_screenings")
    op.drop_table("transaction_validations")
    op.drop_table("compliance_rule_changes")
    op.drop_table("ofac_sdn_entries")
    op.drop_index("ix_rules_geo_reg_domain", table_name="compliance_rules")
    op.drop_table("compliance_rules")
