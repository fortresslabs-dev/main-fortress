"""Add sync_metadata.last_sdn_change_consumed_ref for keyset pagination.

Revision ID: 031
Revises: 030
Create Date: 2026-06-04

Fixes a cursor infinite loop in _record_sdn_changes: when the backfill
emitted N rows in a single transaction (sharing the SAME detected_at), the
single-column timestamp cursor couldn't advance past the batch boundary —
each subsequent call returned the same first-N rows of that batch.

Fix: keyset pagination on (detected_at, change_ref). Server compares
(detected_at, change_ref) > (since, since_ref); client persists both
cursors. This column holds the change_ref half.

Nullable — populated only after the first Phase B sync cycle on patched P2.
"""
from alembic import op


revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sync_metadata "
        "ADD COLUMN IF NOT EXISTS last_sdn_change_consumed_ref VARCHAR(50)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE sync_metadata DROP COLUMN IF EXISTS last_sdn_change_consumed_ref"
    )
