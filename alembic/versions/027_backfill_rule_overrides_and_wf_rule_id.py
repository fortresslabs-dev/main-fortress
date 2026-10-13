"""Backfill rule_overrides from bank_policies + add workflow_executions.rule_id.

Revision ID: 027
Revises: 026
Create Date: 2026-05-30

Two related cleanups:

1. Move any bank_policies rows that were really rule overrides
   (policy_category='compliance_rule') into the new rule_overrides table,
   then delete them from bank_policies so it only stores real policy
   documents going forward. The dashboard previously stored the
   na_reason / threshold_override text in bank_policies.policy_name —
   we route it to the correct column based on status.

2. Add workflow_executions.rule_id so the dashboard can join workflow
   runs to rules by exact match. Replaces the previous
   `jira_story_id LIKE '%' || rule_id || '%'` substring trick, which
   never matched because jira_story_id holds a JIRA key like 'COMP-123'.
"""
import sqlalchemy as sa
from alembic import op


revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # bank_policies / workflow_executions are P2-only (migrations 016/017).
    # On P1 the entrypoint runs `alembic upgrade head` against a schema that
    # only contains the Fortress-side tables — skip the P2 cleanup there.
    insp = sa.inspect(op.get_bind())

    if insp.has_table("bank_policies"):
        op.execute("""
            INSERT INTO rule_overrides
                (id, client_id, rule_id, status, na_reason, threshold_override,
                 reviewed_by, reviewed_at, created_at, updated_at)
            SELECT
                gen_random_uuid(),
                bp.client_id,
                bp.policy_ref,
                bp.status,
                CASE WHEN bp.status = 'not_applicable' THEN bp.policy_name END,
                CASE WHEN bp.status = 'override'       THEN bp.policy_name END,
                bp.policy_owner,
                bp.last_reviewed_date,
                bp.created_at,
                bp.updated_at
            FROM bank_policies bp
            WHERE bp.policy_category = 'compliance_rule'
            ON CONFLICT (client_id, rule_id) DO NOTHING
        """)
        op.execute(
            "DELETE FROM bank_policies WHERE policy_category = 'compliance_rule'"
        )

    if insp.has_table("workflow_executions"):
        op.execute(
            "ALTER TABLE workflow_executions "
            "ADD COLUMN IF NOT EXISTS rule_id VARCHAR(100)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_wf_rule_id "
            "ON workflow_executions (rule_id) WHERE rule_id IS NOT NULL"
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("workflow_executions"):
        op.execute("DROP INDEX IF EXISTS idx_wf_rule_id")
        op.execute("ALTER TABLE workflow_executions DROP COLUMN IF EXISTS rule_id")
    # Note: backfill is not reversed — the bank_policies rows are gone.
