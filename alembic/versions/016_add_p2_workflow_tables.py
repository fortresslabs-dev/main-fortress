"""Add workflow_executions and checkpoint_approvals tables (Pipeline 2 core).

Revision ID: 016
Revises: 015_add_client_credentials
Create Date: 2026-04-28

These are core P2 tables defined in database.py. In production (ECS, no
Base.metadata.create_all), they must exist via migration.
Uses CREATE TABLE IF NOT EXISTS so this is safe to run on DBs that already
have these tables created by create_all during development.
"""
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types safely (Postgres has no CREATE TYPE IF NOT EXISTS)
    op.execute(
        "DO $$ BEGIN CREATE TYPE workflowstatus AS ENUM "
        "('pending','running','waiting_approval','completed','failed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE checkpointstatus AS ENUM "
        "('pending','approved','rejected'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_executions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            jira_story_id   VARCHAR(50)    NOT NULL,
            jira_story_key  VARCHAR(50)    NOT NULL,
            status          workflowstatus NOT NULL DEFAULT 'pending',
            started_at      TIMESTAMP      NOT NULL DEFAULT NOW(),
            completed_at    TIMESTAMP,
            context_data    JSON           NOT NULL DEFAULT '{}',
            artifacts       JSON           NOT NULL DEFAULT '{}'
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_jira_story_id "
        "ON workflow_executions (jira_story_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS checkpoint_approvals (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workflow_id     UUID REFERENCES workflow_executions(id),
            checkpoint_name VARCHAR(50)       NOT NULL,
            status          checkpointstatus  NOT NULL DEFAULT 'pending',
            approved_by     VARCHAR(100),
            approved_at     TIMESTAMP,
            artifacts       JSON NOT NULL DEFAULT '{}',
            feedback        JSON NOT NULL DEFAULT '{}'
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS checkpoint_approvals")
    op.execute("DROP TABLE IF EXISTS workflow_executions")
    op.execute("DROP TYPE IF EXISTS checkpointstatus")
    op.execute("DROP TYPE IF EXISTS workflowstatus")
