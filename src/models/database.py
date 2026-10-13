"""Database Models"""
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, JSON, Boolean, ForeignKey, Enum, Index, UniqueConstraint, BigInteger, Date, Numeric
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
import uuid

Base = declarative_base()

class WorkflowStatus(PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"

class CheckpointStatus(PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jira_story_id = Column(String(50), nullable=False, index=True)
    jira_story_key = Column(String(50), nullable=False)
    rule_id = Column(String(100), nullable=True, index=True)
    # PG enum was created (migration 016) with the lowercase VALUES
    # ('pending','running',...) so SQLAlchemy must round-trip by VALUE,
    # not by NAME (its default).
    status = Column(
        Enum(
            WorkflowStatus,
            name="workflowstatus",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            create_type=False,
        ),
        default=WorkflowStatus.PENDING,
    )

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    context_data = Column(JSON, default=dict)
    artifacts = Column(JSON, default=dict)
    
    checkpoints = relationship("CheckpointApproval", back_populates="workflow")

class CheckpointApproval(Base):
    __tablename__ = "checkpoint_approvals"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflow_executions.id"))
    
    checkpoint_name = Column(String(50), nullable=False)
    # Mirror the workflowstatus fix: PG enum holds lowercase values.
    status = Column(
        Enum(
            CheckpointStatus,
            name="checkpointstatus",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            create_type=False,
        ),
        default=CheckpointStatus.PENDING,
    )
    
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    
    artifacts = Column(JSON, default=dict)
    feedback = Column(JSON, default=dict)
    
    workflow = relationship("WorkflowExecution", back_populates="checkpoints")


# =====================================================================
# PIPELINE 1 MODELS — Compliance Rules (Fortress Cloud)
# =====================================================================

class ComplianceRule(Base):
    """Public regulatory rules served to customer agents (read-only API)."""
    __tablename__ = "compliance_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(String(100), nullable=False, index=True)
    regulator = Column(String(50), nullable=False, index=True)     # occ, cfpb, fincen, ofac, etc.
    category = Column(String(100), nullable=False)                  # bsa_aml, section_1071, etc.
    geography = Column(String(10), nullable=False, default="usa")   # usa, uk, etc.
    domain = Column(String(100), nullable=True)                     # wire_transfer, loan, etc.
    rule_type = Column(String(50), nullable=True)                   # dual_approval, ctr_threshold, etc.
    rule_text = Column(Text, nullable=False)
    threshold_amount = Column(Float, nullable=True)
    threshold_currency = Column(String(10), nullable=True, default="USD")
    field_name = Column(String(100), nullable=True)                 # for required_field rules
    effective_date = Column(DateTime, nullable=True)
    source_url = Column(Text, nullable=True)
    test_template = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    hash = Column(String(64), nullable=True)                    # SHA-256 for change detection
    version = Column(BigInteger, nullable=True, index=True)     # Global sync version when last written
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Enrichment columns (migration 013)
    cfr_citation = Column(Text, nullable=True)
    state_code = Column(String(20), nullable=True)  # migration 014 widened 10→20 for compound state codes
    jurisdiction_level = Column(String(20), nullable=True)      # federal | state
    enforcement_risk_score = Column(Float, nullable=True)
    previous_content = Column(Text, nullable=True)
    change_delta = Column(JSONB, nullable=True)
    changed_obligations = Column(JSONB, nullable=True)
    change_type = Column(String(20), nullable=True)             # added|modified|removed|clarified
    obligation_count = Column(Integer, nullable=True, default=0)
    # Editorial severity grade from the curated obligation map (migration 051).
    # Distinct from enforcement_risk_score (computed 0–1): this is the source
    # Critical|High|Medium|Low classification, surfaced on the CCO dashboard.
    severity = Column(String(20), nullable=True, index=True)    # Critical|High|Medium|Low
    # Authority + provenance (migration 046). See docs/correction_smb.md
    # §11 and src/services/authority_gate.py. authority_type is the
    # class-boundary field: LAW | STATUTE | GUIDANCE | INTERNAL_CONTROL.
    authority_type = Column(String(20), nullable=True, index=True)
    authority_source = Column(Text, nullable=True)
    citation_verified = Column(Boolean, nullable=False, default=False)
    citation_verified_at = Column(DateTime(timezone=True), nullable=True)
    # Discriminator for the single-boolean gate (migration 047). Records
    # *which* verifier flipped citation_verified — e.g. "ecfr_section",
    # "ecfr_appendix", "ecfr_chapter", "usc_lii", "nydfs_500". Without
    # this, a LAW rule cited against a non-eCFR authority has no way to
    # signal how it was verified and silently degrades to advisory. See
    # docs/correction_smb.md §11 (Bug 3 verification report).
    citation_verified_source = Column(String(40), nullable=True)
    source_content_hash = Column(String(64), nullable=True)
    scraped_at = Column(DateTime(timezone=True), nullable=True)
    publication_date = Column(Date, nullable=True)
    comment_deadline = Column(Date, nullable=True)
    federal_register_doc = Column(Text, nullable=True)
    rin = Column(String(40), nullable=True)
    applicability_asset_min = Column(Numeric, nullable=True)
    applicability_asset_max = Column(Numeric, nullable=True)
    # text[] in the DB (migration 046 created these as ARRAY(Text)); the ORM
    # previously mis-declared them JSONB, which made the P2 sync insert bind
    # them as jsonb and fail with DatatypeMismatchError against the text[]
    # column. Match the DB.
    applicability_charter = Column(ARRAY(Text), nullable=True)
    applicability_activities = Column(ARRAY(Text), nullable=True)
    applicability_states = Column(ARRAY(Text), nullable=True)
    is_exemption = Column(Boolean, nullable=False, default=False)
    verify_before_display = Column(Boolean, nullable=False, default=False)
    bank_configurable_threshold = Column(Boolean, nullable=False, default=False)
    # Stale-embedding tracker (migration 019). embedding (pgvector) + tsv
    # (tsvector) columns exist in the DB but are intentionally kept out of the
    # ORM to avoid hard-importing the pgvector extension type at model load.
    embed_updated_at = Column(DateTime(timezone=True), nullable=True)
    # Threshold provenance (migration 039)
    threshold_default = Column(JSONB, nullable=True)
    threshold_rationale = Column(Text, nullable=True)
    threshold_source_citation = Column(Text, nullable=True)
    # Rulegen staging / promotion columns (migration 048). Populated by
    # rulegen.promote(); legacy rows carry corpus_generation='legacy_hardcoded'.
    rule_class = Column(String(20), nullable=True)
    evidence_source = Column(Text, nullable=True)
    result_type = Column(String(20), nullable=True, server_default="PASS/FAIL")
    section_id = Column(UUID(as_uuid=True), nullable=True)
    phase = Column(Integer, nullable=True)
    corpus_generation = Column(Text, nullable=True)
    is_template = Column(Boolean, nullable=False, default=False)
    needs_review = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("rule_id", "regulator", "geography", name="uq_rule_regulator_geo"),
        Index("ix_rules_geo_reg_domain", "geography", "regulator", "domain"),
        Index("ix_rules_version", "version"),
    )


class OFACSDNEntry(Base):
    """OFAC Specially Designated Nationals list (public data)."""
    __tablename__ = "ofac_sdn_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sdn_number = Column(String(50), nullable=False, unique=True, index=True)
    entity_name = Column(String(500), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True)      # individual, entity, vessel, aircraft
    programs = Column(JSON, default=list)                 # ["SDGT", "IRAN"]
    aliases = Column(JSON, default=list)
    addresses = Column(JSON, default=list)
    id_numbers = Column(JSON, default=list)
    remarks = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    source_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    country     = Column(String(100), nullable=True)       # ADD — referenced in local_compliance_provider.py:96
    nationality = Column(String(100), nullable=True)       # ADD


class OfacSdnVector(Base):
    """One row per SDN name variant (primary + each alias) with pgvector + tsvector.

    Separate from ofac_sdn_entries so the screener can hit a single denormalised
    table at query time. is_active + missed_pulls + last_seen_at drive the
    N-strikes soft-deactivation described in docs/runbooks/agent-2-pipeline1.md
    (never hard-delete — a single bad scrape must not unblock a real SDN).
    """
    __tablename__ = "ofac_sdn_vectors"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sdn_number = Column(String(50), nullable=False, index=True)
    name_variant = Column(Text, nullable=False)              # raw alias / primary
    name_normalized = Column(Text, nullable=False)           # uppercased, unaccented, punct-stripped
    is_primary = Column(Boolean, nullable=False, default=False)

    entity_type = Column(String(50), nullable=True)
    programs = Column(JSON, default=list)
    country = Column(String(100), nullable=True)
    nationality = Column(String(100), nullable=True)

    # embedding + tsv populated by triggers / embed job. Both columns are
    # declared as raw SQL types via `column_property` in migration 021 — we
    # leave them out of the ORM here to avoid hard-importing pgvector at
    # module load (it would break P2 envs that don't ship the extension).

    # Lifecycle / safety columns (see review §1, §3)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    missed_pulls = Column(Integer, nullable=False, default=0)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    deactivated_at = Column(DateTime(timezone=True), nullable=True)
    embedded_at = Column(DateTime(timezone=True), nullable=True)
    source_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("sdn_number", "name_normalized", name="uq_sdn_vectors_sdn_norm"),
    )


class ComplianceRuleChange(Base):
    """Tracks changes to compliance rules (delta sync for customer caching)."""
    __tablename__ = "compliance_rule_changes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(String(100), nullable=False, index=True)
    change_type = Column(String(20), nullable=False)       # added, modified, removed
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow, index=True)


# =====================================================================
# PIPELINE 2 MODELS — Validation Audit Logs (Customer Docker)
# =====================================================================

class TransactionValidation(Base):
    """Audit log for wire transfer / loan validations (stays in customer DB)."""
    __tablename__ = "transaction_validations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_type = Column(String(50), nullable=False)    # wire_transfer, loan_application
    action = Column(String(20), nullable=False)             # allow, block, review
    violations_count = Column(Integer, default=0)
    violations = Column(JSON, default=list)
    request_metadata = Column(JSON, default=dict)           # non-PII metadata only
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_validations_type_action", "validation_type", "action"),
    )


class OFACScreening(Base):
    """Audit log for OFAC screenings (stays in customer DB)."""
    __tablename__ = "ofac_screenings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action = Column(String(20), nullable=False)             # clear, block
    match_count = Column(Integer, default=0)
    matched_entries = Column(JSON, default=list)             # SDN numbers only, no customer names
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# =====================================================================
# SDN SCREENING AUDIT (migration 028) — Pipeline 2 only
# =====================================================================

class ScreeningEvent(Base):
    """Immutable audit row per OFACScreener.screen() call.

    Holds the criteria that were screened (PII), the engine response in
    full, and the actor that initiated. Append-only by convention; the
    underlying check constraint on `decision` enforces PASS|REVIEW|HOLD.
    """
    __tablename__ = "screening_events"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    screening_id         = Column(String(64), nullable=False, unique=True)
    request_kind         = Column(String(32), nullable=False)
    request_group_id     = Column(String(128), nullable=True)
    external_customer_id = Column(String(128), nullable=True)
    party_role           = Column(String(32), nullable=True)
    party_name           = Column(Text, nullable=False)
    party_country        = Column(String(8), nullable=True)
    criteria             = Column(JSONB, nullable=False)
    criteria_hash        = Column(String(64), nullable=False)
    decision             = Column(String(8), nullable=False)   # PASS|REVIEW|HOLD
    final_score          = Column(Numeric(5, 4), nullable=False, default=0)
    match_count          = Column(Integer, nullable=False, default=0)
    engine_mode          = Column(String(16), nullable=False)
    engine_response      = Column(JSONB, nullable=False)
    sdn_data_age_hours   = Column(Numeric(7, 2), nullable=True)
    latency_ms           = Column(Numeric(8, 2), nullable=False, default=0)
    actor_kind           = Column(String(16), nullable=False)
    actor_user_id        = Column(String(128), nullable=True)
    created_at           = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ScreeningCase(Base):
    """Workflow row, opened when an event's decision is REVIEW or HOLD."""
    __tablename__ = "screening_cases"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id              = Column(String(64), nullable=False, unique=True)
    external_customer_id = Column(String(128), nullable=True)
    sdn_number           = Column(String(50), nullable=True)
    matched_name         = Column(Text, nullable=True)
    status               = Column(String(16), nullable=False, default="open")
    priority             = Column(String(8), nullable=False)
    decision_at_open     = Column(String(8), nullable=False)
    assignee_user_id     = Column(String(128), nullable=True)
    first_event_id       = Column(UUID(as_uuid=True), ForeignKey("screening_events.id"), nullable=False)
    last_event_id        = Column(UUID(as_uuid=True), ForeignKey("screening_events.id"), nullable=False)
    event_count          = Column(Integer, nullable=False, default=1)
    reviewer_action      = Column(String(32), nullable=True)
    reviewer_notes       = Column(Text, nullable=True)
    reviewer_evidence    = Column(JSONB, nullable=True)
    resolved_at          = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id  = Column(String(128), nullable=True)
    # D3 — primary screening rule that opened this case (FUZZY or OWN50).
    # Nullable for legacy rows and any future code path that opens a case
    # without explicit attribution. FK SET NULL so retiring a rule from
    # screening_rules doesn't orphan years of audit-trail cases.
    matched_rule_id      = Column(String(64), ForeignKey("screening_rules.rule_id", ondelete="SET NULL"), nullable=True)
    created_at           = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at           = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ScreeningRule(Base):
    """DB-backed inventory for OFAC screening rules (migration 034).

    Source of truth for /api/v1/dashboard/screening/rules-perf. Replaces
    the hardcoded _RULES_INVENTORY list; compliance can add, rename,
    reweight, or deactivate rules without a code deploy.
    """
    __tablename__ = "screening_rules"

    rule_id     = Column(String(64), primary_key=True)
    name        = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    weight      = Column(Numeric(4, 2), nullable=False, default=1.0)
    version     = Column(Integer, nullable=False, default=1)
    is_active   = Column(Boolean, nullable=False, default=True)
    updated_by  = Column(String(128), nullable=True)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ScreeningCaseEvent(Base):
    """Join — multiple events tie to a single case (initial + re-hits)."""
    __tablename__ = "screening_case_events"

    case_id   = Column(UUID(as_uuid=True), ForeignKey("screening_cases.id", ondelete="CASCADE"), primary_key=True)
    event_id  = Column(UUID(as_uuid=True), ForeignKey("screening_events.id"), primary_key=True)
    linked_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# =====================================================================
# SYNC MODELS — Master-Replica Architecture (Option 3)
# =====================================================================

class SyncVersion(Base):
    """Global monotonic version counter (Pipeline 1 — Fortress Cloud).
    One row, incremented each time scrapers write new data."""
    __tablename__ = "sync_versions"

    id = Column(Integer, primary_key=True, default=1)
    current_version = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DeletedRule(Base):
    """Tombstone table for deleted rules (Pipeline 1).
    Allows sync API to tell customers which rules were removed."""
    __tablename__ = "deleted_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(String(100), nullable=False, index=True)
    regulator = Column(String(50), nullable=True)
    geography = Column(String(10), nullable=True)
    deleted_at_version = Column(BigInteger, nullable=False, index=True)
    deleted_at = Column(DateTime, default=datetime.utcnow)


class SyncMetadata(Base):
    """Per-customer sync tracking (Pipeline 1 — Fortress Cloud).
    Records what version each customer last synced to."""
    __tablename__ = "sync_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(100), nullable=False, unique=True, index=True)
    last_sync_version = Column(BigInteger, nullable=False, default=0)
    last_sync_timestamp = Column(DateTime, nullable=True)
    sync_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LocalSyncStatus(Base):
    """Sync status log (Pipeline 2 — Customer Docker).
    Tracks each sync operation result."""
    __tablename__ = "local_sync_status"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_version = Column(BigInteger, nullable=False)
    sync_timestamp = Column(DateTime, default=datetime.utcnow)
    rules_added = Column(Integer, default=0)
    rules_updated = Column(Integer, default=0)
    rules_deleted = Column(Integer, default=0)
    sync_status = Column(String(20), nullable=False, default="SUCCESS")  # SUCCESS, PARTIAL, FAILED
    error_message = Column(Text, nullable=True)
    sync_type = Column(String(20), nullable=False, default="incremental")  # initial, incremental


# =====================================================================
# PIPELINE 1 ENRICHMENT MODELS (migrations 008–012)
# =====================================================================

class Obligation(Base):
    """Discrete compliance obligations extracted from scraped documents (REQ-010)."""
    __tablename__ = "obligations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    obligation_id = Column(String(200), nullable=False, unique=True)
    regulation_id = Column(String(100), nullable=False, index=True)
    regulator = Column(String(50), nullable=False)
    category = Column(String(100), nullable=False)
    source_text = Column(Text, nullable=False)
    obligation_type = Column(String(20), nullable=False)   # shall|must|prohibited|required|may_not
    section_reference = Column(Text, nullable=True)
    cfr_citation = Column(Text, nullable=True)
    doc_id = Column(String(100), nullable=True)
    has_test_coverage = Column(Boolean, nullable=False, default=False)
    coverage_updated_at = Column(DateTime, nullable=True)
    test_scenario_ids = Column(JSONB, default=list)
    extracted_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)


class CoverageSnapshot(Base):
    """Daily coverage percentage snapshots per regulator (REQ-010)."""
    __tablename__ = "coverage_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_date = Column(Date, nullable=False)
    regulator = Column(String(50), nullable=False)
    category = Column(String(100), nullable=True)
    total_obligations = Column(Integer, nullable=False, default=0)
    covered_obligations = Column(Integer, nullable=False, default=0)
    coverage_pct = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("snapshot_date", "regulator", "category", name="uq_snapshot_date_reg_cat"),
    )


class EnforcementAction(Base):
    """Regulatory enforcement actions (REQ-011)."""
    __tablename__ = "enforcement_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_ref = Column(String(200), nullable=False, unique=True)
    regulator = Column(String(50), nullable=False, index=True)
    institution_type = Column(String(50), nullable=True)
    asset_tier = Column(String(20), nullable=True)
    action_date = Column(Date, nullable=False)
    action_type = Column(String(50), nullable=False)
    regulations_cited = Column(JSONB, nullable=False, default=list)
    violation_summary = Column(Text, nullable=True)
    penalty_amount_usd = Column(Numeric(15, 2), nullable=True)
    source_url = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EnforcementRiskScore(Base):
    """Computed enforcement risk scores per regulation (REQ-011)."""
    __tablename__ = "enforcement_risk_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    regulation_id = Column(String(100), nullable=False, index=True)
    regulator = Column(String(50), nullable=False)
    period_months = Column(Integer, nullable=False, default=24)
    action_count = Column(Integer, nullable=False, default=0)
    avg_penalty_usd = Column(Numeric(15, 2), nullable=True)
    total_penalty_usd = Column(Numeric(15, 2), nullable=True)
    trend_direction = Column(String(15), nullable=True)
    risk_score = Column(Float, nullable=False, default=0.0)
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("regulation_id", "period_months", name="uq_risk_score_reg_period"),
    )


class ComplianceDeadline(Base):
    """Upcoming compliance deadlines with coverage tracking (REQ-012)."""
    __tablename__ = "compliance_deadlines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deadline_key = Column(String(200), nullable=False, unique=True)
    regulation_id = Column(String(100), nullable=True)
    regulator = Column(String(50), nullable=False)
    deadline_type = Column(String(30), nullable=False)
    deadline_name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    deadline_date = Column(Date, nullable=False)
    days_remaining = Column(Integer, nullable=True)
    has_test_coverage = Column(Boolean, nullable=False, default=False)
    test_coverage_count = Column(Integer, nullable=False, default=0)
    obligation_count = Column(Integer, nullable=False, default=0)
    uncovered_obligation_count = Column(Integer, nullable=False, default=0)
    alert_thresholds_sent = Column(JSONB, default=dict)
    is_recurring = Column(Boolean, nullable=False, default=False)
    recurrence_rule = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RegulatoryChangeRecord(Base):
    """Change management records for regulatory rule updates (REQ-013)."""
    __tablename__ = "regulatory_change_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_ref = Column(String(50), nullable=False, unique=True)
    regulation_id = Column(String(100), nullable=True)
    rule_id = Column(String(100), nullable=False, index=True)
    regulator = Column(String(50), nullable=False)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    previous_hash = Column(String(64), nullable=True)
    new_hash = Column(String(64), nullable=True)
    change_summary = Column(Text, nullable=True)
    semantic_diff = Column(Text, nullable=True)
    change_delta = Column(JSONB, nullable=True)  # migration 037
    impact_rating = Column(String(10), nullable=True)
    change_type = Column(String(20), nullable=True)
    assigned_to = Column(String(200), nullable=True)
    status = Column(String(30), nullable=False, default="detected")
    status_updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reviewer_name = Column(String(200), nullable=True)
    reviewer_role = Column(String(200), nullable=True)
    signed_off_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    comments = relationship("ChangeRecordComment", back_populates="change_record")


class ChangeRecordComment(Base):
    """Append-only audit trail for change record actions (REQ-013)."""
    __tablename__ = "change_record_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_record_id = Column(UUID(as_uuid=True), ForeignKey("regulatory_change_records.id"), nullable=False)
    comment_type = Column(String(20), nullable=False, default="comment")
    author = Column(String(200), nullable=False)
    author_role = Column(String(200), nullable=True)
    comment_text = Column(Text, nullable=True)
    status_from = Column(String(30), nullable=True)
    status_to = Column(String(30), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    change_record = relationship("RegulatoryChangeRecord", back_populates="comments")


class ClientCredential(Base):
    """Per-bank API credentials for sync endpoint authentication (Pipeline 1)."""
    __tablename__ = "client_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(100), nullable=False, unique=True, index=True)
    bank_name = Column(String(200), nullable=False)
    api_key_hash = Column(String(64), nullable=False)   # SHA-256(raw_key), never store plaintext
    is_active = Column(Boolean, nullable=False, default=True)
    tier = Column(String(20), nullable=False, default="standard")  # standard | premium
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)


class PeerBenchmark(Base):
    """Anonymised peer compliance coverage benchmarks by asset tier (REQ-020)."""
    __tablename__ = "peer_benchmarks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    regulation_id = Column(String(100), nullable=False)
    regulator = Column(String(50), nullable=False)
    asset_tier = Column(String(20), nullable=False)
    source = Column(String(30), nullable=False)
    avg_coverage_pct = Column(Float, nullable=True)
    median_coverage_pct = Column(Float, nullable=True)
    top_quartile_pct = Column(Float, nullable=True)
    bottom_quartile_pct = Column(Float, nullable=True)
    sample_size = Column(Integer, nullable=True)
    data_period = Column(Date, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("regulation_id", "asset_tier", "source", "data_period",
                         name="uq_benchmark_reg_tier_source_period"),
        Index("idx_benchmarks_reg", "regulation_id", "asset_tier"),
    )


# ─────────────────────────────────────────────────────────────────────
# Examination Evidence & Reports — exports schema (migration 042)
#
# Per design doc §5.2. PostgreSQL enums match the CREATE TYPE statements
# in the migration; SQLAlchemy round-trips by VALUE (not NAME) so
# Python-side identifiers can stay uppercase while DB values stay
# lowercase.
# ─────────────────────────────────────────────────────────────────────

class OfficerRole(PyEnum):
    BSA_OFFICER = "bsa_officer"
    CCO = "cco"
    CRO = "cro"
    INTERNAL_AUDIT = "internal_audit"
    AUDIT_COMMITTEE_CHAIR = "audit_committee_chair"
    BOARD_CHAIR = "board_chair"
    CEO = "ceo"
    OTHER = "other"


class TestResult(PyEnum):
    PASS_ = "PASS"   # PASS is a reserved word — avoid the keyword clash
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_TESTED = "NOT_TESTED"


class FindingType(PyEnum):
    MRA = "MRA"
    MRIA = "MRIA"
    MRBA = "MRBA"
    VIOLATION = "VIOLATION"
    OBSERVATION = "OBSERVATION"


class FindingSeverity(PyEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingStatus(PyEnum):
    OPEN = "open"
    PAST_DUE = "past_due"
    PENDING_VALIDATION = "pending_validation"
    VALIDATED_CLOSED = "validated_closed"


class ChangeDisposition(PyEnum):
    ACKNOWLEDGED = "acknowledged"
    REQUIRES_ACTION = "requires_action"
    NOT_APPLICABLE = "not_applicable"


class SignatureMethod(PyEnum):
    SSO_SESSION = "sso_session"
    JWT_JTI = "jwt_jti"
    MANUAL_UPLOAD = "manual_upload"


def _e(name, py_enum):
    """Map a PG enum type by VALUE so Python uppercase identifiers can map
    to lowercase DB values without converting on every read/write."""
    return Enum(py_enum, name=name, values_callable=lambda x: [m.value for m in x])


class VocabVersion(Base):
    """Frozen disposition/finding vocabulary (Q18)."""
    __tablename__ = "vocab_versions"

    version_id = Column(String(32), primary_key=True)
    frozen_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    vocab_json = Column(JSONB, nullable=False)
    description = Column(Text, nullable=True)


class BankProfile(Base):
    __tablename__ = "bank_profile"

    bank_id = Column(String(80), primary_key=True)
    legal_name = Column(Text, nullable=False)
    charter_number = Column(String(32), nullable=True)
    primary_regulator = Column(String(32), nullable=False)
    secondary_regulators = Column(JSONB, nullable=False, default=list)
    total_assets_usd = Column(Numeric(20, 2), nullable=True)
    total_assets_as_of = Column(Date, nullable=True)
    fiscal_year_end = Column(Date, nullable=False)
    address = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    officers = relationship("BankOfficer", back_populates="bank",
                            cascade="all, delete-orphan", passive_deletes=True)


class BankOfficer(Base):
    __tablename__ = "bank_officers"

    person_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_id = Column(String(80), ForeignKey("bank_profile.bank_id", ondelete="RESTRICT"),
                     nullable=False, index=True)
    full_name = Column(Text, nullable=False)
    role = Column(_e("officer_role_t", OfficerRole), nullable=False)
    email = Column(Text, nullable=True)
    started_at = Column(Date, nullable=False)
    ended_at = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    bank = relationship("BankProfile", back_populates="officers")


class Control(Base):
    __tablename__ = "controls"

    control_id = Column(String(80), primary_key=True)
    bank_id = Column(String(80), ForeignKey("bank_profile.bank_id", ondelete="RESTRICT"),
                     nullable=False, index=True)
    name = Column(Text, nullable=False)
    owner_role = Column(_e("officer_role_t", OfficerRole), nullable=False)
    owner_person_id = Column(UUID(as_uuid=True),
                             ForeignKey("bank_officers.person_id", ondelete="SET NULL"),
                             nullable=True)
    description = Column(Text, nullable=True)
    control_type = Column(String(40), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class ControlRuleMapping(Base):
    __tablename__ = "control_rule_mappings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bank_id = Column(String(80), nullable=False)
    control_id = Column(String(80),
                        ForeignKey("controls.control_id", ondelete="CASCADE"),
                        nullable=False)
    rule_id = Column(String(100), nullable=False)
    mapping_strength = Column(String(10), nullable=False, default="full")
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    superseded_at = Column(DateTime(timezone=True), nullable=True)


class ControlTest(Base):
    __tablename__ = "control_tests"

    test_id = Column(String(80), primary_key=True)
    bank_id = Column(String(80), nullable=False)
    control_id = Column(String(80),
                        ForeignKey("controls.control_id", ondelete="CASCADE"),
                        nullable=False)
    framework = Column(String(40), nullable=False, default="pytest")
    threshold_value = Column(Numeric(20, 4), nullable=True)
    threshold_unit = Column(String(40), nullable=True)
    threshold_rationale = Column(Text, nullable=True)
    threshold_source_rule_id = Column(String(100), nullable=True)
    jira_story_key = Column(String(50), nullable=True)
    github_pr_url = Column(Text, nullable=True)
    commit_hash = Column(String(64), nullable=True)
    code_path = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class TestExecution(Base):
    __tablename__ = "test_executions"

    execution_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_id = Column(String(80), nullable=False)
    test_id = Column(String(80),
                     ForeignKey("control_tests.test_id", ondelete="RESTRICT"),
                     nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=False)
    result = Column(_e("test_result_t", TestResult), nullable=False)
    evidence_s3_uri = Column(Text, nullable=True)
    executor = Column(String(120), nullable=True)
    run_log_s3_uri = Column(Text, nullable=True)
    exception_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Finding(Base):
    __tablename__ = "findings"

    finding_id = Column(String(80), primary_key=True)
    bank_id = Column(String(80), nullable=False, index=True)
    type = Column(_e("finding_type_t", FindingType), nullable=False)
    severity = Column(_e("finding_severity_t", FindingSeverity), nullable=False)
    regulatory_source = Column(Text, nullable=True)
    # OCC 5 Cs
    concern = Column(Text, nullable=False)
    cause = Column(Text, nullable=True)
    consequence = Column(Text, nullable=True)
    corrective_action = Column(Text, nullable=True)
    commitment = Column(Text, nullable=True)
    root_cause = Column(Text, nullable=True)
    root_cause_identified = Column(Boolean, nullable=False, default=False)
    owner_person_id = Column(UUID(as_uuid=True),
                             ForeignKey("bank_officers.person_id", ondelete="SET NULL"),
                             nullable=True)
    identified_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    status = Column(_e("finding_status_t", FindingStatus), nullable=False, default=FindingStatus.OPEN)
    evidence_s3_uri = Column(Text, nullable=True)
    validation_notes = Column(Text, nullable=True)
    repeat_flag = Column(Boolean, nullable=False, default=False)
    repeat_of_finding_id = Column(String(80),
                                  ForeignKey("findings.finding_id", ondelete="SET NULL"),
                                  nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class FindingHistory(Base):
    __tablename__ = "finding_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    finding_id = Column(String(80),
                        ForeignKey("findings.finding_id", ondelete="CASCADE"),
                        nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    changed_by = Column(String(120), nullable=False)
    field = Column(String(64), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)


class ChangeRecordDisposition(Base):
    __tablename__ = "change_record_dispositions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    change_ref = Column(String(120), nullable=False)
    bank_id = Column(String(80), nullable=False)
    disposition = Column(_e("change_disposition_t", ChangeDisposition), nullable=False)
    owner_person_id = Column(UUID(as_uuid=True),
                             ForeignKey("bank_officers.person_id", ondelete="SET NULL"),
                             nullable=True)
    impl_link = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="open")
    dispositioned_at = Column(DateTime(timezone=True), nullable=True)
    dispositioned_by = Column(String(120), nullable=True)
    vocab_version = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("bank_id", "change_ref", name="uq_crd_bank_ref"),
    )


class OFACValidation(Base):
    __tablename__ = "ofac_validations"

    validation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_id = Column(String(80), nullable=False, index=True)
    performed_on = Column(Date, nullable=False)
    performed_by_person_id = Column(UUID(as_uuid=True),
                                    ForeignKey("bank_officers.person_id", ondelete="SET NULL"),
                                    nullable=True)
    scope = Column(Text, nullable=False)
    methodology = Column(Text, nullable=True)
    findings_summary = Column(Text, nullable=True)
    next_due = Column(Date, nullable=True)
    attestation_s3_uri = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class ExportArtifact(Base):
    """WORM-tracked export artifact (PDF/CSV) — written once, never edited."""
    __tablename__ = "export_artifacts"

    artifact_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_id = Column(String(80), nullable=False, index=True)
    type = Column(String(40), nullable=False)
    as_of = Column(Date, nullable=False)
    format = Column(String(8), nullable=False)
    version_hash = Column(String(7), nullable=False)
    s3_bucket = Column(String(200), nullable=False)
    s3_key = Column(Text, nullable=False)
    s3_etag = Column(String(80), nullable=True)
    s3_version_id = Column(String(120), nullable=True)
    kms_key_arn = Column(Text, nullable=True)
    byte_size = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=False)
    generated_by = Column(String(120), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    object_lock_retain_until = Column(DateTime(timezone=True), nullable=False)
    snapshot_canonical_sha256 = Column(String(64), nullable=False)
    source_data_uri = Column(Text, nullable=True)
    template_version = Column(String(32), nullable=False)
    vocab_version = Column(String(32), nullable=False)
    app_commit = Column(String(40), nullable=False)

    __table_args__ = (
        UniqueConstraint("bank_id", "type", "as_of", "version_hash",
                         name="uq_artifact_bank_type_asof_hash"),
        Index("ix_artifacts_bank_type_asof", "bank_id", "type", "as_of"),
    )


class ExportSignoff(Base):
    """E-signature audit chain — one row per required signer per artifact."""
    __tablename__ = "export_signoffs"

    signoff_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(UUID(as_uuid=True),
                         ForeignKey("export_artifacts.artifact_id", ondelete="RESTRICT"),
                         nullable=False, index=True)
    signer_person_id = Column(UUID(as_uuid=True),
                              ForeignKey("bank_officers.person_id", ondelete="RESTRICT"),
                              nullable=False)
    signer_role = Column(_e("officer_role_t", OfficerRole), nullable=False)
    signed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    signature_method = Column(_e("signature_method_t", SignatureMethod), nullable=False)
    ip_address = Column(Text, nullable=True)  # INET on the DB side
    user_agent = Column(Text, nullable=True)
    jwt_jti = Column(String(120), nullable=True)
    typed_full_name = Column(Text, nullable=True)
    acknowledgement = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("artifact_id", "signer_person_id", "signer_role",
                         name="uq_signoff_artifact_signer_role"),
    )


class ExportAccessLog(Base):
    __tablename__ = "export_access_log"

    access_id = Column(BigInteger, primary_key=True, autoincrement=True)
    artifact_id = Column(UUID(as_uuid=True),
                         ForeignKey("export_artifacts.artifact_id", ondelete="RESTRICT"),
                         nullable=False)
    accessed_by = Column(String(120), nullable=False)
    accessed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    ip_address = Column(Text, nullable=True)
    jwt_jti = Column(String(120), nullable=True)
    http_status = Column(Integer, nullable=True)


class ApplicableObligationRegistry(Base):
    """Per-charter/per-tier target obligation set (migration 043, audit §P1 / R10).

    Denominator source for the Obligation-inventory readiness dim: when
    populated for a bank's ``(bank_charter, asset_tier)`` scope the widget
    switches to ``pct(matched_cfr_citations, required_registry_rows)``;
    when empty the widget label degrades to "Ingestion completeness."
    """
    __tablename__ = "applicable_obligation_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_charter = Column(String(30), nullable=False)
    asset_tier = Column(String(20), nullable=False)
    cfr_citation = Column(String(200), nullable=False)
    regulator = Column(String(50), nullable=False)
    obligation_key = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    is_required = Column(Boolean, nullable=False, default=True)
    source = Column(String(60), nullable=False)
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "bank_charter", "asset_tier", "cfr_citation", "obligation_key",
            name="uq_registry_scope_citation_key",
        ),
    )


class ControlObligationMapping(Base):
    """Bank-scoped obligation↔control link (migration 044, audit §P2 / R11).

    Numerator source for the Control-mapping readiness dim: when
    populated for a bank the widget switches to
    ``pct(distinct_obligations_mapped, obligations_total)``; when empty
    the widget label degrades to "Obligation attribution" (R8).

    A mapping is *live* when ``superseded_at IS NULL`` and today falls
    inside ``[effective_from, effective_to]`` (both nullable → unbounded).
    Superseding a mapping preserves the audit trail — do not hard-delete.
    """
    __tablename__ = "control_obligation_mappings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bank_id = Column(String(80), nullable=False)
    control_id = Column(String(80),
                        ForeignKey("controls.control_id", ondelete="CASCADE"),
                        nullable=False)
    obligation_id = Column(String(200),
                           ForeignKey("obligations.obligation_id", ondelete="CASCADE"),
                           nullable=False)
    mapping_strength = Column(String(10), nullable=False, default="full")
    rationale = Column(Text, nullable=True)
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "bank_id", "control_id", "obligation_id",
            name="uq_com_bank_control_obligation",
        ),
    )


class GovernanceEvidence(Base):
    """Bank-scoped canonical governance artifact store (migration 045, audit §P7 / R12).

    Numerator source for the Governance readiness dim: when populated
    for a bank the widget switches to
    ``pct(distinct_evidence_types_live, _CANONICAL_GOVERNANCE_TYPES)``;
    when empty the widget label degrades to "Risk-attribution proxy"
    (the pre-registry query that shared its numerator with ra_currency).

    A row is *live* when ``superseded_at IS NULL`` and today falls
    inside ``[effective_from, effective_to]`` (both nullable →
    unbounded). Superseding a row preserves the audit trail — do not
    hard-delete. Partial UNIQUE on ``(bank_id, evidence_type)
    WHERE superseded_at IS NULL`` enforces at most one live row per
    (bank, type).
    """
    __tablename__ = "governance_evidence"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bank_id = Column(String(80), nullable=False)
    evidence_type = Column(String(60), nullable=False)
    artifact_ref = Column(String(500), nullable=True)
    source = Column(String(120), nullable=True)
    attested_by = Column(String(120), nullable=True)
    notes = Column(Text, nullable=True)
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
