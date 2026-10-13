"""Add enforcement_actions and enforcement_risk_scores tables (REQ-011)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "enforcement_actions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("action_ref", sa.String(200), nullable=False, unique=True),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("institution_type", sa.String(50), nullable=True),
        sa.Column("asset_tier", sa.String(20), nullable=True),
        sa.Column("action_date", sa.Date, nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("regulations_cited", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("violation_summary", sa.Text, nullable=True),
        sa.Column("penalty_amount_usd", sa.Numeric(15, 2), nullable=True),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("scraped_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "action_type IN ('consent_order','formal_agreement','civil_money_penalty',"
            "'mra','mraia','cease_and_desist','written_agreement')",
            name="ck_enforcement_action_type",
        ),
    )
    op.create_index("idx_enforcement_reg_date", "enforcement_actions", ["regulator", "action_date"])
    op.create_index("idx_enforcement_type", "enforcement_actions", ["action_type", "action_date"])
    op.create_index("idx_enforcement_regs_gin", "enforcement_actions", ["regulations_cited"],
                    postgresql_using="gin")

    op.create_table(
        "enforcement_risk_scores",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("regulation_id", sa.String(100), nullable=False),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("period_months", sa.Integer, nullable=False, server_default="24"),
        sa.Column("action_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_penalty_usd", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_penalty_usd", sa.Numeric(15, 2), nullable=True),
        sa.Column("trend_direction", sa.String(15), nullable=True),
        sa.Column("risk_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("regulation_id", "period_months", name="uq_risk_score_reg_period"),
        sa.CheckConstraint(
            "trend_direction IN ('increasing','stable','decreasing')",
            name="ck_trend_direction",
        ),
    )
    op.create_index("idx_risk_scores_reg", "enforcement_risk_scores", ["regulation_id"])
    op.create_index("idx_risk_scores_score", "enforcement_risk_scores", ["risk_score"])


def downgrade():
    op.drop_index("idx_risk_scores_score", "enforcement_risk_scores")
    op.drop_index("idx_risk_scores_reg", "enforcement_risk_scores")
    op.drop_table("enforcement_risk_scores")
    op.drop_index("idx_enforcement_regs_gin", "enforcement_actions")
    op.drop_index("idx_enforcement_type", "enforcement_actions")
    op.drop_index("idx_enforcement_reg_date", "enforcement_actions")
    op.drop_table("enforcement_actions")
