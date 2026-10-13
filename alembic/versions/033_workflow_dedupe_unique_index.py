"""M9 — partial unique index on workflow_executions.jira_story_id WHERE pending/running.

Revision ID: 033
Revises: 032
Create Date: 2026-06-10

JIRA retries webhook deliveries on any non-2xx (and sometimes on slow
2xx). Before this migration, the webhook handler did a SELECT-then-
INSERT on workflow_executions which races: two concurrent retries can
both miss the existing row and INSERT, spawning N parallel workflows
for one JIRA story.

The partial unique index pins ONE in-flight workflow per jira_story_id.
COMPLETED/FAILED rows are excluded so historical workflows do not block
a fresh trigger when the story is re-opened.

Index is created CONCURRENTLY in the upgrade to avoid taking an
exclusive lock on a hot table; alembic's autocommit-block is used so
CONCURRENTLY can run outside a transaction.
"""
import sqlalchemy as sa
from alembic import op


revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


# workflow_executions / workflowstatus are P2-only (migration 016). P1's entrypoint
# runs `alembic upgrade head` against the P1 Aurora cluster — guard so this is a
# no-op there. Also covers P1 clusters that ended up with a stale workflowstatus
# enum (table present, but enum lacking 'pending'/'running') from a pre-016 import.
def _migration_applies() -> bool:
    bind = op.get_bind()
    has_table = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = 'workflow_executions'"
    )).scalar()
    if not has_table:
        return False
    states = {row[0] for row in bind.execute(sa.text(
        "SELECT enumlabel FROM pg_enum "
        "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
        "WHERE pg_type.typname = 'workflowstatus'"
    ))}
    return {"pending", "running"}.issubset(states)


def upgrade() -> None:
    if not _migration_applies():
        return

    # First, sweep any existing duplicate (jira_story_id, pending/running)
    # rows down to one by keeping the latest started_at. Without this, the
    # CREATE INDEX CONCURRENTLY below will fail on the duplicate.
    op.execute("""
        WITH ranked AS (
          SELECT id,
                 ROW_NUMBER() OVER (
                   PARTITION BY jira_story_id
                   ORDER BY started_at DESC
                 ) AS rn
          FROM workflow_executions
          WHERE status IN ('pending', 'running')
        )
        UPDATE workflow_executions
        SET status = 'failed'
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    """)

    with op.get_context().autocommit_block():
        op.execute("""
            CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
              uq_workflow_jira_story_inflight
            ON workflow_executions (jira_story_id)
            WHERE status IN ('pending', 'running')
        """)


def downgrade() -> None:
    if not _migration_applies():
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_workflow_jira_story_inflight")
