"""Add regulatory_change_records, bank_policies, policy_regulation_mappings (P2).

Revision ID: 017
Revises: 016
Create Date: 2026-04-28

regulatory_change_records: extends 011's table with per-bank scope (client_id +
                           updated_at) for the Panel 3 change-record inbox.
bank_policies:             internal bank policies (Panel 6 — policy gap matrix).
policy_regulation_mappings: links policies to regulations (Panel 6).
"""
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 011 already created regulatory_change_records; only add the
    # P2-specific columns. CREATE TABLE IF NOT EXISTS was a no-op when the
    # table pre-existed, leaving client_id absent and breaking the index below.
    op.execute(
        "ALTER TABLE regulatory_change_records "
        "ADD COLUMN IF NOT EXISTS client_id VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE regulatory_change_records "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rcr_client_status "
        "ON regulatory_change_records (client_id, status) WHERE is_active = TRUE"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS bank_policies (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_ref         VARCHAR(100) NOT NULL,
            policy_name        TEXT         NOT NULL,
            policy_version     VARCHAR(20),
            policy_owner       VARCHAR(200),
            policy_category    VARCHAR(50),
            effective_date     TIMESTAMP WITH TIME ZONE,
            last_reviewed_date TIMESTAMP WITH TIME ZONE,
            next_review_date   TIMESTAMP WITH TIME ZONE,
            document_url       TEXT,
            status             VARCHAR(20) NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','under_review','retired')),
            client_id          VARCHAR(100) NOT NULL,
            created_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (policy_ref, client_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pol_client "
        "ON bank_policies (client_id) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pol_review "
        "ON bank_policies (next_review_date ASC) WHERE status = 'active'"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS policy_regulation_mappings (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_id         UUID NOT NULL REFERENCES bank_policies(id),
            regulation_id     VARCHAR(100) NOT NULL,
            obligation_id     UUID,
            mapping_rationale TEXT,
            mapped_by         VARCHAR(200),
            ai_suggested      BOOLEAN DEFAULT FALSE,
            ai_confidence     FLOAT,
            mapped_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE (policy_id, regulation_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_polmap_reg "
        "ON policy_regulation_mappings (regulation_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS policy_regulation_mappings")
    op.execute("DROP TABLE IF EXISTS bank_policies")
    op.execute("DROP TABLE IF EXISTS regulatory_change_records")
