"""Add peer_benchmarks table (REQ-020)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "peer_benchmarks",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("regulation_id", sa.String(100), nullable=False),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("asset_tier", sa.String(20), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("avg_coverage_pct", sa.Float, nullable=True),
        sa.Column("median_coverage_pct", sa.Float, nullable=True),
        sa.Column("top_quartile_pct", sa.Float, nullable=True),
        sa.Column("bottom_quartile_pct", sa.Float, nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=True),
        sa.Column("data_period", sa.Date, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("regulation_id", "asset_tier", "source", "data_period",
                            name="uq_benchmark_reg_tier_source_period"),
        sa.CheckConstraint(
            "asset_tier IN ('under_1B','1B_10B','10B_100B','over_100B')",
            name="ck_asset_tier",
        ),
        sa.CheckConstraint(
            "source IN ('public_ffiec','public_fdic','public_occ','network_opt_in')",
            name="ck_benchmark_source",
        ),
    )
    op.create_index("idx_benchmarks_reg", "peer_benchmarks", ["regulation_id", "asset_tier"])


def downgrade():
    op.drop_index("idx_benchmarks_reg", "peer_benchmarks")
    op.drop_table("peer_benchmarks")
