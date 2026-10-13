"""Add matched_rule_id to screening_cases (D3 — per-rule precision).

Revision ID: 035
Revises: 034
Create Date: 2026-06-16

`/api/v1/dashboard/screening/rules-perf` currently shows a per-rule
table sourced from `screening_rules` but its precision numbers are
GLOBAL across all cases — examiners can't actually answer "what is the
precision of your fuzzy-match rule vs your ownership rule?". The fix
is to attribute each open case to the primary rule that fired so the
rules-perf join is per-rule.

Schema change:

  screening_cases.matched_rule_id  VARCHAR(64) NULL
    REFERENCES screening_rules(rule_id) ON DELETE SET NULL

Nullable because legacy cases (pre-035) have no attribution and
because not every code path that opens a case will be backfilled in
this PR — only `OFACScreener._write_audit`. SET NULL on the FK so a
compliance-driven retirement of a rule from `screening_rules` doesn't
cascade-delete years of audit-trail cases.

Attribution map (computed in OFACScreener):
  - any Violation.rule_id startswith 'OFAC_BLOCK' → OFAC-SCREEN-OWN50-001
  - else (fuzzy/trgm/vector name match)          → OFAC-SCREEN-FUZZY-001
  - FRESH / CLEAN never open a case in isolation — they don't appear.

Depends on 034 (screening_rules table). Run order in prod: 034 → 035.
"""
from alembic import op


revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE screening_cases
        ADD COLUMN IF NOT EXISTS matched_rule_id VARCHAR(64) NULL
    """)
    # FK ON DELETE SET NULL — see docstring. IF NOT EXISTS guard so
    # re-running the migration is idempotent in dev.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_screening_cases_matched_rule'
            ) THEN
                ALTER TABLE screening_cases
                ADD CONSTRAINT fk_screening_cases_matched_rule
                FOREIGN KEY (matched_rule_id)
                REFERENCES screening_rules(rule_id)
                ON DELETE SET NULL;
            END IF;
        END $$
    """)
    # Partial index — only non-null values participate in /rules-perf joins.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_screening_cases_matched_rule_id
        ON screening_cases (matched_rule_id)
        WHERE matched_rule_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_screening_cases_matched_rule_id")
    op.execute("""
        ALTER TABLE screening_cases
        DROP CONSTRAINT IF EXISTS fk_screening_cases_matched_rule
    """)
    op.execute("ALTER TABLE screening_cases DROP COLUMN IF EXISTS matched_rule_id")
