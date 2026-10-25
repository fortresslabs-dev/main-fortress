"""regulatory_change_records.test_jira_key

Stores the JIRA story key when a regulatory change is routed into the 6-agent
test-generation pipeline (Changes → "Generate/refresh test"), so a changed rule
produces a refreshed test + PR just like the Rules flow.

Revision ID: 054
Revises: 053
"""
from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE regulatory_change_records "
               "ADD COLUMN IF NOT EXISTS test_jira_key varchar(50)")


def downgrade() -> None:
    op.execute("ALTER TABLE regulatory_change_records "
               "DROP COLUMN IF EXISTS test_jira_key")
