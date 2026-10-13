-- rulegen ingestion schema for the Fortress database.
--
-- DESIGN DECISION: rulegen writes to its own schema, not to public.
--
-- compliance_rules currently serves the live dashboard. Writing several
-- hundred unreviewed rules into it -- even flagged is_active=false --
-- means any query that forgets the filter shows them to a customer. A
-- separate schema makes forgetting impossible, makes a bad run a
-- DROP SCHEMA rather than a delete-by-predicate, and lets the SME work
-- against staging while production is untouched.
--
-- Promotion into public.compliance_rules is a deliberate second step,
-- one reviewed rule at a time.
--
-- Additive only. Nothing in public is dropped, renamed or rewritten.
-- Safe to re-run.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rulegen;

-- ── 1. Regulation head ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rulegen.regulations (
    reg_id          TEXT PRIMARY KEY,               -- REG-31-1020
    name            TEXT NOT NULL,
    cfr_title       INT  NOT NULL,
    cfr_part        TEXT NOT NULL,
    regulator       TEXT NOT NULL,                  -- fincen|ofac|ncua|occ|fdic|frb
    authority_type  TEXT NOT NULL DEFAULT 'LAW',    -- LAW|GUIDANCE
    phase           INT,
    applies_charter JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 2. Fetched source corpus ──────────────────────────────────────────
-- The layer that does not exist in public today. ECFRSection.xml_raw is
-- currently computed and discarded, so no rule can be traced to source
-- text and no drift query is possible.
CREATE TABLE IF NOT EXISTS rulegen.cfr_sections (
    id              UUID PRIMARY KEY,
    reg_id          TEXT REFERENCES rulegen.regulations(reg_id),
    cfr_title       INT  NOT NULL,
    citation        TEXT NOT NULL,                  -- 1020.210 | 748 App A | Part 510
    kind            TEXT NOT NULL,                  -- section|appendix|chapter|part
    heading         TEXT,
    section_text    TEXT NOT NULL,                  -- verbatim, whitespace normalised
    content_hash    CHAR(64) NOT NULL,
    source_url      TEXT NOT NULL,                  -- section-scoped, machine-built
    effective_date  DATE,
    retrieved_at    TIMESTAMPTZ NOT NULL,
    superseded_at   TIMESTAMPTZ,                    -- set when text changes
    UNIQUE (cfr_title, citation, content_hash)
);

CREATE INDEX IF NOT EXISTS ix_rg_sections_current
    ON rulegen.cfr_sections (cfr_title, citation) WHERE superseded_at IS NULL;

-- ── 3. Obligations ────────────────────────────────────────────────────
-- One row per duty, NOT one per rule. If rule_count is 1.00 across the
-- board after a load, the grouping step in pipeline.extract() is broken
-- and the corpus has the 1:1 collapse.
CREATE TABLE IF NOT EXISTS rulegen.obligations (
    obligation_id     TEXT PRIMARY KEY,             -- OB-1020.210-003
    section_id        UUID NOT NULL REFERENCES rulegen.cfr_sections(id),
    reg_id            TEXT REFERENCES rulegen.regulations(reg_id),
    subsection        TEXT,                         -- (a)(2)(ii)
    obligation_text   TEXT NOT NULL,
    criticality       TEXT,                         -- Critical|High|Medium
    control_archetype TEXT,                         -- drives the dashboard view
    applies_bank      BOOLEAN NOT NULL DEFAULT TRUE,
    applies_credit_union BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 4. Rules ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rulegen.rules (
    rule_id           TEXT PRIMARY KEY,
    obligation_id     TEXT NOT NULL REFERENCES rulegen.obligations(obligation_id),
    section_id        UUID NOT NULL REFERENCES rulegen.cfr_sections(id),
    rule_class        TEXT NOT NULL,                -- REGULATORY|BUSINESS|TECHNICAL
    rule_logic        TEXT NOT NULL,
    evidence_source   TEXT NOT NULL,                -- bank artifact, controlled vocab
    result_type       TEXT NOT NULL DEFAULT 'PASS/FAIL',  -- + INDETERMINATE
    threshold_value   NUMERIC,
    threshold_unit    TEXT,                         -- usd|percent|calendar_days|...
    authority_type    TEXT NOT NULL,                -- LAW|GUIDANCE
    authority_source  TEXT,
    source_span       TEXT,                         -- verbatim quote from section_text
    span_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    cfr_citation      TEXT NOT NULL,
    applies_bank      BOOLEAN NOT NULL DEFAULT TRUE,
    applies_credit_union BOOLEAN NOT NULL DEFAULT TRUE,
    confidence        NUMERIC,
    route             TEXT NOT NULL,                -- auto_promote|review|block
    reasons           JSONB NOT NULL DEFAULT '[]'::jsonb,
    prompt_version    TEXT,                         -- which prompt produced this
    corpus_generation TEXT NOT NULL DEFAULT 'rulegen_2026_08',
    reviewed          BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by       TEXT,
    reviewed_at       TIMESTAMPTZ,
    promoted_at       TIMESTAMPTZ,                  -- when it reached public
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A LAW rule with no verified span is ungrounded. Make it impossible
    -- to insert rather than something to catch in review.
    CONSTRAINT ck_law_span_verified
        CHECK (authority_type <> 'LAW' OR span_verified = TRUE),
    CONSTRAINT ck_route
        CHECK (route IN ('auto_promote','review','block'))
);

CREATE INDEX IF NOT EXISTS ix_rg_rules_route ON rulegen.rules (route, reviewed);
CREATE INDEX IF NOT EXISTS ix_rg_rules_section ON rulegen.rules (section_id);

-- Blocked rules must never reach production. Belt and braces alongside
-- the promote function.
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

-- ── 5. Run and change tracking ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rulegen.runs (
    run_id          TEXT PRIMARY KEY,
    batch_key       TEXT,
    prompt_version  TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    sections        INT DEFAULT 0,
    rules           INT DEFAULT 0,
    model_calls     INT DEFAULT 0,
    recall          NUMERIC,                        -- against the golden set
    verdict         TEXT                            -- promote|hold
);

CREATE TABLE IF NOT EXISTS rulegen.change_records (
    id              BIGSERIAL PRIMARY KEY,
    citation        TEXT NOT NULL,
    old_hash        CHAR(64),
    new_hash        CHAR(64),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    rules_affected  JSONB NOT NULL DEFAULT '[]'::jsonb,
    spans_surviving JSONB NOT NULL DEFAULT '[]'::jsonb,
    spans_broken    JSONB NOT NULL DEFAULT '[]'::jsonb,
    disposition     TEXT,                           -- pending|confirmed|amended|obsolete
    dispositioned_by TEXT,
    dispositioned_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_rg_changes_pending
    ON rulegen.change_records (detected_at) WHERE disposition IS NULL;

-- ── 6. Additive changes to public.compliance_rules ────────────────────
-- Only what promotion needs. No existing column is touched.
ALTER TABLE public.compliance_rules
    ADD COLUMN IF NOT EXISTS rule_class        VARCHAR(20),
    ADD COLUMN IF NOT EXISTS evidence_source   TEXT,
    ADD COLUMN IF NOT EXISTS result_type       VARCHAR(20) DEFAULT 'PASS/FAIL',
    ADD COLUMN IF NOT EXISTS section_id        UUID,
    ADD COLUMN IF NOT EXISTS obligation_id     TEXT,
    ADD COLUMN IF NOT EXISTS phase             INT,
    ADD COLUMN IF NOT EXISTS corpus_generation TEXT,
    ADD COLUMN IF NOT EXISTS is_template       BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS needs_review      BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_rules_corpus_generation
    ON public.compliance_rules (corpus_generation);

-- Name the existing corpus so the dashboard can tell the two apart.
UPDATE public.compliance_rules
   SET corpus_generation = 'legacy_hardcoded'
 WHERE corpus_generation IS NULL;

-- NOT VALID: applies to new rows, grandfathers the existing corpus.
-- Run VALIDATE CONSTRAINT once legacy rows are retired.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_law_needs_verification') THEN
        ALTER TABLE public.compliance_rules
            ADD CONSTRAINT ck_law_needs_verification
            CHECK (authority_type <> 'LAW' OR citation_verified = TRUE)
            NOT VALID;
    END IF;
END $$;

-- ── 7. Promotion ──────────────────────────────────────────────────────
-- Copies reviewed, non-blocked rules into public.compliance_rules with
-- full provenance. Idempotent on (rule_id, regulator, geography).
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
            -- Only fully clean rules go live. Reviewed-but-flagged rules
            -- land inactive so a human can see them in context.
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
            jsonb_build_array(
                CASE WHEN r.applies_bank THEN 'bank' END,
                CASE WHEN r.applies_credit_union THEN 'credit_union' END
            ) - 'null'::jsonb,
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

-- ── 8. Health views ───────────────────────────────────────────────────
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

-- Rules asserting law whose source anchor is stale, changed or unverified.
-- This query is the exam-readiness signal and cannot be written today.
CREATE OR REPLACE VIEW rulegen.v_stale_anchors AS
SELECT r.rule_id, s.citation, s.retrieved_at, s.superseded_at
  FROM rulegen.rules r
  JOIN rulegen.cfr_sections s ON s.id = r.section_id
 WHERE r.authority_type = 'LAW'
   AND (r.span_verified = FALSE
        OR s.superseded_at IS NOT NULL
        OR s.retrieved_at < now() - INTERVAL '7 days');

-- Catches the 1:1 obligation collapse. If avg_rules_per_obligation is
-- 1.00, pipeline.extract() is not grouping and the load is wrong.
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

COMMIT;

-- ── Rollback ──────────────────────────────────────────────────────────
-- Staging:    DROP SCHEMA rulegen CASCADE;
-- Production: UPDATE public.compliance_rules SET is_active = false
--               WHERE corpus_generation = 'rulegen_2026_08';
--             UPDATE public.compliance_rules SET is_active = true
--               WHERE corpus_generation = 'legacy_hardcoded';
