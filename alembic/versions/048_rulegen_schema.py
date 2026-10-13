"""Add rulegen staging schema for BSA/AML rule extraction.

Revision ID: 048
Revises: 047
Create Date: 2026-08-15

Ports scrapping/rulegen/schema/ingest.sql into an alembic migration.

Two-schema design: rulegen.* is the SME-reviewed staging area for
BSA/AML rules extracted from CFR sections. Only the promote() function
copies reviewed, non-blocked rules into public.compliance_rules. Writing
several hundred unreviewed rules directly into public — even flagged
is_active=false — means any query that forgets the filter leaks them
into a customer view. A separate schema makes that impossible and
makes a bad run a DROP SCHEMA rather than a delete-by-predicate.

New objects:
  * rulegen schema (CREATE SCHEMA)
  * rulegen.regulations         — one row per CFR part
  * rulegen.cfr_sections        — fetched source text + hash + supersede
  * rulegen.obligations         — one row per duty (NOT one per rule)
  * rulegen.rules               — extracted rules with route + reasons
  * rulegen.runs                — extraction telemetry per batch
  * rulegen.change_records      — nightly hash-drift log
  * trg_no_blocked_promotion    — trigger: blocked rules can never promote
  * rulegen.promote(p_limit)    — reviewed → public.compliance_rules
  * rulegen.v_review_queue      — SME worklist (blocks first)
  * rulegen.v_stale_anchors     — LAW rules whose grounding is stale
  * rulegen.v_decomposition_health — catches the 1:1 obligation collapse

Additive columns on public.compliance_rules (all nullable / defaulted;
existing rows untouched except for a corpus_generation backfill):
  rule_class, evidence_source, result_type, section_id, obligation_id,
  phase, corpus_generation, is_template, needs_review

Constraint on public.compliance_rules:
  ck_law_needs_verification — LAW rows must have citation_verified=TRUE.
  Added NOT VALID so the migration is fast; grandfathers existing rows.
  Complements 046's ck_cr_verified_implies_law (contrapositive shape).

Rollback: downgrade drops the schema CASCADE and removes the added
columns. Legacy rows in public.compliance_rules are never touched.
"""
from alembic import op
import sqlalchemy as sa


revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


# ── rulegen schema DDL ────────────────────────────────────────────────

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS rulegen;"

_CREATE_REGULATIONS = """
CREATE TABLE IF NOT EXISTS rulegen.regulations (
    reg_id          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    cfr_title       INT  NOT NULL,
    cfr_part        TEXT NOT NULL,
    regulator       TEXT NOT NULL,
    authority_type  TEXT NOT NULL DEFAULT 'LAW',
    phase           INT,
    applies_charter JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_CFR_SECTIONS = """
CREATE TABLE IF NOT EXISTS rulegen.cfr_sections (
    id              UUID PRIMARY KEY,
    reg_id          TEXT REFERENCES rulegen.regulations(reg_id),
    cfr_title       INT  NOT NULL,
    citation        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    heading         TEXT,
    section_text    TEXT NOT NULL,
    content_hash    CHAR(64) NOT NULL,
    source_url      TEXT NOT NULL,
    effective_date  DATE,
    retrieved_at    TIMESTAMPTZ NOT NULL,
    superseded_at   TIMESTAMPTZ,
    UNIQUE (cfr_title, citation, content_hash)
);
"""

_CREATE_SECTIONS_INDEX = """
CREATE INDEX IF NOT EXISTS ix_rg_sections_current
    ON rulegen.cfr_sections (cfr_title, citation) WHERE superseded_at IS NULL;
"""

_CREATE_OBLIGATIONS = """
CREATE TABLE IF NOT EXISTS rulegen.obligations (
    obligation_id     TEXT PRIMARY KEY,
    section_id        UUID NOT NULL REFERENCES rulegen.cfr_sections(id),
    reg_id            TEXT REFERENCES rulegen.regulations(reg_id),
    subsection        TEXT,
    obligation_text   TEXT NOT NULL,
    criticality       TEXT,
    control_archetype TEXT,
    applies_bank      BOOLEAN NOT NULL DEFAULT TRUE,
    applies_credit_union BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_RULES = """
CREATE TABLE IF NOT EXISTS rulegen.rules (
    rule_id           TEXT PRIMARY KEY,
    obligation_id     TEXT NOT NULL REFERENCES rulegen.obligations(obligation_id),
    section_id        UUID NOT NULL REFERENCES rulegen.cfr_sections(id),
    rule_class        TEXT NOT NULL,
    rule_logic        TEXT NOT NULL,
    evidence_source   TEXT NOT NULL,
    result_type       TEXT NOT NULL DEFAULT 'PASS/FAIL',
    threshold_value   NUMERIC,
    threshold_unit    TEXT,
    authority_type    TEXT NOT NULL,
    authority_source  TEXT,
    source_span       TEXT,
    span_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    cfr_citation      TEXT NOT NULL,
    applies_bank      BOOLEAN NOT NULL DEFAULT TRUE,
    applies_credit_union BOOLEAN NOT NULL DEFAULT TRUE,
    confidence        NUMERIC,
    route             TEXT NOT NULL,
    reasons           JSONB NOT NULL DEFAULT '[]'::jsonb,
    prompt_version    TEXT,
    corpus_generation TEXT NOT NULL DEFAULT 'rulegen_2026_08',
    reviewed          BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by       TEXT,
    reviewed_at       TIMESTAMPTZ,
    promoted_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_law_span_verified
        CHECK (authority_type <> 'LAW' OR span_verified = TRUE),
    CONSTRAINT ck_route
        CHECK (route IN ('auto_promote','review','block'))
);
"""

_CREATE_RULES_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_rg_rules_route ON rulegen.rules (route, reviewed);
CREATE INDEX IF NOT EXISTS ix_rg_rules_section ON rulegen.rules (section_id);
"""

_CREATE_BLOCK_TRIGGER = """
CREATE OR REPLACE FUNCTION rulegen.reject_blocked_promotion()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.route = 'block' AND NEW.promoted_at IS NOT NULL THEN
        RAISE EXCEPTION 'rule % is blocked and cannot be promoted', NEW.rule_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_no_blocked_promotion ON rulegen.rules;
CREATE TRIGGER trg_no_blocked_promotion
    BEFORE INSERT OR UPDATE ON rulegen.rules
    FOR EACH ROW EXECUTE FUNCTION rulegen.reject_blocked_promotion();
"""

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS rulegen.runs (
    run_id          TEXT PRIMARY KEY,
    batch_key       TEXT,
    prompt_version  TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    sections        INT DEFAULT 0,
    rules           INT DEFAULT 0,
    model_calls     INT DEFAULT 0,
    recall          NUMERIC,
    verdict         TEXT
);
"""

_CREATE_CHANGE_RECORDS = """
CREATE TABLE IF NOT EXISTS rulegen.change_records (
    id              BIGSERIAL PRIMARY KEY,
    citation        TEXT NOT NULL,
    old_hash        CHAR(64),
    new_hash        CHAR(64),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    rules_affected  JSONB NOT NULL DEFAULT '[]'::jsonb,
    spans_surviving JSONB NOT NULL DEFAULT '[]'::jsonb,
    spans_broken    JSONB NOT NULL DEFAULT '[]'::jsonb,
    disposition     TEXT,
    dispositioned_by TEXT,
    dispositioned_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_rg_changes_pending
    ON rulegen.change_records (detected_at) WHERE disposition IS NULL;
"""

_CREATE_PROMOTE = """
CREATE OR REPLACE FUNCTION rulegen.promote(p_limit INT DEFAULT NULL)
RETURNS TABLE (promoted INT, skipped INT) AS $$
DECLARE
    v_promoted INT := 0;
    v_skipped  INT := 0;
    r RECORD;
BEGIN
    FOR r IN
        SELECT ru.*, s.content_hash, s.source_url, s.retrieved_at,
               s.effective_date, g.regulator AS reg_regulator, g.phase AS reg_phase
          FROM rulegen.rules ru
          JOIN rulegen.cfr_sections s ON s.id = ru.section_id
          LEFT JOIN rulegen.regulations g ON g.reg_id = s.reg_id
         WHERE ru.reviewed = TRUE
           AND ru.route <> 'block'
           AND ru.promoted_at IS NULL
         ORDER BY ru.cfr_citation, ru.rule_id
         LIMIT p_limit
    LOOP
        INSERT INTO public.compliance_rules (
            rule_id, regulator, category, geography, rule_text,
            threshold_amount, threshold_currency, effective_date, source_url,
            is_active, hash, version, cfr_citation, jurisdiction_level,
            authority_type, authority_source, citation_verified,
            citation_verified_at, citation_verified_source,
            source_content_hash, scraped_at, applicability_charter,
            rule_class, evidence_source, result_type, section_id,
            obligation_id, phase, corpus_generation, is_template, needs_review
        ) VALUES (
            r.rule_id,
            COALESCE(r.reg_regulator, 'unknown'),
            'bsa_aml', 'usa', r.rule_logic,
            r.threshold_value,
            CASE WHEN r.threshold_unit = 'usd' THEN 'USD' END,
            r.effective_date, r.source_url,
            (r.route = 'auto_promote'),
            r.content_hash,
            (SELECT COALESCE(current_version, 0) + 1 FROM sync_versions WHERE id = 1),
            r.cfr_citation, 'federal',
            r.authority_type, r.authority_source,
            (r.authority_type = 'LAW' AND r.span_verified),
            CASE WHEN r.authority_type = 'LAW' THEN r.retrieved_at END,
            CASE WHEN r.authority_type = 'LAW' THEN 'ecfr_section'
                 ELSE r.authority_source END,
            r.content_hash, r.retrieved_at,
            -- applicability_charter is TEXT[]. Build a text array of the
            -- charters this rule covers and strip the NULL slots. The
            -- earlier jsonb_build_array(...) - 'null'::jsonb form was
            -- wrong on two counts: no jsonb - jsonb operator exists, and
            -- the target column is text[] not jsonb.
            array_remove(ARRAY[
                CASE WHEN r.applies_bank THEN 'bank' END,
                CASE WHEN r.applies_credit_union THEN 'credit_union' END
            ]::text[], NULL),
            r.rule_class, r.evidence_source, r.result_type, r.section_id,
            r.obligation_id, COALESCE(r.reg_phase, 1), r.corpus_generation,
            (r.rule_class = 'BUSINESS'),
            (r.route <> 'auto_promote')
        )
        ON CONFLICT (rule_id, regulator, geography) DO UPDATE SET
            rule_text = EXCLUDED.rule_text,
            threshold_amount = EXCLUDED.threshold_amount,
            authority_type = EXCLUDED.authority_type,
            citation_verified = EXCLUDED.citation_verified,
            citation_verified_at = EXCLUDED.citation_verified_at,
            source_content_hash = EXCLUDED.source_content_hash,
            scraped_at = EXCLUDED.scraped_at,
            updated_at = now();

        UPDATE rulegen.rules SET promoted_at = now() WHERE rule_id = r.rule_id;
        v_promoted := v_promoted + 1;
    END LOOP;

    SELECT COUNT(*) INTO v_skipped FROM rulegen.rules
     WHERE promoted_at IS NULL AND (reviewed = FALSE OR route = 'block');

    UPDATE sync_versions SET current_version = current_version + 1,
                             updated_at = now() WHERE id = 1;

    RETURN QUERY SELECT v_promoted, v_skipped;
END $$ LANGUAGE plpgsql;
"""

_CREATE_VIEWS = """
CREATE OR REPLACE VIEW rulegen.v_review_queue AS
SELECT r.rule_id, r.cfr_citation, r.route, o.criticality, r.rule_class,
       r.rule_logic, r.evidence_source, r.source_span, r.reasons,
       CASE r.route
            WHEN 'block' THEN 0
            WHEN 'review' THEN CASE o.criticality
                 WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 ELSE 3 END
            ELSE 4 END AS priority
  FROM rulegen.rules r
  JOIN rulegen.obligations o ON o.obligation_id = r.obligation_id
 WHERE r.reviewed = FALSE
 ORDER BY priority, r.cfr_citation, r.rule_id;

CREATE OR REPLACE VIEW rulegen.v_stale_anchors AS
SELECT r.rule_id, s.citation, s.retrieved_at, s.superseded_at
  FROM rulegen.rules r
  JOIN rulegen.cfr_sections s ON s.id = r.section_id
 WHERE r.authority_type = 'LAW'
   AND (r.span_verified = FALSE
        OR s.superseded_at IS NOT NULL
        OR s.retrieved_at < now() - INTERVAL '7 days');

CREATE OR REPLACE VIEW rulegen.v_decomposition_health AS
SELECT s.citation,
       COUNT(DISTINCT o.obligation_id) AS obligations,
       COUNT(r.rule_id)                AS rules,
       ROUND(COUNT(r.rule_id)::numeric
             / NULLIF(COUNT(DISTINCT o.obligation_id), 0), 2)
                                       AS avg_rules_per_obligation
  FROM rulegen.cfr_sections s
  LEFT JOIN rulegen.obligations o ON o.section_id = s.id
  LEFT JOIN rulegen.rules r       ON r.obligation_id = o.obligation_id
 WHERE s.superseded_at IS NULL
 GROUP BY s.citation
 ORDER BY avg_rules_per_obligation NULLS LAST;
"""


def upgrade() -> None:
    # ── rulegen schema ───────────────────────────────────────────────
    op.execute(_CREATE_SCHEMA)
    op.execute(_CREATE_REGULATIONS)
    op.execute(_CREATE_CFR_SECTIONS)
    op.execute(_CREATE_SECTIONS_INDEX)
    op.execute(_CREATE_OBLIGATIONS)
    op.execute(_CREATE_RULES)
    op.execute(_CREATE_RULES_INDEXES)
    op.execute(_CREATE_BLOCK_TRIGGER)
    op.execute(_CREATE_RUNS)
    op.execute(_CREATE_CHANGE_RECORDS)
    op.execute(_CREATE_PROMOTE)
    op.execute(_CREATE_VIEWS)

    # ── additive columns on public.compliance_rules ──────────────────
    # Nullable / defaulted so existing rows survive without a backfill.
    op.add_column("compliance_rules",
                  sa.Column("rule_class", sa.String(20), nullable=True))
    op.add_column("compliance_rules",
                  sa.Column("evidence_source", sa.Text, nullable=True))
    op.add_column("compliance_rules",
                  sa.Column("result_type", sa.String(20),
                            nullable=True, server_default=sa.text("'PASS/FAIL'")))
    op.add_column("compliance_rules",
                  sa.Column("section_id", sa.dialects.postgresql.UUID(as_uuid=True),
                            nullable=True))
    op.add_column("compliance_rules",
                  sa.Column("obligation_id", sa.Text, nullable=True))
    op.add_column("compliance_rules",
                  sa.Column("phase", sa.Integer, nullable=True))
    op.add_column("compliance_rules",
                  sa.Column("corpus_generation", sa.Text, nullable=True))
    op.add_column("compliance_rules",
                  sa.Column("is_template", sa.Boolean,
                            nullable=False, server_default=sa.text("FALSE")))
    op.add_column("compliance_rules",
                  sa.Column("needs_review", sa.Boolean,
                            nullable=False, server_default=sa.text("FALSE")))

    op.create_index("ix_rules_corpus_generation", "compliance_rules",
                    ["corpus_generation"])

    # Name the existing corpus so the dashboard can tell the two apart.
    op.execute("""
        UPDATE public.compliance_rules
           SET corpus_generation = 'legacy_hardcoded'
         WHERE corpus_generation IS NULL;
    """)

    # LAW rows must carry a verified citation. NOT VALID grandfathers
    # any pre-existing rows; connector runs backfill going forward.
    # This is the mirror of 046's ck_cr_verified_implies_law
    # (which says "if verified, must be LAW"): together they lock the
    # gate on both sides.
    op.execute("""
        ALTER TABLE public.compliance_rules
            ADD CONSTRAINT ck_law_needs_verification
            CHECK (authority_type IS NULL
                   OR authority_type <> 'LAW'
                   OR citation_verified = TRUE)
            NOT VALID;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE public.compliance_rules "
               "DROP CONSTRAINT IF EXISTS ck_law_needs_verification;")
    op.drop_index("ix_rules_corpus_generation", table_name="compliance_rules")
    for col in ("needs_review", "is_template", "corpus_generation", "phase",
                "obligation_id", "section_id", "result_type",
                "evidence_source", "rule_class"):
        op.drop_column("compliance_rules", col)
    # DROP SCHEMA CASCADE removes all rulegen.* tables, indexes, function,
    # trigger, and views in one operation.
    op.execute("DROP SCHEMA IF EXISTS rulegen CASCADE;")
