"""Add scrape_runs table for MCP scrape monitor.

Revision ID: 018
Revises: 017
Create Date: 2026-05-09
"""
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS scrape_runs (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            connector_name   VARCHAR(100) NOT NULL,
            success          BOOLEAN NOT NULL,
            rules_extracted  INTEGER DEFAULT 0,
            rules_changed    INTEGER DEFAULT 0,
            error_message    TEXT DEFAULT '',
            duration_seconds FLOAT DEFAULT 0,
            s3_key           VARCHAR(500) DEFAULT '',
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_sr_connector_run ON scrape_runs (connector_name, run_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sr_success ON scrape_runs (success, run_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sr_run_at ON scrape_runs (run_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scrape_runs")
