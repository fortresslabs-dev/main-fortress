"""Add enrichment columns to compliance_rules (cfr_citation, state_code, etc.)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("compliance_rules", sa.Column("cfr_citation", sa.Text, nullable=True))
    op.add_column("compliance_rules", sa.Column("state_code", sa.String(2), nullable=True))
    op.add_column("compliance_rules", sa.Column("jurisdiction_level", sa.String(10), nullable=True))
    op.add_column("compliance_rules", sa.Column("enforcement_risk_score", sa.Float, nullable=True))
    op.add_column("compliance_rules", sa.Column("previous_content", sa.Text, nullable=True))
    op.add_column("compliance_rules", sa.Column("change_delta", JSONB, nullable=True))
    op.add_column("compliance_rules", sa.Column("changed_obligations", JSONB, nullable=True))
    op.add_column("compliance_rules", sa.Column("change_type", sa.String(20), nullable=True))
    op.add_column("compliance_rules", sa.Column("obligation_count", sa.Integer, nullable=True,
                                                 server_default="0"))

    op.create_index(
        "idx_rules_state_code", "compliance_rules", ["state_code"],
        postgresql_where=sa.text("state_code IS NOT NULL"),
    )
    op.create_index(
        "idx_rules_enforcement_score", "compliance_rules", ["enforcement_risk_score"],
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade():
    op.drop_index("idx_rules_enforcement_score", table_name="compliance_rules")
    op.drop_index("idx_rules_state_code", table_name="compliance_rules")
    op.drop_column("compliance_rules", "obligation_count")
    op.drop_column("compliance_rules", "change_type")
    op.drop_column("compliance_rules", "changed_obligations")
    op.drop_column("compliance_rules", "change_delta")
    op.drop_column("compliance_rules", "previous_content")
    op.drop_column("compliance_rules", "enforcement_risk_score")
    op.drop_column("compliance_rules", "jurisdiction_level")
    op.drop_column("compliance_rules", "state_code")
    op.drop_column("compliance_rules", "cfr_citation")
