"""Default compliance_deadlines coverage columns so raw-SQL sync inserts work.

Revision ID: 053
Revises: 052
Create Date: 2026-10-10

Companion to 052. `compliance_deadlines` has four NOT NULL coverage-tracking
columns with no DB default — ``has_test_coverage``, ``test_coverage_count``,
``obligation_count``, ``uncovered_obligation_count``. They're populated later
by P2's coverage enrichment, so P1's ``/api/sync/deadlines`` payload doesn't
carry them and the raw-SQL deadline upsert in `UpdateService._sync_deadlines`
omits them. On a fresh replica that tripped::

    null value in column "has_test_coverage" ... violates not-null constraint

and the deadlines sync was swallowed (0 rows) while every other ancillary
table synced. Defaulting the four to "nothing computed yet" (FALSE / 0) lets
the insert succeed; the enrichment pass overwrites them with real counts.

Idempotent; safe to re-run.
"""
from alembic import op


revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


_BOOL_COLS = ("has_test_coverage",)
_INT_COLS = ("test_coverage_count", "obligation_count", "uncovered_obligation_count")


def upgrade() -> None:
    for c in _BOOL_COLS:
        op.execute(f"ALTER TABLE compliance_deadlines ALTER COLUMN {c} SET DEFAULT FALSE;")
    for c in _INT_COLS:
        op.execute(f"ALTER TABLE compliance_deadlines ALTER COLUMN {c} SET DEFAULT 0;")


def downgrade() -> None:
    for c in _BOOL_COLS + _INT_COLS:
        op.execute(f"ALTER TABLE compliance_deadlines ALTER COLUMN {c} DROP DEFAULT;")
