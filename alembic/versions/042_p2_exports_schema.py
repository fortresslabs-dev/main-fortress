"""P2 atomic schema for Examination Evidence & Reports (formerly M016p2–029p2).

Revision ID: 042
Revises: 041
Create Date: 2026-06-21

Lands 14 logical tables in one atomic migration per design doc §11.1 Q2
(one PR, single ``alembic downgrade -1`` for rollback). The original
plan called for one migration per table; collapsing to one upgrade()
keeps the artifact, signoff, and access-log chain consistent at all
times (the FK graph would otherwise break mid-upgrade).

Table inventory (grouped by report dependency):

  Cover-block facts
    * bank_profile             — institution facts (charter, regulator,
                                  total assets, fiscal year end)
    * bank_officers            — named-role roster with end-date history
    * vocab_versions           — frozen disposition/finding vocab (Q18)

  Control catalogue + test evidence (Reports 1 & 6)
    * controls                 — bank-scoped control list with owner
    * control_rule_mappings    — control -> P1 rule (append-only mapping)
    * control_tests            — per-test threshold + GitHub/JIRA refs
    * test_executions          — every run's result with evidence S3 URI

  Findings / MRA tracker (Report 4)
    * findings                 — OCC 5 Cs + repeat-flag taxonomy
    * finding_history          — append-only change log per OCC 2012-7

  Change disposition (Report 5)
    * change_record_dispositions — per-bank disposition of P1 change refs

  OFAC independent testing (Report 2 §6)
    * ofac_validations         — Pillar-4 validation log

  Artifact store (cross-cutting)
    * export_artifacts         — WORM contract + content hash + S3 key
    * export_signoffs          — e-sig audit chain (per Q9 matrix)
    * export_access_log        — every download tracked (FFIEC IT Hdbk)

Banking-compliance posture (per design doc §9):
  * vocabulary CHECK constraints lock PASS/REVIEW/HOLD, OCC 5 Cs status,
    disposition vocab — render-time enums also enforce in Python
  * Append-only patterns (control_rule_mappings, finding_history,
    bank_officers.ended_at instead of DELETE)
  * UNIQUE (bank_id, type, as_of, version_hash) on export_artifacts is
    the idempotency key — re-running same as-of returns same row

Rollback (per feedback_p1_alembic_blocks_rollback):
    python -m alembic stamp 041
"""
from alembic import op


revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ENUMs ────────────────────────────────────────────────────────────
    # All wrapped in DO $$ ... $$ so re-runs against partially-migrated
    # databases don't fail.
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE officer_role_t AS ENUM
                ('bsa_officer','cco','cro','internal_audit',
                 'audit_committee_chair','board_chair','ceo','other');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE test_result_t AS ENUM
                ('PASS','PARTIAL','FAIL','NOT_TESTED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE finding_type_t AS ENUM
                ('MRA','MRIA','MRBA','VIOLATION','OBSERVATION');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE finding_severity_t AS ENUM ('high','medium','low');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE finding_status_t AS ENUM
                ('open','past_due','pending_validation','validated_closed');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE change_disposition_t AS ENUM
                ('acknowledged','requires_action','not_applicable');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE signature_method_t AS ENUM
                ('sso_session','jwt_jti','manual_upload');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)

    # ── vocab_versions (Q18) ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS vocab_versions (
            version_id    VARCHAR(32) PRIMARY KEY,
            frozen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            vocab_json    JSONB        NOT NULL,
            description   TEXT
        )
    """)

    # ── bank_profile ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS bank_profile (
            bank_id              VARCHAR(80) PRIMARY KEY,
            legal_name           TEXT         NOT NULL,
            charter_number       VARCHAR(32),
            primary_regulator    VARCHAR(32)  NOT NULL,
            secondary_regulators JSONB        NOT NULL DEFAULT '[]'::jsonb,
            total_assets_usd     NUMERIC(20,2),
            total_assets_as_of   DATE,
            fiscal_year_end      DATE         NOT NULL,
            address              JSONB,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)

    # ── bank_officers ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS bank_officers (
            person_id     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            bank_id       VARCHAR(80)  NOT NULL REFERENCES bank_profile(bank_id) ON DELETE RESTRICT,
            full_name     TEXT         NOT NULL,
            role          officer_role_t NOT NULL,
            email         TEXT,
            started_at    DATE         NOT NULL,
            ended_at      DATE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_bank_officers_bank_role_active
        ON bank_officers (bank_id, role)
        WHERE ended_at IS NULL
    """)

    # ── controls + mappings + tests + executions ─────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS controls (
            control_id        VARCHAR(80) PRIMARY KEY,
            bank_id           VARCHAR(80) NOT NULL REFERENCES bank_profile(bank_id) ON DELETE RESTRICT,
            name              TEXT        NOT NULL,
            owner_role        officer_role_t NOT NULL,
            owner_person_id   UUID        REFERENCES bank_officers(person_id) ON DELETE SET NULL,
            description       TEXT,
            control_type      VARCHAR(40),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_controls_bank ON controls (bank_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS control_rule_mappings (
            id              BIGSERIAL PRIMARY KEY,
            bank_id         VARCHAR(80) NOT NULL,
            control_id      VARCHAR(80) NOT NULL REFERENCES controls(control_id) ON DELETE CASCADE,
            rule_id         VARCHAR(100) NOT NULL,
            mapping_strength VARCHAR(10) NOT NULL DEFAULT 'full',
            created_by      VARCHAR(120),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_at   TIMESTAMPTZ,
            CHECK (mapping_strength IN ('full','partial'))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_crm_active
        ON control_rule_mappings (bank_id, control_id, rule_id)
        WHERE superseded_at IS NULL
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS control_tests (
            test_id                  VARCHAR(80) PRIMARY KEY,
            bank_id                  VARCHAR(80) NOT NULL,
            control_id               VARCHAR(80) NOT NULL REFERENCES controls(control_id) ON DELETE CASCADE,
            framework                VARCHAR(40) NOT NULL DEFAULT 'pytest',
            threshold_value          NUMERIC(20,4),
            threshold_unit           VARCHAR(40),
            threshold_rationale      TEXT,
            threshold_source_rule_id VARCHAR(100),
            jira_story_key           VARCHAR(50),
            github_pr_url            TEXT,
            commit_hash              VARCHAR(64),
            code_path                TEXT,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_control_tests_bank ON control_tests (bank_id, control_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS test_executions (
            execution_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bank_id           VARCHAR(80) NOT NULL,
            test_id           VARCHAR(80) NOT NULL REFERENCES control_tests(test_id) ON DELETE RESTRICT,
            executed_at       TIMESTAMPTZ NOT NULL,
            result            test_result_t NOT NULL,
            evidence_s3_uri   TEXT,
            executor          VARCHAR(120),
            run_log_s3_uri    TEXT,
            exception_text    TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_test_exec_bank_test_at ON test_executions (bank_id, test_id, executed_at DESC)")

    # ── findings + history (Report 4 — OCC 5 Cs) ─────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            finding_id              VARCHAR(80) PRIMARY KEY,
            bank_id                 VARCHAR(80) NOT NULL,
            type                    finding_type_t NOT NULL,
            severity                finding_severity_t NOT NULL,
            regulatory_source       TEXT,
            -- 5 Cs columns (locked vocab)
            concern                 TEXT NOT NULL,
            cause                   TEXT,
            consequence             TEXT,
            corrective_action       TEXT,
            commitment              TEXT,
            -- root-cause analytics
            root_cause              TEXT,
            root_cause_identified   BOOLEAN NOT NULL DEFAULT FALSE,
            owner_person_id         UUID REFERENCES bank_officers(person_id) ON DELETE SET NULL,
            identified_date         DATE NOT NULL,
            due_date                DATE,
            status                  finding_status_t NOT NULL DEFAULT 'open',
            evidence_s3_uri         TEXT,
            validation_notes        TEXT,
            repeat_flag             BOOLEAN NOT NULL DEFAULT FALSE,
            repeat_of_finding_id    VARCHAR(80) REFERENCES findings(finding_id) ON DELETE SET NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_findings_bank_status ON findings (bank_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_findings_due ON findings (bank_id, due_date) WHERE status IN ('open','past_due')")

    op.execute("""
        CREATE TABLE IF NOT EXISTS finding_history (
            id              BIGSERIAL PRIMARY KEY,
            finding_id      VARCHAR(80) NOT NULL REFERENCES findings(finding_id) ON DELETE CASCADE,
            changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            changed_by      VARCHAR(120) NOT NULL,
            field           VARCHAR(64)  NOT NULL,
            old_value       TEXT,
            new_value       TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_finding_history_fid ON finding_history (finding_id, changed_at DESC)")

    # ── change_record_dispositions (Report 5) ────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS change_record_dispositions (
            id                  BIGSERIAL PRIMARY KEY,
            change_ref          VARCHAR(120) NOT NULL,
            bank_id             VARCHAR(80)  NOT NULL,
            disposition         change_disposition_t NOT NULL,
            owner_person_id     UUID REFERENCES bank_officers(person_id) ON DELETE SET NULL,
            impl_link           TEXT,
            status              VARCHAR(40) NOT NULL DEFAULT 'open',
            dispositioned_at    TIMESTAMPTZ,
            dispositioned_by    VARCHAR(120),
            vocab_version       VARCHAR(32),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (bank_id, change_ref)
        )
    """)

    # ── ofac_validations (Report 2 §6) ───────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ofac_validations (
            validation_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bank_id                VARCHAR(80) NOT NULL,
            performed_on           DATE NOT NULL,
            performed_by_person_id UUID REFERENCES bank_officers(person_id) ON DELETE SET NULL,
            scope                  TEXT NOT NULL,
            methodology            TEXT,
            findings_summary       TEXT,
            next_due               DATE,
            attestation_s3_uri     TEXT,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ofac_val_bank_perf ON ofac_validations (bank_id, performed_on DESC)")

    # ── export_artifacts (WORM contract) ─────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS export_artifacts (
            artifact_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bank_id                     VARCHAR(80) NOT NULL,
            type                        VARCHAR(40) NOT NULL,
            as_of                       DATE        NOT NULL,
            format                      VARCHAR(8)  NOT NULL,
            version_hash                CHAR(7)     NOT NULL,
            s3_bucket                   VARCHAR(200) NOT NULL,
            s3_key                      TEXT         NOT NULL,
            s3_etag                     VARCHAR(80),
            s3_version_id               VARCHAR(120),
            kms_key_arn                 TEXT,
            byte_size                   BIGINT,
            sha256                      CHAR(64)    NOT NULL,
            generated_by                VARCHAR(120) NOT NULL,
            generated_at                TIMESTAMPTZ  NOT NULL DEFAULT now(),
            object_lock_retain_until    TIMESTAMPTZ  NOT NULL,
            snapshot_canonical_sha256   CHAR(64)    NOT NULL,
            source_data_uri             TEXT,
            template_version            VARCHAR(32) NOT NULL,
            vocab_version               VARCHAR(32) NOT NULL,
            app_commit                  VARCHAR(40) NOT NULL,
            UNIQUE (bank_id, type, as_of, version_hash),
            CHECK (format IN ('pdf','csv','json')),
            CHECK (type IN ('examiner_package','ofac_attestation','board_report',
                            'mra_tracker','change_log','coverage'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_bank_type_asof ON export_artifacts (bank_id, type, as_of DESC)")

    # ── export_signoffs (per Q9 matrix) ──────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS export_signoffs (
            signoff_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            artifact_id       UUID NOT NULL REFERENCES export_artifacts(artifact_id) ON DELETE RESTRICT,
            signer_person_id  UUID NOT NULL REFERENCES bank_officers(person_id) ON DELETE RESTRICT,
            signer_role       officer_role_t NOT NULL,
            signed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            signature_method  signature_method_t NOT NULL,
            ip_address        INET,
            user_agent        TEXT,
            jwt_jti           VARCHAR(120),
            typed_full_name   TEXT,
            acknowledgement   TEXT,
            UNIQUE (artifact_id, signer_person_id, signer_role)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_signoffs_artifact ON export_signoffs (artifact_id)")

    # ── export_access_log (FFIEC IT Handbook §IV) ────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS export_access_log (
            access_id     BIGSERIAL PRIMARY KEY,
            artifact_id   UUID NOT NULL REFERENCES export_artifacts(artifact_id) ON DELETE RESTRICT,
            accessed_by   VARCHAR(120) NOT NULL,
            accessed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            ip_address    INET,
            jwt_jti       VARCHAR(120),
            http_status   INT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_access_log_artifact_at ON export_access_log (artifact_id, accessed_at DESC)")


def downgrade() -> None:
    # Drop in reverse dependency order — children first.
    op.execute("DROP TABLE IF EXISTS export_access_log")
    op.execute("DROP TABLE IF EXISTS export_signoffs")
    op.execute("DROP TABLE IF EXISTS export_artifacts")
    op.execute("DROP TABLE IF EXISTS ofac_validations")
    op.execute("DROP TABLE IF EXISTS change_record_dispositions")
    op.execute("DROP TABLE IF EXISTS finding_history")
    op.execute("DROP TABLE IF EXISTS findings")
    op.execute("DROP TABLE IF EXISTS test_executions")
    op.execute("DROP TABLE IF EXISTS control_tests")
    op.execute("DROP TABLE IF EXISTS control_rule_mappings")
    op.execute("DROP TABLE IF EXISTS controls")
    op.execute("DROP TABLE IF EXISTS bank_officers")
    op.execute("DROP TABLE IF EXISTS bank_profile")
    op.execute("DROP TABLE IF EXISTS vocab_versions")
    # Enums drop last — nothing references them now.
    op.execute("DROP TYPE IF EXISTS signature_method_t")
    op.execute("DROP TYPE IF EXISTS change_disposition_t")
    op.execute("DROP TYPE IF EXISTS finding_status_t")
    op.execute("DROP TYPE IF EXISTS finding_severity_t")
    op.execute("DROP TYPE IF EXISTS finding_type_t")
    op.execute("DROP TYPE IF EXISTS test_result_t")
    op.execute("DROP TYPE IF EXISTS officer_role_t")
