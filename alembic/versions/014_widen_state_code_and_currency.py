"""Widen state_code to VARCHAR(10) and threshold_currency to VARCHAR(10)."""

from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "compliance_rules",
        "state_code",
        existing_type=sa.String(2),
        type_=sa.String(10),
        existing_nullable=True,
    )
    op.alter_column(
        "compliance_rules",
        "threshold_currency",
        existing_type=sa.String(3),
        type_=sa.String(10),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "compliance_rules",
        "state_code",
        existing_type=sa.String(10),
        type_=sa.String(2),
        existing_nullable=True,
    )
    op.alter_column(
        "compliance_rules",
        "threshold_currency",
        existing_type=sa.String(10),
        type_=sa.String(3),
        existing_nullable=True,
    )
