"""Add screening_events + screening_cases + join table.

Revision ID: 028
Revises: 027
Create Date: 2026-06-04

Audit + case-management substrate for the OFAC/SDN screening APIs
(see docs/runbooks/ofac-screening-api-contract.md).

Three tables:

  screening_events
    Immutable audit row per OFACScreener.screen() call. Holds the criteria
    that were screened (PII — name, DOB, address), the engine response in
    full (matches + subscores), and the actor that initiated it.
    Append-only by convention; no trigger guard because ops needs the option
    to backfill / re-anchor records during incident response.

  screening_cases
    Workflow row, opened when an event's decision is REVIEW or HOLD.
    Tracks reviewer assignment, action, evidence, and final resolution.

  screening_case_events
    Many-to-one — multiple events (initial screen, continuous-monitoring
    rehits, reviewer-triggered re-screens) tie to a single case.

PII NOTE: criteria + engine_response contain customer data. These tables
live in the P2 (bank-side) Aurora only; they must NEVER be synced upstream
to Fortress Cloud. The migration runs on P1 too because alembic is global,
but the tables stay empty there.

Retention: 7yr (configurable per institution). Enforced by an out-of-band
sweeper, not by this schema.
"""
from alembic import op


revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── screening_events ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS screening_events (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            screening_id          VARCHAR(64)   NOT NULL UNIQUE,
            request_kind          VARCHAR(32)   NOT NULL,
            request_group_id      VARCHAR(128),
            external_customer_id  VARCHAR(128),
            party_role            VARCHAR(32),
            party_name            TEXT          NOT NULL,
            party_country         VARCHAR(8),
            criteria              JSONB         NOT NULL,
            criteria_hash         CHAR(64)      NOT NULL,
            decision              VARCHAR(8)    NOT NULL,
            final_score           NUMERIC(5,4)  NOT NULL DEFAULT 0,
            match_count           INTEGER       NOT NULL DEFAULT 0,
            engine_mode           VARCHAR(16)   NOT NULL,
            engine_response       JSONB         NOT NULL,
            sdn_data_age_hours    NUMERIC(7,2),
            latency_ms            NUMERIC(8,2)  NOT NULL DEFAULT 0,
            actor_kind            VARCHAR(16)   NOT NULL,
            actor_user_id         VARCHAR(128),
            created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_screening_decision
                CHECK (decision IN ('PASS','REVIEW','HOLD')),
            CONSTRAINT ck_screening_request_kind
                CHECK (request_kind IN ('customer','transaction','bulk','monitoring','dashboard')),
            CONSTRAINT ck_screening_actor_kind
                CHECK (actor_kind IN ('api','dashboard','bulk','monitoring'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_screening_events_customer "
        "ON screening_events (external_customer_id) "
        "WHERE external_customer_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_screening_events_group "
        "ON screening_events (request_group_id) "
        "WHERE request_group_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_screening_events_created_at "
        "ON screening_events (created_at DESC)"
    )
    # Partial index on actionable decisions — case dashboard filters here.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_screening_events_actionable "
        "ON screening_events (decision, created_at DESC) "
        "WHERE decision IN ('REVIEW','HOLD')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_screening_events_criteria_hash "
        "ON screening_events (criteria_hash)"
    )

    # ── screening_cases ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS screening_cases (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id                VARCHAR(64)   NOT NULL UNIQUE,
            external_customer_id   VARCHAR(128),
            sdn_number             VARCHAR(50),
            matched_name           TEXT,
            status                 VARCHAR(16)   NOT NULL DEFAULT 'open',
            priority               VARCHAR(8)    NOT NULL,
            decision_at_open       VARCHAR(8)    NOT NULL,
            assignee_user_id       VARCHAR(128),
            first_event_id         UUID          NOT NULL
                REFERENCES screening_events(id),
            last_event_id          UUID          NOT NULL
                REFERENCES screening_events(id),
            event_count            INTEGER       NOT NULL DEFAULT 1,
            reviewer_action        VARCHAR(32),
            reviewer_notes         TEXT,
            reviewer_evidence      JSONB,
            resolved_at            TIMESTAMPTZ,
            resolved_by_user_id    VARCHAR(128),
            created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_case_status
                CHECK (status IN ('open','in_review','cleared','escalated','blocked','closed')),
            CONSTRAINT ck_case_priority
                CHECK (priority IN ('high','medium')),
            CONSTRAINT ck_case_decision_at_open
                CHECK (decision_at_open IN ('REVIEW','HOLD')),
            CONSTRAINT ck_case_reviewer_action
                CHECK (reviewer_action IS NULL OR reviewer_action IN
                       ('cleared_false_positive','confirmed_true_positive',
                        'escalated','needs_more_info'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cases_status "
        "ON screening_cases (status, priority, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cases_assignee "
        "ON screening_cases (assignee_user_id) "
        "WHERE assignee_user_id IS NOT NULL"
    )
    # One OPEN case per (customer, sdn) pair — once cleared/closed, a new
    # alert can open a new case. Application layer should upsert against
    # this index on REVIEW|HOLD events.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cases_open_customer_sdn
        ON screening_cases (external_customer_id, sdn_number)
        WHERE external_customer_id IS NOT NULL
          AND sdn_number IS NOT NULL
          AND status NOT IN ('cleared','closed')
    """)

    # updated_at maintainer
    op.execute("""
        CREATE OR REPLACE FUNCTION screening_cases_touch_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS trig_screening_cases_updated_at ON screening_cases
    """)
    op.execute("""
        CREATE TRIGGER trig_screening_cases_updated_at
        BEFORE UPDATE ON screening_cases
        FOR EACH ROW EXECUTE FUNCTION screening_cases_touch_updated_at()
    """)

    # ── screening_case_events (join) ────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS screening_case_events (
            case_id    UUID NOT NULL REFERENCES screening_cases(id) ON DELETE CASCADE,
            event_id   UUID NOT NULL REFERENCES screening_events(id),
            linked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (case_id, event_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_events_event "
        "ON screening_case_events (event_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS screening_case_events")
    op.execute("DROP TRIGGER IF EXISTS trig_screening_cases_updated_at ON screening_cases")
    op.execute("DROP FUNCTION IF EXISTS screening_cases_touch_updated_at")
    op.execute("DROP TABLE IF EXISTS screening_cases")
    op.execute("DROP TABLE IF EXISTS screening_events")
