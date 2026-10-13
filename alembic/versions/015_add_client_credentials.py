"""Add client_credentials table for per-bank API key management."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if not conn.dialect.has_table(conn, "client_credentials"):
        op.create_table(
            "client_credentials",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("customer_id", sa.String(100), nullable=False),
            sa.Column("bank_name", sa.String(200), nullable=False),
            sa.Column("api_key_hash", sa.String(64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("tier", sa.String(20), nullable=False, server_default="standard"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_client_credentials_customer_id", "client_credentials", ["customer_id"], unique=True)


def downgrade():
    op.drop_index("ix_client_credentials_customer_id", table_name="client_credentials")
    op.drop_table("client_credentials")
