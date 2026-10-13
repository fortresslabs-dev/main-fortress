"""Widen ck_change_record_type to include 'reactivated'.

Revision ID: 029
Revises: 028
Create Date: 2026-06-04

The OFAC SDN loader emits three kinds of variant lifecycle events into
regulatory_change_records:

  - 'added'       — first time we see this (sdn_number, name_normalized)
  - 'reactivated' — was previously tombstoned (is_active=FALSE), now back
  - 'removed'     — soft-deactivated after N consecutive misses (existing)

011 ck_change_record_type was ('added','modified','removed','clarified').
We add 'reactivated' so reactivations are precisely typed — P2's
continuous-monitoring API counts them distinctly from modifications.
"""
from alembic import op


revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE regulatory_change_records "
        "DROP CONSTRAINT IF EXISTS ck_change_record_type"
    )
    op.execute("""
        ALTER TABLE regulatory_change_records
        ADD CONSTRAINT ck_change_record_type
        CHECK (change_type IN ('added','modified','removed','clarified','reactivated'))
    """)


def downgrade() -> None:
    # Drop any 'reactivated' rows first — the narrower CHECK would otherwise
    # reject the constraint add. Best-effort; lose audit history.
    op.execute(
        "DELETE FROM regulatory_change_records WHERE change_type = 'reactivated'"
    )
    op.execute(
        "ALTER TABLE regulatory_change_records "
        "DROP CONSTRAINT IF EXISTS ck_change_record_type"
    )
    op.execute("""
        ALTER TABLE regulatory_change_records
        ADD CONSTRAINT ck_change_record_type
        CHECK (change_type IN ('added','modified','removed','clarified'))
    """)
