"""Add screening_rules — DB-backed inventory for OFAC screening rules.

Revision ID: 034
Revises: 033
Create Date: 2026-06-15

Replaces the hardcoded `_RULES_INVENTORY` list in
`src/api/dashboard_api.py` (formerly at lines 1491-1500). The dashboard
`/rules-perf` endpoint and the FE OFAC Engine health card now read from
this table so compliance can add, rename, reweight, or deactivate
screening rules without a code deploy.

Schema mirrors the four columns the FE already reads (`rule_id`,
`name`, `weight`) plus `description`, `is_active`, and
`updated_by`/`updated_at` so changes are auditable. `version` is a
monotonic int — bump it whenever weights/thresholds shift so
historical screening_cases can be re-keyed by `(rule_id, version)` if
we later add that join.

P1 + P2 — the table is harmless on P1 (no callers) and required on
P2. Seeded with the four screening rules that were in the hardcoded
list.
"""
from alembic import op


revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS screening_rules (
            rule_id      VARCHAR(64)  PRIMARY KEY,
            name         TEXT         NOT NULL,
            description  TEXT,
            weight       NUMERIC(4,2) NOT NULL DEFAULT 1.0,
            version      INTEGER      NOT NULL DEFAULT 1,
            is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
            updated_by   VARCHAR(128),
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_screening_rules_weight
                CHECK (weight >= 0 AND weight <= 10)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_screening_rules_active "
        "ON screening_rules (is_active) WHERE is_active = TRUE"
    )

    # updated_at maintainer (same pattern as screening_cases trigger)
    op.execute("""
        CREATE OR REPLACE FUNCTION screening_rules_touch_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS trig_screening_rules_updated_at ON screening_rules
    """)
    op.execute("""
        CREATE TRIGGER trig_screening_rules_updated_at
        BEFORE UPDATE ON screening_rules
        FOR EACH ROW EXECUTE FUNCTION screening_rules_touch_updated_at()
    """)

    # Seed today's four rules. ON CONFLICT keeps re-runs idempotent and
    # preserves any operator edits already made via the admin endpoint.
    op.execute("""
        INSERT INTO screening_rules (rule_id, name, description, weight, updated_by)
        VALUES
          ('OFAC-SCREEN-FUZZY-001',
           'Fuzzy match triggers review',
           'Name similarity (token_sort_ratio) above the per-class threshold opens a REVIEW case.',
           1.0, 'migration_034'),
          ('OFAC-SCREEN-CLEAN-001',
           'Clean name clears',
           'No SDN hit and no fuzzy/alias/vector signal — party passes.',
           0.8, 'migration_034'),
          ('OFAC-SCREEN-OWN50-001',
           '>=50%-owned entity blocked',
           'OFAC 50% Rule: entities owned >=50% by a blocked party inherit the block.',
           1.0, 'migration_034'),
          ('OFAC-SCREEN-FRESH-001',
           'List freshness < 24h',
           'SDN data older than the soft-stale threshold bumps the decision one level.',
           1.0, 'migration_034')
        ON CONFLICT (rule_id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trig_screening_rules_updated_at ON screening_rules")
    op.execute("DROP FUNCTION IF EXISTS screening_rules_touch_updated_at")
    op.execute("DROP TABLE IF EXISTS screening_rules")
