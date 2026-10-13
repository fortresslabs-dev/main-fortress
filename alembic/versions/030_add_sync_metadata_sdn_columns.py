"""Add sync_metadata.p1_sdn_last_scraped_at + last_sdn_change_consumed_at.

Revision ID: 030
Revises: 029
Create Date: 2026-06-04

Phase B of OFAC screening rollout (P2-side cutover).

  p1_sdn_last_scraped_at      — cached from /api/sync/sdn-health every cycle;
                                read by OFACScreener._enforce_freshness so a
                                broken P2 sync surfaces as stale-source rather
                                than silently screening against day-old data.

  last_sdn_change_consumed_at — cursor for /api/sync/sdn-changes; the next
                                poll uses this as `since=` so we only fetch
                                deltas we haven't processed yet.

Both columns are nullable — populated only after Phase B P2 cutover runs.
Migration is global (also lands on P1) but unused there; sync_metadata on P1
is keyed per-customer for the OPPOSITE direction (P1-side telemetry).
"""
from alembic import op
import sqlalchemy as sa


revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sync_metadata "
        "ADD COLUMN IF NOT EXISTS p1_sdn_last_scraped_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE sync_metadata "
        "ADD COLUMN IF NOT EXISTS last_sdn_change_consumed_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE sync_metadata DROP COLUMN IF EXISTS last_sdn_change_consumed_at"
    )
    op.execute(
        "ALTER TABLE sync_metadata DROP COLUMN IF EXISTS p1_sdn_last_scraped_at"
    )
