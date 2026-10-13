"""Add country and nationality columns to ofac_sdn_entries (FIX-2)."""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ofac_sdn_entries", sa.Column("country", sa.String(100), nullable=True))
    op.add_column("ofac_sdn_entries", sa.Column("nationality", sa.String(100), nullable=True))
    op.create_index("idx_sdn_country", "ofac_sdn_entries", ["country"])


def downgrade():
    op.drop_index("idx_sdn_country", "ofac_sdn_entries")
    op.drop_column("ofac_sdn_entries", "nationality")
    op.drop_column("ofac_sdn_entries", "country")
