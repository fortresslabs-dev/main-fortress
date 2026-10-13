"""Forward-only widening of ck_deadline_type to include internal_*/compliance_effective_date.

Revision ID: 025
Revises: 024
Create Date: 2026-05-29

Migration 024 widened ck_deadline_type but missed three values the
DeadlineTracker actually emits (internal_review, internal_assessment,
compliance_effective_date). On P2 testbank, 024 succeeded against an empty
table, then incoming /sync/deadlines rows from P1 were rejected at INSERT
time. On P1, 024 itself failed because compliance_deadlines already held
an internal_review row from the in-process seeder.

024 has been edited in-place so fresh deploys pick up the wider set in one
hop. 025 is the forward-only step that pulls already-deployed databases
(which recorded a clean 024 against the narrower set) up to the same state.
Idempotent — DROP IF EXISTS then ADD with the wider set.
"""
from alembic import op


revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


_WIDE_DEADLINE_TYPES = (
    "'effective_date',"
    "'filing_deadline',"
    "'reporting_deadline',"
    "'review_deadline',"
    "'quarterly_filing',"
    "'annual_filing',"
    "'monthly_filing',"
    "'examination_deadline',"
    "'compliance_deadline',"
    "'compliance_effective_date',"
    "'proposed_effective_date',"
    "'internal_review',"
    "'internal_assessment',"
    "'other'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE compliance_deadlines DROP CONSTRAINT IF EXISTS ck_deadline_type")
    op.execute(
        f"ALTER TABLE compliance_deadlines "
        f"ADD CONSTRAINT ck_deadline_type "
        f"CHECK (deadline_type IN ({_WIDE_DEADLINE_TYPES}))"
    )


def downgrade() -> None:
    # Revert to the 024 set (no internal_*/compliance_effective_date).
    op.execute("ALTER TABLE compliance_deadlines DROP CONSTRAINT IF EXISTS ck_deadline_type")
    op.execute("""
        ALTER TABLE compliance_deadlines
        ADD CONSTRAINT ck_deadline_type
        CHECK (deadline_type IN (
            'effective_date','filing_deadline','reporting_deadline','review_deadline',
            'quarterly_filing','annual_filing','monthly_filing',
            'examination_deadline','compliance_deadline','other'
        ))
    """)
