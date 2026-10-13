"""Widen 3 narrow CHECK constraints + canonicalize compliance_rules.regulator casing.

Revision ID: 024
Revises: 023
Create Date: 2026-05-29

Three P2 CHECK constraints (009 ck_enforcement_action_type,
010 ck_deadline_type, 011 ck_impact_rating) were defined too narrowly versus
what the P1 scraper actually emits. They drop 100% of inbound rows from
/sync/enforcement-actions and partially break /sync/deadlines and
/sync/change-records. Fix: drop + re-add each constraint with the wider set
the scraper produces, and case-fold impact_rating so 'high' / 'High' both pass.

Also folds lowercase regulator strings ('cfpb', 'occ', 'frb', 'ofac',
'ffiec', 'fincen', 'nydfs', 'sec', 'ncua') in compliance_rules into their
UPPERCASE canonical form. Origin: scripts/seed_demo_p2.py inserts lowercase
identifiers; the sync layer (P1) emits uppercase. Result was 25 silently
duplicated rule rows under different regulator casings.
"""
from alembic import op


revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. ck_enforcement_action_type ────────────────────────────────────
    # 009 allowed only the FRB taxonomy. Add OCC/FDIC variants the scraper
    # routinely emits.
    op.execute("ALTER TABLE enforcement_actions DROP CONSTRAINT IF EXISTS ck_enforcement_action_type")
    op.execute("""
        ALTER TABLE enforcement_actions
        ADD CONSTRAINT ck_enforcement_action_type
        CHECK (action_type IN (
            'consent_order',
            'formal_agreement',
            'civil_money_penalty',
            'mra',
            'mraia',
            'cease_and_desist',
            'written_agreement',
            'supervisory_action',
            'formal_action',
            'informal_action',
            'prohibition',
            'removal_order',
            'restitution_order',
            'termination_order',
            'other'
        ))
    """)

    # ── 2. ck_deadline_type ──────────────────────────────────────────────
    # 010 had only four broad classes. Scraper + DeadlineTracker emit
    # per-frequency strings as well as internal_* and compliance_effective_date
    # for board/policy reviews that are tracked but not externally filed.
    op.execute("ALTER TABLE compliance_deadlines DROP CONSTRAINT IF EXISTS ck_deadline_type")
    op.execute("""
        ALTER TABLE compliance_deadlines
        ADD CONSTRAINT ck_deadline_type
        CHECK (deadline_type IN (
            'effective_date',
            'filing_deadline',
            'reporting_deadline',
            'review_deadline',
            'quarterly_filing',
            'annual_filing',
            'monthly_filing',
            'examination_deadline',
            'compliance_deadline',
            'compliance_effective_date',
            'proposed_effective_date',
            'internal_review',
            'internal_assessment',
            'other'
        ))
    """)

    # ── 3. ck_impact_rating (case-insensitive) ──────────────────────────
    # 011 enumerated 'High','Medium','Low','None'. Scraper emits lowercase
    # 'high' (and sometimes 'critical'). Switch to LOWER()-based check so
    # both casings pass and the canonical set is unambiguous.
    op.execute("ALTER TABLE regulatory_change_records DROP CONSTRAINT IF EXISTS ck_impact_rating")
    op.execute("""
        ALTER TABLE regulatory_change_records
        ADD CONSTRAINT ck_impact_rating
        CHECK (impact_rating IS NULL OR LOWER(impact_rating) IN (
            'critical', 'high', 'medium', 'low', 'none'
        ))
    """)

    # ── 4. Canonicalize compliance_rules.regulator casing ───────────────
    # seed_demo_p2.py wrote 25 rows with lowercase regulator strings; the
    # P1 sync layer writes UPPERCASE. Result: visible regulator-casing
    # duplicates in the dashboard regulator filter.
    #
    # We can't blindly UPPER() because (a) canonical mixed-case prefixes
    # like CA-DFPI / TX-DOB / FL-OFR must stay intact (their UPPER is a
    # no-op, so the predicate skips them), and (b) where both a lowercase
    # AND an UPPER twin already exist for the same (rule_id, geography),
    # bare UPDATE collides with uq_rule_regulator_geo. Step 4a deletes the
    # lowercase row in any such collision pair (canonical UPPER row wins);
    # step 4b UPPERs the lone lowercase rows that have no twin.
    op.execute("""
        DELETE FROM compliance_rules cr1
        WHERE cr1.regulator IS NOT NULL
          AND cr1.regulator <> UPPER(cr1.regulator)
          AND EXISTS (
              SELECT 1 FROM compliance_rules cr2
              WHERE cr2.rule_id = cr1.rule_id
                AND cr2.regulator = UPPER(cr1.regulator)
                AND COALESCE(cr2.geography, '') = COALESCE(cr1.geography, '')
                AND cr2.id <> cr1.id
          )
    """)
    op.execute("""
        UPDATE compliance_rules
        SET regulator = UPPER(regulator)
        WHERE regulator IS NOT NULL
          AND regulator <> UPPER(regulator)
    """)


def downgrade() -> None:
    # Reverse the canonicalization (best-effort — won't recover original
    # mixed case unless we track it; lower() reverses any UPPER() we did).
    op.execute("""
        UPDATE compliance_rules
        SET regulator = LOWER(regulator)
        WHERE regulator IN (
            'CFPB','OCC','FRB','OFAC','FFIEC','FINCEN',
            'NYDFS','SEC','NCUA'
        )
    """)

    # Restore the original narrow CHECKs (matches 009/010/011)
    op.execute("ALTER TABLE regulatory_change_records DROP CONSTRAINT IF EXISTS ck_impact_rating")
    op.execute("""
        ALTER TABLE regulatory_change_records
        ADD CONSTRAINT ck_impact_rating
        CHECK (impact_rating IN ('High','Medium','Low','None'))
    """)

    op.execute("ALTER TABLE compliance_deadlines DROP CONSTRAINT IF EXISTS ck_deadline_type")
    op.execute("""
        ALTER TABLE compliance_deadlines
        ADD CONSTRAINT ck_deadline_type
        CHECK (deadline_type IN (
            'effective_date','filing_deadline','reporting_deadline','review_deadline'
        ))
    """)

    op.execute("ALTER TABLE enforcement_actions DROP CONSTRAINT IF EXISTS ck_enforcement_action_type")
    op.execute("""
        ALTER TABLE enforcement_actions
        ADD CONSTRAINT ck_enforcement_action_type
        CHECK (action_type IN (
            'consent_order','formal_agreement','civil_money_penalty',
            'mra','mraia','cease_and_desist','written_agreement'
        ))
    """)
