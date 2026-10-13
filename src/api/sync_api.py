"""
Sync API — Master-Replica data sync endpoints for Pipeline 1.

These endpoints allow customer Pipeline 2 instances to pull incremental
compliance rule updates. Only public regulatory data is served.

Base URL (fixed, never changes): /api/sync
Auth: pass customer_id + api_key in the POST body for every request.

Endpoints:
  POST /api/sync/postgres       → Incremental compliance rule diffs
  POST /api/sync/embeddings     → Incremental rules with pre-computed 1024-dim vectors
  POST /api/sync/initial        → Full dump for first-time setup (paginated)
  POST /api/sync/status         → Customer reports sync telemetry
  POST /api/license/verify      → Verify customer API key is active
  POST /api/sync/obligations    → Obligation-level sync
  POST /api/sync/deadlines      → Compliance deadlines sync
  POST /api/sync/change-records → Regulatory change records sync (version-filtered)
  POST /api/sync/risk-scores    → Enforcement risk scores sync
  POST /api/sync/benchmarks     → Peer benchmarks sync
  POST /api/sync/ofac-sdn       → OFAC SDN entries sync (paginated)
  POST /api/sync/sdn-vectors    → OFAC SDN per-variant rows + 1024-dim embeddings
  POST /api/sync/sdn-changes    → OFAC SDN lifecycle delta (added/reactivated/removed)
  POST /api/sync/sdn-health     → P1-side SDN freshness + coverage telemetry
  POST /api/sync/counts         → Live record counts for parity verification
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.sync_auth import require_sync_auth
from src.services.database import get_db
from src.models.database import (
    ComplianceRule,
    ComplianceDeadline,
    DeletedRule,
    EnforcementRiskScore,
    OFACSDNEntry,
    Obligation,
    PeerBenchmark,
    RegulatoryChangeRecord,
    SyncMetadata,
    SyncVersion,
)
from src.services.obligation_diff_service import ObligationDiffService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Data Sync (Master → Replica)"])


# ── Request / Response schemas ──────────────────────────────────────

class SyncRequest(BaseModel):
    # H1: auth moved to headers via require_sync_auth. These body fields
    # are accepted for client back-compat during the cutover but are no
    # longer used for authentication — the only auth gate is the Depends
    # in the route signature. Drop them entirely once all P2 clients
    # send X-Customer-Id / X-Api-Key headers.
    customer_id: Optional[str] = Field(None, max_length=100)
    api_key: Optional[str] = Field(None)
    from_version: int = Field(0, ge=0)


class RuleDiff(BaseModel):
    rule_id: str
    regulator: str
    category: str
    geography: str
    domain: Optional[str] = None
    rule_type: Optional[str] = None
    rule_text: str
    threshold_amount: Optional[float] = None
    threshold_currency: Optional[str] = None
    field_name: Optional[str] = None
    effective_date: Optional[str] = None
    source_url: Optional[str] = None
    test_template: Optional[str] = None
    is_active: bool = True
    hash: Optional[str] = None
    version: Optional[int] = None
    # enrichment fields (migration 013)
    cfr_citation: Optional[str] = None
    state_code: Optional[str] = None
    jurisdiction_level: Optional[str] = None
    enforcement_risk_score: Optional[float] = None
    severity: Optional[str] = None  # Critical|High|Medium|Low (migration 051)
    change_delta: Optional[dict] = None
    changed_obligations: Optional[list] = None
    change_type: Optional[str] = None
    # authority + provenance (migrations 046/047) — the may_assert_as_law()
    # gate on P2 refuses to fire without these; if the payload drops them,
    # every synced rule becomes advisory-only.
    authority_type: Optional[str] = None
    authority_source: Optional[str] = None
    citation_verified: Optional[bool] = None
    citation_verified_at: Optional[str] = None
    citation_verified_source: Optional[str] = None
    source_content_hash: Optional[str] = None
    scraped_at: Optional[str] = None
    publication_date: Optional[str] = None
    comment_deadline: Optional[str] = None
    federal_register_doc: Optional[str] = None
    rin: Optional[str] = None
    applicability_asset_min: Optional[float] = None
    applicability_asset_max: Optional[float] = None
    applicability_charter: Optional[list] = None
    applicability_activities: Optional[list] = None
    applicability_states: Optional[list] = None
    is_exemption: Optional[bool] = None
    verify_before_display: Optional[bool] = None
    bank_configurable_threshold: Optional[bool] = None


class PostgresSyncResponse(BaseModel):
    current_version: int
    added: List[RuleDiff]
    updated: List[RuleDiff]
    deleted: List[str]  # rule_ids


class SyncTelemetry(BaseModel):
    customer_id: str
    sync_version: int
    sync_status: str  # SUCCESS, PARTIAL, FAILED
    rules_added: int = 0
    rules_updated: int = 0
    rules_deleted: int = 0
    error_message: Optional[str] = None


class LicenseResponse(BaseModel):
    status: str  # ACTIVE, EXPIRED, SUSPENDED
    customer_id: str
    current_version: int
    message: str = ""


class InitialSyncResponse(BaseModel):
    current_version: int
    total_rules: int
    page: int
    page_size: int
    rules: List[RuleDiff]
    has_more: bool


# ── Helpers ─────────────────────────────────────────────────────────

def _rule_to_diff(row) -> RuleDiff:
    """Convert a ComplianceRule row to a RuleDiff."""
    return RuleDiff(
        rule_id=row.rule_id,
        regulator=row.regulator,
        category=row.category,
        geography=row.geography,
        domain=row.domain,
        rule_type=row.rule_type,
        rule_text=row.rule_text,
        threshold_amount=row.threshold_amount,
        threshold_currency=row.threshold_currency,
        field_name=row.field_name,
        effective_date=str(row.effective_date) if row.effective_date else None,
        source_url=row.source_url,
        test_template=row.test_template,
        is_active=row.is_active,
        hash=row.hash,
        version=row.version,
        cfr_citation=getattr(row, "cfr_citation", None),
        state_code=getattr(row, "state_code", None),
        jurisdiction_level=getattr(row, "jurisdiction_level", None),
        enforcement_risk_score=getattr(row, "enforcement_risk_score", None),
        severity=getattr(row, "severity", None),
        change_delta=getattr(row, "change_delta", None),
        changed_obligations=getattr(row, "changed_obligations", None),
        change_type=getattr(row, "change_type", None),
        authority_type=getattr(row, "authority_type", None),
        authority_source=getattr(row, "authority_source", None),
        citation_verified=getattr(row, "citation_verified", None),
        citation_verified_at=(
            row.citation_verified_at.isoformat()
            if getattr(row, "citation_verified_at", None) else None
        ),
        citation_verified_source=getattr(row, "citation_verified_source", None),
        source_content_hash=getattr(row, "source_content_hash", None),
        scraped_at=(
            row.scraped_at.isoformat()
            if getattr(row, "scraped_at", None) else None
        ),
        publication_date=(
            str(row.publication_date)
            if getattr(row, "publication_date", None) else None
        ),
        comment_deadline=(
            str(row.comment_deadline)
            if getattr(row, "comment_deadline", None) else None
        ),
        federal_register_doc=getattr(row, "federal_register_doc", None),
        rin=getattr(row, "rin", None),
        applicability_asset_min=(
            float(row.applicability_asset_min)
            if getattr(row, "applicability_asset_min", None) is not None else None
        ),
        applicability_asset_max=(
            float(row.applicability_asset_max)
            if getattr(row, "applicability_asset_max", None) is not None else None
        ),
        applicability_charter=getattr(row, "applicability_charter", None),
        applicability_activities=getattr(row, "applicability_activities", None),
        applicability_states=getattr(row, "applicability_states", None),
        is_exemption=getattr(row, "is_exemption", None),
        verify_before_display=getattr(row, "verify_before_display", None),
        bank_configurable_threshold=getattr(row, "bank_configurable_threshold", None),
    )


async def _get_current_version(session: AsyncSession) -> int:
    """Get the current global sync version."""
    result = await session.execute(
        select(SyncVersion.current_version).where(SyncVersion.id == 1)
    )
    row = result.scalar_one_or_none()
    return row or 0


async def _verify_api_key(api_key: str, customer_id: str, session: AsyncSession) -> bool:
    """Verify customer API key against the client_credentials table."""
    from src.services.client_credential_service import ClientCredentialService
    return await ClientCredentialService().verify(session, customer_id, api_key)


# ── Endpoints ───────────────────────────────────────────────────────

@router.post("/sync/postgres", response_model=PostgresSyncResponse)
async def sync_postgres(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """
    Return incremental compliance rule diffs since `from_version`.

    The customer passes their last-known version and receives only
    rules that were added, updated, or deleted since that version.
    """
    customer_id = auth["customer_id"]
    async with get_db() as session:
        current_version = await _get_current_version(session)

        if request.from_version >= current_version:
            return PostgresSyncResponse(
                current_version=current_version, added=[], updated=[], deleted=[]
            )

        result = await session.execute(
            select(ComplianceRule).where(
                ComplianceRule.version > request.from_version,
                ComplianceRule.is_active == True,
            ).order_by(ComplianceRule.version)
        )
        changed_rules = result.scalars().all()

        added = []
        updated = []
        for rule in changed_rules:
            diff = _rule_to_diff(rule)
            if rule.created_at and rule.updated_at and rule.created_at == rule.updated_at:
                added.append(diff)
            else:
                updated.append(diff)

        del_result = await session.execute(
            select(DeletedRule.rule_id).where(
                DeletedRule.deleted_at_version > request.from_version
            )
        )
        deleted_ids = [row[0] for row in del_result.all()]

        await session.execute(
            update(SyncMetadata)
            .where(SyncMetadata.customer_id == customer_id)
            .values(
                last_sync_version=current_version,
                last_sync_timestamp=datetime.utcnow(),
                sync_count=SyncMetadata.sync_count + 1,
            )
        )
        await session.commit()

        logger.info(
            "sync_postgres_served",
            customer_id=customer_id,
            from_version=request.from_version,
            current_version=current_version,
            added=len(added),
            updated=len(updated),
            deleted=len(deleted_ids),
        )

        return PostgresSyncResponse(
            current_version=current_version,
            added=added,
            updated=updated,
            deleted=deleted_ids,
        )


@router.post("/sync/embeddings")
async def sync_embeddings(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """
    Return rules updated since from_version WITH pre-computed Titan Embed v2
    vectors (1024-dim). P2 stores these directly into pgvector — no local
    embedding step needed.
    """
    async with get_db() as session:
        current_version = await _get_current_version(session)

        if request.from_version >= current_version:
            return {"current_version": current_version, "rules": [], "has_more": False}

        from sqlalchemy import text as sqla_text
        result = await session.execute(sqla_text("""
            SELECT rule_id, rule_text, regulator, cfr_citation,
                   jurisdiction_level, state_code,
                   effective_date, is_active,
                   enforcement_risk_score, severity, version,
                   authority_type, authority_source,
                   citation_verified, citation_verified_at,
                   citation_verified_source,
                   source_content_hash, scraped_at,
                   publication_date, comment_deadline,
                   federal_register_doc, rin,
                   applicability_asset_min, applicability_asset_max,
                   applicability_charter, applicability_activities,
                   applicability_states,
                   is_exemption, verify_before_display,
                   bank_configurable_threshold,
                   embedding::text AS embedding
            FROM compliance_rules
            WHERE version > :from_ver
              AND is_active = TRUE
              AND embedding IS NOT NULL
            ORDER BY version ASC
            LIMIT 500
        """), {"from_ver": request.from_version})
        rows = result.fetchall()

        rules = [dict(r._mapping) for r in rows]

        return {
            "current_version": current_version,
            "rules": rules,
            "has_more": len(rules) == 500,
        }


@router.post("/sync/initial", response_model=InitialSyncResponse)
async def sync_initial(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    page: int = Query(1, ge=1),
    page_size: int = Query(5000, ge=100, le=50000),
    active_only: bool = Query(False),
):
    """
    Full dump for first-time customer setup. Returns paginated results.

    Usage: customer calls this repeatedly incrementing `page` until
    `has_more` is False, then switches to incremental sync.

    Returns every rule (active + inactive) so the replica mirrors P1
    ground truth. Set active_only=true to filter to is_active rows only.
    Each row carries its own is_active flag — P2 keeps the field intact.
    """
    customer_id = auth["customer_id"]
    async with get_db() as session:
        current_version = await _get_current_version(session)

        base_filter = ComplianceRule.is_active == True if active_only else None

        count_q = select(func.count()).select_from(ComplianceRule)
        if base_filter is not None:
            count_q = count_q.where(base_filter)
        count_result = await session.execute(count_q)
        total_rules = count_result.scalar() or 0

        offset = (page - 1) * page_size
        rule_q = select(ComplianceRule)
        if base_filter is not None:
            rule_q = rule_q.where(base_filter)
        result = await session.execute(
            rule_q.order_by(ComplianceRule.rule_id).offset(offset).limit(page_size)
        )
        rules = [_rule_to_diff(r) for r in result.scalars().all()]

        has_more = (offset + len(rules)) < total_rules

        # H6 — UPSERT sync_metadata on EVERY page so dashboard / health checks
        # see progress mid-initial-sync. sync_count only bumps on the final
        # page so a multi-page seed counts as one sync, not N.
        await session.execute(text("""
            INSERT INTO sync_metadata
              (customer_id, last_sync_version, last_sync_timestamp, sync_count)
            VALUES
              (:cid, :ver, :ts, :inc)
            ON CONFLICT (customer_id) DO UPDATE
              SET last_sync_version   = EXCLUDED.last_sync_version,
                  last_sync_timestamp = EXCLUDED.last_sync_timestamp,
                  sync_count          = sync_metadata.sync_count + :inc,
                  updated_at          = NOW()
        """), {
            "cid": customer_id,
            "ver": current_version,
            "ts": datetime.utcnow(),
            "inc": 0 if has_more else 1,
        })
        await session.commit()

        logger.info(
            "sync_initial_served",
            customer_id=customer_id,
            page=page,
            rules_in_page=len(rules),
            total_rules=total_rules,
            has_more=has_more,
        )

        return InitialSyncResponse(
            current_version=current_version,
            total_rules=total_rules,
            page=page,
            page_size=page_size,
            rules=rules,
            has_more=has_more,
        )


@router.post("/sync/status")
async def report_sync_telemetry(
    telemetry: SyncTelemetry,
    auth: dict = Depends(require_sync_auth),
):
    """
    Receive sync telemetry from customer Pipeline 2.
    Logged for monitoring and support purposes.

    H2: header-auth required. customer_id in the body is ignored when
    it disagrees with the authenticated identity, to stop a caller
    from poisoning telemetry for a customer they know but don't own.
    """
    if telemetry.customer_id != auth["customer_id"]:
        logger.warning(
            "sync_status_customer_mismatch",
            body_customer=telemetry.customer_id,
            auth_customer=auth["customer_id"],
        )
        raise HTTPException(status_code=403, detail="Forbidden")
    async with get_db() as session:
        result = await session.execute(
            select(SyncMetadata).where(SyncMetadata.customer_id == telemetry.customer_id)
        )
        meta = result.scalar_one_or_none()
        if meta:
            meta.last_sync_version = telemetry.sync_version
            meta.last_sync_timestamp = datetime.utcnow()
            await session.commit()

    logger.info(
        "sync_telemetry_received",
        customer_id=telemetry.customer_id,
        sync_version=telemetry.sync_version,
        sync_status=telemetry.sync_status,
        rules_added=telemetry.rules_added,
        rules_updated=telemetry.rules_updated,
        rules_deleted=telemetry.rules_deleted,
        error=telemetry.error_message,
    )

    return {"status": "received"}


@router.post("/license/verify", response_model=LicenseResponse)
async def verify_license(customer_id: str, api_key: str):
    """
    Verify customer license / API key status.
    Called by customer update service before each sync.
    POST /api/license/verify?customer_id=...&api_key=...
    """
    async with get_db() as session:
        is_valid = await _verify_api_key(api_key, customer_id, session)
        current_version = await _get_current_version(session)
        await session.commit()

    if not is_valid:
        return LicenseResponse(
            status="EXPIRED",
            customer_id=customer_id,
            current_version=current_version,
            message="API key is invalid or expired.",
        )

    return LicenseResponse(
        status="ACTIVE",
        customer_id=customer_id,
        current_version=current_version,
        message="License is active. Sync is permitted.",
    )


# ── Section 11: New Sync Endpoints ──────────────────────────────────

@router.post("/sync/obligations")
async def sync_obligations(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """Return all active obligations for obligation-level sync and diffing."""
    async with get_db() as session:
        result = await session.execute(
            select(Obligation).where(Obligation.is_active == True)
        )
        obligations = [
            {
                "obligation_id":     r.obligation_id,
                "regulation_id":     r.regulation_id,
                "regulator":         r.regulator,
                "category":          r.category,
                "source_text":       r.source_text,
                "obligation_type":   r.obligation_type,
                "section_reference": r.section_reference,
                "cfr_citation":      r.cfr_citation,
                "has_test_coverage": r.has_test_coverage,
                "extracted_at":      r.extracted_at.isoformat() if r.extracted_at else None,
            }
            for r in result.scalars().all()
        ]
        return {"obligations": obligations}


@router.post("/sync/deadlines")
async def sync_deadlines(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """Return upcoming active compliance deadlines."""
    async with get_db() as session:
        result = await session.execute(
            select(ComplianceDeadline)
            .where(ComplianceDeadline.is_active == True)
            .where(ComplianceDeadline.deadline_date >= func.current_date())
        )
        deadlines = [
            {
                "deadline_key":    r.deadline_key,
                "regulation_id":   r.regulation_id,
                "regulator":       r.regulator,
                "deadline_type":   r.deadline_type,
                "deadline_name":   r.deadline_name,
                "description":     r.description,
                "deadline_date":   r.deadline_date.isoformat() if r.deadline_date else None,
                "days_remaining":  r.days_remaining,
                "is_recurring":    r.is_recurring,
                "recurrence_rule": r.recurrence_rule,
            }
            for r in result.scalars().all()
        ]
        return {"deadlines": deadlines}


@router.post("/sync/change-records")
async def sync_change_records(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    limit: int = Query(200, ge=1, le=1000),
):
    """Return regulatory change records since from_version, ordered by detected_at desc."""
    async with get_db() as session:
        current_version = await _get_current_version(session)

        query = (
            select(RegulatoryChangeRecord)
            .where(RegulatoryChangeRecord.is_active == True)
        )
        if request.from_version > 0:
            # Map version to a detected_at cutoff via the sync_versions timestamp
            version_row = await session.execute(
                select(SyncVersion.updated_at).where(SyncVersion.id == 1)
            )
            # Fall back to ordering by id — filter rules touched at or after from_version
            query = query.where(
                RegulatoryChangeRecord.rule_id.in_(
                    select(ComplianceRule.rule_id).where(
                        ComplianceRule.version > request.from_version
                    )
                )
            )

        result = await session.execute(
            query.order_by(RegulatoryChangeRecord.detected_at.desc()).limit(limit)
        )
        records = [
            {
                "change_ref":     r.change_ref,
                "rule_id":        r.rule_id,
                "regulation_id":  r.regulation_id,
                "regulator":      r.regulator,
                "detected_at":    r.detected_at.isoformat() if r.detected_at else None,
                "change_summary": r.change_summary,
                "semantic_diff":  r.semantic_diff,
                "change_delta":   r.change_delta,
                "impact_rating":  r.impact_rating,
                "change_type":    r.change_type,
                "status":         r.status,
                "signed_off_at":  r.signed_off_at.isoformat() if r.signed_off_at else None,
            }
            for r in result.scalars().all()
        ]
        return {"current_version": current_version, "records": records}


@router.post("/sync/risk-scores")
async def sync_risk_scores(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """Return enforcement risk scores (24-month window)."""
    async with get_db() as session:
        result = await session.execute(
            select(EnforcementRiskScore).where(EnforcementRiskScore.period_months == 24)
        )
        scores = [
            {
                "regulation_id":     r.regulation_id,
                "regulator":         r.regulator,
                "risk_score":        r.risk_score,
                "action_count":      r.action_count,
                "avg_penalty_usd":   float(r.avg_penalty_usd) if r.avg_penalty_usd else None,
                "total_penalty_usd": float(r.total_penalty_usd) if r.total_penalty_usd else None,
                "trend_direction":   r.trend_direction,
                "computed_at":       r.computed_at.isoformat() if r.computed_at else None,
            }
            for r in result.scalars().all()
        ]
        return {"risk_scores": scores}


@router.post("/sync/benchmarks")
async def sync_benchmarks(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """Return peer benchmarking data ordered by most recent period."""
    async with get_db() as session:
        result = await session.execute(
            select(PeerBenchmark).order_by(PeerBenchmark.data_period.desc())
        )
        benchmarks = [
            {
                "regulation_id":       r.regulation_id,
                "regulator":           r.regulator,
                "asset_tier":          r.asset_tier,
                "source":              r.source,
                "avg_coverage_pct":    r.avg_coverage_pct,
                "median_coverage_pct": r.median_coverage_pct,
                "top_quartile_pct":    r.top_quartile_pct,
                "bottom_quartile_pct": r.bottom_quartile_pct,
                "data_period":         r.data_period.isoformat() if r.data_period else None,
            }
            for r in result.scalars().all()
        ]
        return {"benchmarks": benchmarks}


@router.post("/sync/ofac-sdn")
async def sync_ofac_sdn(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    page: int = Query(1, ge=1),
    page_size: int = Query(5000, ge=100, le=50000),
):
    """
    Paginated sync of active OFAC SDN entries.

    Customer polls incrementing page until has_more is False.
    Re-call with page=1 on each sync cycle to pick up new/removed entries.
    """
    customer_id = auth["customer_id"]
    async with get_db() as session:
        count_result = await session.execute(
            select(func.count()).select_from(OFACSDNEntry).where(OFACSDNEntry.is_active == True)
        )
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        result = await session.execute(
            select(OFACSDNEntry)
            .where(OFACSDNEntry.is_active == True)
            .order_by(OFACSDNEntry.sdn_number)
            .offset(offset)
            .limit(page_size)
        )
        entries = [
            {
                "sdn_number":        r.sdn_number,
                "entity_name":       r.entity_name,
                "entity_type":       r.entity_type,
                "programs":          r.programs,
                "aliases":           r.aliases,
                "addresses":         r.addresses,
                "id_numbers":        r.id_numbers,
                "remarks":           r.remarks,
                "country":           r.country,
                "nationality":       r.nationality,
                "source_updated_at": r.source_updated_at.isoformat() if r.source_updated_at else None,
            }
            for r in result.scalars().all()
        ]

        has_more = (offset + len(entries)) < total

        logger.info(
            "sync_ofac_sdn_served",
            customer_id=customer_id,
            page=page,
            entries_in_page=len(entries),
            total=total,
        )

        return {
            "total_entries": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "entries": entries,
        }


@router.post("/sync/sdn-vectors")
async def sync_sdn_vectors(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    page: int = Query(1, ge=1),
    page_size: int = Query(2000, ge=100, le=10000),
    embedded_only: bool = Query(True),
    since: Optional[datetime] = Query(
        None,
        description="If set, return only rows with embedded_at >= since (ISO 8601). "
                    "Use for incremental sync after the initial full dump.",
    ),
):
    """Paginated sync of ofac_sdn_vectors with pre-computed 1024-dim Titan embeddings.

    Replaces the previous P2-side re-derive + re-embed cycle. P2 stores rows
    directly into its local ofac_sdn_vectors table — no Bedrock call needed.

    Recommended sync strategy:
      - First sync: poll incrementing `page` until `has_more` is False
      - Incremental: pass `since` = timestamp of previous sync to fetch only
        rows whose embeddings have been added/updated since then. For
        reactivations and additions, P2 should also call `/sync/sdn-changes`
        and re-fetch any (sdn_number, name_normalized) keys it surfaces.

    `embedded_only=true` (default) excludes variants that haven't been
    embedded yet — those rows would be useless for the vector screening path.
    """
    customer_id = auth["customer_id"]
    async with get_db() as session:
        where_clauses = ["v.is_active = TRUE"]
        params: Dict[str, Any] = {}
        if embedded_only:
            where_clauses.append("v.embedding IS NOT NULL")
        if since is not None:
            where_clauses.append("v.embedded_at >= :since")
            params["since"] = since
        where_sql = " AND ".join(where_clauses)

        total = await session.scalar(
            text(f"SELECT COUNT(*) FROM ofac_sdn_vectors v WHERE {where_sql}"),
            params,
        ) or 0

        offset = (page - 1) * page_size
        result = await session.execute(
            text(f"""
                SELECT
                    v.sdn_number, v.name_variant, v.name_normalized, v.is_primary,
                    v.entity_type, v.programs, v.country, v.nationality,
                    v.source_updated_at, v.last_seen_at, v.embedded_at,
                    v.embedding::text AS embedding
                FROM ofac_sdn_vectors v
                WHERE {where_sql}
                ORDER BY v.sdn_number, v.name_normalized
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": page_size, "offset": offset},
        )

        rows = [dict(r._mapping) for r in result.fetchall()]
        for r in rows:
            for k in ("source_updated_at", "last_seen_at", "embedded_at"):
                if r.get(k) is not None:
                    r[k] = r[k].isoformat()

        has_more = (offset + len(rows)) < total

        logger.info(
            "sync_sdn_vectors_served",
            customer_id=customer_id,
            page=page,
            rows_in_page=len(rows),
            total=total,
            embedded_only=embedded_only,
            since=since.isoformat() if since else None,
        )

        return {
            "total_vectors": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "embedded_only": embedded_only,
            "rows": rows,
        }


@router.post("/sync/sdn-changes")
async def sync_sdn_changes(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    since: datetime = Query(
        ...,
        description="ISO 8601 — keyset cursor lower-bound for detected_at.",
    ),
    since_ref: Optional[str] = Query(
        None,
        description="Keyset tiebreaker — change_ref of the last row in the "
                    "previous page. Required to advance past batches where "
                    "many rows share the same detected_at (e.g. backfills).",
    ),
    limit: int = Query(500, ge=1, le=2000),
):
    """OFAC SDN lifecycle delta — drives P2 continuous-monitoring rescreens.

    Returns variant-level change events emitted by `ofac_sdn_loader`:
      - 'added'       — first time we saw this (sdn_number, name_normalized)
      - 'reactivated' — was tombstoned, now back; embedding preserved
      - 'removed'     — soft-deactivated after N consecutive misses

    Keyset pagination: comparison is `(detected_at, change_ref) > (since, since_ref)`
    so backfill batches that share `detected_at` paginate correctly. First
    call passes `since` only; subsequent calls pass both `since` and `since_ref`
    from the previous response.

    P2 consumption pattern (canonical):
      - 'added'       → call /sync/sdn-vectors?since=… to fetch the new row
                         (embedding included)
      - 'reactivated' → flip local is_active=TRUE on the matching row;
                         embedding was preserved, no fetch needed
      - 'removed'     → flip local is_active=FALSE on the matching row

    `change_summary` is a human sentence (e.g. "SDN added: ACME LTD —
    entity, programs: IRAN-EO13902") suitable for direct display in the
    P2 dashboard's change inbox. `semantic_diff` carries the structured
    detail line (variant role · entity type · programs · country) used
    to locate the local row without an extra round-trip.
    """
    customer_id = auth["customer_id"]
    async with get_db() as session:
        # Tuple compare: detected_at > since OR (detected_at = since AND change_ref > since_ref).
        # When since_ref is NULL, treat as empty string so the OR branch
        # selects every row at exactly :since.
        result = await session.execute(text("""
            SELECT
                change_ref,
                rule_id,
                change_type,
                change_summary,
                semantic_diff,
                status,
                detected_at,
                status_updated_at
            FROM regulatory_change_records
            WHERE regulator = 'OFAC'
              AND rule_id LIKE 'OFAC-SDN-%'
              AND change_type IN ('added','reactivated','removed')
              AND is_active = TRUE
              AND (
                    detected_at > :since
                 OR (detected_at = :since AND change_ref > COALESCE(:since_ref, ''))
              )
            ORDER BY detected_at ASC, change_ref ASC
            LIMIT :limit
        """), {"since": since, "since_ref": since_ref, "limit": limit})

        rows = []
        for r in result.fetchall():
            m = r._mapping
            rows.append({
                "change_ref":         m["change_ref"],
                "rule_id":            m["rule_id"],
                "sdn_number":         m["rule_id"].removeprefix("OFAC-SDN-"),
                "change_type":        m["change_type"],
                "change_summary":     m["change_summary"],
                "semantic_diff":      m["semantic_diff"],
                "status":             m["status"],
                "detected_at":        m["detected_at"].isoformat() if m["detected_at"] else None,
                "status_updated_at":  m["status_updated_at"].isoformat() if m["status_updated_at"] else None,
            })

        # Composite cursor for the next page. Clients echo BOTH back next call.
        if rows:
            next_since = rows[-1]["detected_at"]
            next_since_ref = rows[-1]["change_ref"]
        else:
            next_since = since.isoformat()
            next_since_ref = since_ref

        logger.info(
            "sync_sdn_changes_served",
            customer_id=customer_id,
            since=since.isoformat(),
            since_ref=since_ref,
            returned=len(rows),
            limit=limit,
        )

        return {
            "since":          since.isoformat(),
            "since_ref":      since_ref,
            "next_since":     next_since,
            "next_since_ref": next_since_ref,
            "count":          len(rows),
            "has_more":       len(rows) == limit,
            "changes":        rows,
        }


@router.post("/sync/sdn-health")
async def sync_sdn_health(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """P1-side SDN freshness + coverage telemetry.

    P2's OFACScreener._enforce_freshness() should consult this alongside
    its local last_seen_at, so a broken P2 sync surfaces as stale-source
    rather than silently screening against day-old data.

    Returned fields:
      p1_last_scraped_at      — latest successful ofac_sdn scrape (TIMESTAMPTZ)
      hours_since_last_scrape — derived; null if no successful runs yet
      entries_active          — distinct active SDNs (parity check)
      variants_active         — active rows in ofac_sdn_vectors
      variants_embedded       — variants_active AND embedding IS NOT NULL
      embed_coverage_pct      — embedded / active * 100, rounded
    """
    async with get_db() as session:
        last_scraped = await session.scalar(text("""
            SELECT MAX(run_at) FROM scrape_runs
            WHERE connector_name = 'ofac_sdn' AND success = TRUE
        """))

        entries_active = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active = TRUE"
        )) or 0

        variants_active = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active = TRUE"
        )) or 0

        variants_embedded = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors "
            "WHERE is_active = TRUE AND embedding IS NOT NULL"
        )) or 0

    hours_since = None
    if last_scraped is not None:
        delta = datetime.now(last_scraped.tzinfo) - last_scraped
        hours_since = round(delta.total_seconds() / 3600.0, 2)

    coverage = (
        round(100.0 * variants_embedded / variants_active, 1)
        if variants_active else 0.0
    )

    return {
        "p1_last_scraped_at":      last_scraped.isoformat() if last_scraped else None,
        "hours_since_last_scrape": hours_since,
        "entries_active":          entries_active,
        "variants_active":         variants_active,
        "variants_embedded":       variants_embedded,
        "embed_coverage_pct":      coverage,
    }


@router.post("/sync/ofac-list-version")
async def sync_ofac_list_version(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    list_name: str = Query("sdn", regex="^(sdn|cons)$"),
):
    """Latest row from ``ofac_list_versions`` for the requested list.

    Surfaces the OFAC source-freshness anchor (publish date + SHA-256 of the
    raw XML) so the P2 ofac_attestation export can include "List version +
    publication time + ingestion lag" per design doc §4 type 2.

    Returns 404 if no version has been recorded yet — caller decides whether
    to render the cover block with "not yet ingested" or hold the report.
    """
    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT list_name, version_id, published_at, ingested_at,
                   source_url, sha256_of_xml, scrape_run_id
            FROM ofac_list_versions
            WHERE list_name = :list_name
            ORDER BY ingested_at DESC
            LIMIT 1
        """), {"list_name": list_name})).first()

    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"no recorded version for list_name={list_name!r}")

    return {
        "list_name":     row.list_name,
        "version_id":    row.version_id,
        "published_at":  row.published_at.isoformat() if row.published_at else None,
        "ingested_at":   row.ingested_at.isoformat(),
        "source_url":    row.source_url,
        "sha256_of_xml": row.sha256_of_xml,
        "scrape_run_id": str(row.scrape_run_id) if row.scrape_run_id else None,
    }


@router.post("/sync/enforcement-actions")
async def sync_enforcement_actions(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
    page: int = Query(1, ge=1),
    page_size: int = Query(1000, ge=100, le=10000),
):
    """Paginated sync of active enforcement actions (raw event log).

    Distinct from /sync/risk-scores, which returns the aggregated scores.
    P2's dashboard panels (enforcement-risk timeline) need both.
    """
    async with get_db() as session:
        from src.models.database import EnforcementAction

        count_result = await session.execute(
            select(func.count()).select_from(EnforcementAction).where(
                EnforcementAction.is_active == True
            )
        )
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        result = await session.execute(
            select(EnforcementAction)
            .where(EnforcementAction.is_active == True)
            .order_by(EnforcementAction.action_ref)
            .offset(offset)
            .limit(page_size)
        )
        actions = [
            {
                "action_ref":         r.action_ref,
                "regulator":          r.regulator,
                "institution_type":   r.institution_type,
                "asset_tier":         r.asset_tier,
                "action_date":        r.action_date.isoformat() if r.action_date else None,
                "action_type":        r.action_type,
                "regulations_cited":  r.regulations_cited,
                "violation_summary":  r.violation_summary,
                "penalty_amount_usd": float(r.penalty_amount_usd) if r.penalty_amount_usd else None,
                "source_url":         r.source_url,
                "content_hash":       r.content_hash,
                "scraped_at":         r.scraped_at.isoformat() if r.scraped_at else None,
            }
            for r in result.scalars().all()
        ]

        has_more = (offset + len(actions)) < total

        return {
            "total_actions": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "actions": actions,
        }


@router.post("/sync/counts")
async def sync_counts(
    request: SyncRequest,
    auth: dict = Depends(require_sync_auth),
):
    """
    Live record counts for all synced tables.
    P2 uses this to verify parity against P1 after sync.
    """
    customer_id = auth["customer_id"]
    async with get_db() as session:
        current_version = await _get_current_version(session)

        async def _count(model, where=None):
            q = select(func.count()).select_from(model)
            if where is not None:
                q = q.where(where)
            return (await session.scalar(q)) or 0

        from src.models.database import EnforcementAction
        from sqlalchemy import text as sqla_text

        # Count rules that have embeddings (P2 can verify vector sync)
        embedded_count = await session.scalar(sqla_text(
            "SELECT COUNT(*) FROM compliance_rules "
            "WHERE is_active = TRUE AND embedding IS NOT NULL"
        )) or 0

        # OFAC SDN per-variant table — counts feed the screener parity check
        # in P2's update_service. Embedded count drives the freshness/coverage
        # gate alongside /sync/sdn-health.
        sdn_vectors_active = await session.scalar(sqla_text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active = TRUE"
        )) or 0
        sdn_vectors_embedded = await session.scalar(sqla_text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors "
            "WHERE is_active = TRUE AND embedding IS NOT NULL"
        )) or 0

        postgres_counts = {
            "compliance_rules":           await _count(ComplianceRule, ComplianceRule.is_active == True),
            "compliance_rules_embedded":  embedded_count,
            "ofac_sdn_entries":           await _count(OFACSDNEntry, OFACSDNEntry.is_active == True),
            "ofac_sdn_vectors":           sdn_vectors_active,
            "ofac_sdn_vectors_embedded":  sdn_vectors_embedded,
            "obligations":                await _count(Obligation, Obligation.is_active == True),
            "enforcement_actions":        await _count(EnforcementAction, EnforcementAction.is_active == True),
            "enforcement_risk_scores":    await _count(EnforcementRiskScore),
            "compliance_deadlines":       await _count(ComplianceDeadline, ComplianceDeadline.is_active == True),
            "regulatory_change_records":  await _count(RegulatoryChangeRecord, RegulatoryChangeRecord.is_active == True),
            "peer_benchmarks":            await _count(PeerBenchmark),
            "deleted_rules":              await _count(DeletedRule),
        }

    logger.info("sync_counts_served",
                customer_id=customer_id,
                current_version=current_version)

    return {
        "current_version": current_version,
        "postgres": postgres_counts,
        "vector_search": "pgvector_on_aurora",
    }
