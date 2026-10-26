"""regulatory_change_records.remediation_jira_key

Stores the JIRA remediation epic key opened when a CCO dispositions a
regulatory change as "requires action" from the dashboard.

Revision ID: 055
Revises: 054
"""
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE regulatory_change_records "
               "ADD COLUMN IF NOT EXISTS remediation_jira_key varchar(50)")


def downgrade() -> None:
    op.execute("ALTER TABLE regulatory_change_records "
               "DROP COLUMN IF EXISTS remediation_jira_key")
