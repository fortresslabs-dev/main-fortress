"""Add obligations and coverage_snapshots tables (REQ-010)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "obligations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("obligation_id", sa.String(200), nullable=False, unique=True),
        sa.Column("regulation_id", sa.String(100), nullable=False),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("source_text", sa.Text, nullable=False),
        sa.Column("obligation_type", sa.String(20), nullable=False),
        sa.Column("section_reference", sa.Text, nullable=True),
        sa.Column("cfr_citation", sa.Text, nullable=True),
        sa.Column("doc_id", sa.String(100), nullable=True),
        sa.Column("has_test_coverage", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("coverage_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("test_scenario_ids", JSONB, server_default=sa.text("'[]'")),
        sa.Column("extracted_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.CheckConstraint(
            "obligation_type IN ('shall','must','prohibited','required','may_not')",
            name="ck_obligation_type",
        ),
    )
    op.create_index("idx_obligations_regulation", "obligations", ["regulation_id"],
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_obligations_regulator", "obligations", ["regulator", "category"],
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_obligations_coverage", "obligations", ["has_test_coverage", "regulator"],
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_obligations_cfr", "obligations", ["cfr_citation"],
                    postgresql_where=sa.text("cfr_citation IS NOT NULL"))

    op.create_table(
        "coverage_snapshots",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("total_obligations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("covered_obligations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("coverage_pct", sa.Float, nullable=False, server_default="0.0"),
        sa.UniqueConstraint("snapshot_date", "regulator", "category", name="uq_snapshot_date_reg_cat"),
    )
    op.create_index("idx_snapshots_date_reg", "coverage_snapshots", ["snapshot_date", "regulator"])


def downgrade():
    op.drop_index("idx_snapshots_date_reg", "coverage_snapshots")
    op.drop_table("coverage_snapshots")
    op.drop_index("idx_obligations_cfr", "obligations")
    op.drop_index("idx_obligations_coverage", "obligations")
    op.drop_index("idx_obligations_regulator", "obligations")
    op.drop_index("idx_obligations_regulation", "obligations")
    op.drop_table("obligations")
