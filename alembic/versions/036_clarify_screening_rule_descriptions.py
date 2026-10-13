"""Clarify what `screening_rules.weight` actually does (G2).

Revision ID: 036
Revises: 035
Create Date: 2026-06-16

`/dashboard/rules-perf` surfaces a `weight` column on each rule that
compliance and bank examiners assume is wired to scoring. It mostly
isn't — `_derive_decision` uses `OFAC_HOLD_THRESHOLD = 0.92` (hardcoded
in validators.py) plus per-class SSM thresholds at
`/fortress/p2/ofac/threshold/*`. Only FRESH is being wired in this PR.

This migration rewrites each rule's `description` to spell out exactly
what `weight` does for that rule today:

  FUZZY  — Policy weight (display-only). Scoring governed by
           OFAC_HOLD_THRESHOLD + per-class SSM thresholds.
  CLEAN  — Display-only. CLEAN is the pass-through path — no case
           opens; weight has no engine effect.
  OWN50  — Policy weight (display-only). Decisions use the 50%-rule
           (OFAC_BLOCK violations) from compliance_rules.
  FRESH  — Engine-active. weight > 0 enables the staleness bump
           (REVIEW→HOLD if SDN data > 24h old). weight <= 0 disables.

Versions are bumped so audit/history records the semantic change.
`updated_by = 'migration_036'`. Pair this migration with the
validators.py wiring of FRESH and the FE relabel.
"""
from alembic import op


revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


_RULE_DESCRIPTIONS = {
    "OFAC-SCREEN-FUZZY-001": (
        "Policy weight (display-only). Scoring governed by OFAC_HOLD_THRESHOLD "
        "(0.92) and per-class SSM thresholds at /fortress/p2/ofac/threshold/*."
    ),
    "OFAC-SCREEN-CLEAN-001": (
        "Display-only. CLEAN is the pass-through path — no case opens; "
        "weight has no engine effect."
    ),
    "OFAC-SCREEN-OWN50-001": (
        "Policy weight (display-only). Decisions use the 50%-rule "
        "(OFAC_BLOCK violations) sourced from compliance_rules."
    ),
    "OFAC-SCREEN-FRESH-001": (
        "Engine-active. weight > 0 enables the staleness bump "
        "(REVIEW→HOLD when SDN data is older than 24h); weight <= 0 disables it."
    ),
}


# Reverse map for downgrade — the descriptions migration 034 originally seeded.
_RULE_DESCRIPTIONS_PREV = {
    "OFAC-SCREEN-FUZZY-001":
        "Name similarity (token_sort_ratio) above the per-class threshold opens a REVIEW case.",
    "OFAC-SCREEN-CLEAN-001":
        "No SDN hit and no fuzzy/alias/vector signal — party passes.",
    "OFAC-SCREEN-OWN50-001":
        "OFAC 50% Rule: entities owned >=50% by a blocked party inherit the block.",
    "OFAC-SCREEN-FRESH-001":
        "SDN data older than the soft-stale threshold bumps the decision one level.",
}


def upgrade() -> None:
    from sqlalchemy import text
    conn = op.get_bind()
    for rule_id, desc in _RULE_DESCRIPTIONS.items():
        conn.execute(text("""
            UPDATE screening_rules
            SET description = :desc,
                updated_by  = 'migration_036',
                version     = version + 1
            WHERE rule_id = :rule_id
        """), {"desc": desc, "rule_id": rule_id})


def downgrade() -> None:
    from sqlalchemy import text
    conn = op.get_bind()
    for rule_id, desc in _RULE_DESCRIPTIONS_PREV.items():
        conn.execute(text("""
            UPDATE screening_rules
            SET description = :desc,
                updated_by  = 'migration_036_downgrade',
                version     = GREATEST(version - 1, 1)
            WHERE rule_id = :rule_id
        """), {"desc": desc, "rule_id": rule_id})
