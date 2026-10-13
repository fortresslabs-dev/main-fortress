"""Convert regulatory_change_records timestamp columns to TIMESTAMPTZ.

Revision ID: 020
Revises: 019
Create Date: 2026-05-14

The ORM bootstrap (scripts/setup/init_p1_schema.py) created these columns
as TIMESTAMP WITHOUT TIME ZONE because the ORM model used `Column(DateTime)`
instead of `Column(DateTime(timezone=True))`. The original migration 011
already specified TIMESTAMPTZ but was never applied — init_p1_schema.py
stamps alembic_version=019 directly. ChangeManager writes tz-aware UTC
datetimes, which then fail with `can't subtract offset-naive and offset-aware`
on insert.

USING clause treats the stored naive timestamps as UTC, matching how
ChangeManager produced them.
"""
from alembic import op


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


_COLUMNS = ["detected_at", "status_updated_at", "signed_off_at", "created_at"]


def upgrade() -> None:
    for col in _COLUMNS:
        op.execute(f"""
            ALTER TABLE regulatory_change_records
            ALTER COLUMN {col} TYPE TIMESTAMPTZ
            USING {col} AT TIME ZONE 'UTC'
        """)


def downgrade() -> None:
    for col in _COLUMNS:
        op.execute(f"""
            ALTER TABLE regulatory_change_records
            ALTER COLUMN {col} TYPE TIMESTAMP
            USING {col} AT TIME ZONE 'UTC'
        """)
