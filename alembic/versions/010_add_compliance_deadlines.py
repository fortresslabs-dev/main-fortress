"""Add compliance_deadlines table with seeded upcoming deadlines (REQ-012)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "compliance_deadlines",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("deadline_key", sa.String(200), nullable=False, unique=True),
        sa.Column("regulation_id", sa.String(100), nullable=True),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("deadline_type", sa.String(30), nullable=False),
        sa.Column("deadline_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("deadline_date", sa.Date, nullable=False),
        sa.Column("days_remaining", sa.Integer, nullable=True),
        sa.Column("has_test_coverage", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("test_coverage_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("obligation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("uncovered_obligation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("alert_thresholds_sent", JSONB, server_default=sa.text("'{}'")),
        sa.Column("is_recurring", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("recurrence_rule", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "deadline_type IN ('effective_date','filing_deadline','reporting_deadline','review_deadline')",
            name="ck_deadline_type",
        ),
    )
    op.create_index("idx_deadlines_date", "compliance_deadlines", ["deadline_date"],
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_deadlines_days_rem", "compliance_deadlines", ["days_remaining"],
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_deadlines_uncovered", "compliance_deadlines", ["has_test_coverage", "deadline_date"],
                    postgresql_where=sa.text("is_active = TRUE"))

    # Seed known upcoming deadlines
    op.execute("""
        INSERT INTO compliance_deadlines
            (deadline_key, regulator, deadline_type, deadline_name, deadline_date)
        VALUES
            ('PCI-DSS-V4-CUTOVER-2026',   'PCI',   'effective_date',   'PCI-DSS v4.0 full compliance',    '2026-04-29'),
            ('CFPB-1071-TIER1-2026',      'CFPB',  'effective_date',   'Section 1071 Tier-1 LAR',          '2026-07-01'),
            ('FFIEC-CAT-ANNUAL-2026',     'FFIEC', 'review_deadline',  'FFIEC CAT self-assessment',        '2026-06-30'),
            ('SOX-404-Q2-2026',           'SEC',   'filing_deadline',  'SOX §404 Q2 attestation',          '2026-06-28'),
            ('FDIC-363-AUDIT-2026',       'FDIC',  'review_deadline',  'FDIC Part 363 audit',              '2026-08-15'),
            ('CALL-REPORT-Q2-2026',       'FFIEC', 'filing_deadline',  'CALL Report Q2',                   '2026-07-30'),
            ('CALL-REPORT-Q3-2026',       'FFIEC', 'filing_deadline',  'CALL Report Q3',                   '2026-10-30')
        ON CONFLICT DO NOTHING;
    """)

    # Initialise days_remaining for seeded rows
    op.execute("""
        UPDATE compliance_deadlines
        SET days_remaining = (deadline_date - CURRENT_DATE)
        WHERE is_active = TRUE AND deadline_date >= CURRENT_DATE;
    """)


def downgrade():
    op.drop_index("idx_deadlines_uncovered", "compliance_deadlines")
    op.drop_index("idx_deadlines_days_rem", "compliance_deadlines")
    op.drop_index("idx_deadlines_date", "compliance_deadlines")
    op.drop_table("compliance_deadlines")
