"""Add summary_pretty + peer_signal_score to enforcement_actions.

Revision ID: 040
Revises: 039
Create Date: 2026-06-21

The board_report export (design doc §5.1, §4 type 3) needs:
  * a plain-English action summary that a board member can skim
    (violation_summary is regulator-language and often dense)
  * a peer-signal score 0..1 indicating how much this action signals risk
    to the bank's own peer group (computed from institution_type +
    asset_tier overlap with the consuming bank)

Both are populated by a separate backfill (Bedrock Sonnet 4.x for
summary_pretty, weighted-overlap computation for peer_signal_score).
Until then the columns are NULL and the renderer falls back to
violation_summary verbatim.

Additive change. Rollback (per feedback_p1_alembic_blocks_rollback):
    python -m alembic stamp 039
"""
from alembic import op
import sqlalchemy as sa


revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enforcement_actions",
        sa.Column("summary_pretty", sa.Text, nullable=True),
    )
    op.add_column(
        "enforcement_actions",
        sa.Column("peer_signal_score", sa.Numeric(5, 4), nullable=True),
    )
    # CHECK keeps peer_signal_score in the 0..1 band so the board-report
    # renderer can format it as a percentage without sanity-checking.
    op.execute("""
        ALTER TABLE enforcement_actions
        ADD CONSTRAINT ck_enf_peer_signal_range
        CHECK (peer_signal_score IS NULL
               OR (peer_signal_score >= 0 AND peer_signal_score <= 1))
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE enforcement_actions DROP CONSTRAINT IF EXISTS ck_enf_peer_signal_range"
    )
    op.drop_column("enforcement_actions", "peer_signal_score")
    op.drop_column("enforcement_actions", "summary_pretty")
