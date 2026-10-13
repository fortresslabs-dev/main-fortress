"""Add rule_overrides table for CCO decisions on Fortress-scraped rules.

Revision ID: 026
Revises: 025
Create Date: 2026-05-30

rule_overrides is a separate domain object from bank_policies:
  - bank_policies        = the bank's internal policy documents (name, owner,
                           document URL, review dates), managed by the CCO
                           outside Fortress.
  - rule_overrides       = the CCO's decisions on Fortress-scraped regulatory
                           rules (mark N/A, set a threshold override).

Prior to 026 the dashboard wedged rule overrides into bank_policies with
policy_category='compliance_rule', but bank_policies has no na_reason /
threshold_override columns and its status CHECK only allows
'active/under_review/retired' — so PATCH /rules/{id}/status 500'd at the DB
and GET /dashboard/rules 500'd on the missing columns.
"""
from alembic import op


revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS rule_overrides (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            client_id           VARCHAR(100) NOT NULL,
            rule_id             VARCHAR(100) NOT NULL,
            status              VARCHAR(20)  NOT NULL
                CHECK (status IN ('active','not_applicable','override')),
            na_reason           TEXT,
            threshold_override  TEXT,
            reviewed_by         VARCHAR(200),
            reviewed_at         TIMESTAMP WITH TIME ZONE,
            created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (client_id, rule_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rule_overrides_rule "
        "ON rule_overrides (rule_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rule_overrides_client_status "
        "ON rule_overrides (client_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rule_overrides")
