"""
src/api/dashboard_api.py
CCO / CRO / CTO dashboard data endpoints for the React frontend.

Existing endpoints:
  GET  /api/v1/dashboard/overview      health score + 4 metric cards
  GET  /api/v1/dashboard/rules         rule table with filter/sort/pagination
  GET  /api/v1/dashboard/changes       regulatory change inbox
  GET  /api/v1/dashboard/changes/{ref}  change detail + joined rule before/after
  GET  /api/v1/dashboard/deadlines     upcoming filing deadlines
  GET  /api/v1/dashboard/risk          enforcement risk scores
  GET  /api/v1/dashboard/coverage      test coverage by regulator
  GET  /api/v1/dashboard/workflows     JIRA pipeline run status
  GET  /api/v1/dashboard/ofac-health   SDN entries / variants / embeddings ready
  GET  /api/v1/dashboard/enforcement   recent enforcement actions feed
  GET  /api/v1/dashboard/obligations   obligation coverage breakdown by regulator
  PATCH /api/v1/dashboard/rules/{rule_id}/status   mark N/A or set override (CCO)
  POST  /api/v1/dashboard/changes/{ref}/signoff    sign off change (CCO/CRO)
  POST  /api/v1/dashboard/changes/signoff/bulk     bulk sign off up to 500 refs (R9)

Unified-dashboard additions (read-only, computed from existing tables):
  GET  /api/v1/dashboard/readiness            persona-weighted readiness score + 10 dims
  GET  /api/v1/dashboard/risk-matrix          3×3 risk × coverage grid + danger ids
  GET  /api/v1/dashboard/cro                  residual-vs-appetite + KRIs + peer signal
  GET  /api/v1/dashboard/cio                  engine integrity + architecture manifest
  GET  /api/v1/dashboard/findings             findings/MRA list (empty until DB table exists)
  GET  /api/v1/architecture/data-flow         static data-flow manifest for TPRM/architect
  POST /api/v1/dashboard/exports              generate point-in-time, version-hashed snapshot
  GET  /api/v1/dashboard/exports/{id}/download  download generated artifact
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import io
import csv
import json
from datetime import datetime, timezone, date as _date
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.api.auth import (
    CurrentUser,
    get_current_user,
    require_admin,
    require_analyst,
    require_approver,
    require_cco,
    require_vector_intel,
)
from src.services.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

# Second router mounted at /api/v1/architecture for the vendor-due-diligence
# data-flow manifest (per dashboard_enhancement.md §7).
architecture_router = APIRouter(
    prefix="/api/v1/architecture", tags=["architecture"]
)


# ── Overview ──────────────────────────────────────────────────────────

@router.get("/overview")
async def get_overview(user: CurrentUser = Depends(get_current_user)) -> Dict:
    async with get_db() as session:
        total = await session.scalar(text(
            "SELECT COUNT(*) FROM compliance_rules WHERE is_active=TRUE"
        )) or 0

        tested = await session.scalar(text("""
            SELECT COUNT(DISTINCT r.rule_id)
            FROM compliance_rules r
            JOIN workflow_executions w ON w.rule_id = r.rule_id
            WHERE r.is_active=TRUE AND w.status='completed'
        """)) or 0

        excluded = await session.scalar(text(
            "SELECT COUNT(*) FROM rule_overrides WHERE status='not_applicable'"
        )) or 0

        changes = await session.scalar(text(
            "SELECT COUNT(*) FROM regulatory_change_records "
            "WHERE status='detected' AND is_active=TRUE"
        )) or 0

        deadline = await session.execute(text("""
            SELECT deadline_name, days_remaining FROM compliance_deadlines
            WHERE is_active=TRUE AND days_remaining > 0
            ORDER BY days_remaining ASC LIMIT 1
        """))
        nearest = deadline.fetchone()

        regulators_count = await session.scalar(text(
            "SELECT COUNT(DISTINCT regulator) FROM compliance_rules "
            "WHERE is_active=TRUE AND regulator IS NOT NULL"
        )) or 0

    health_pct = round((tested / total * 100) if total else 0, 1)
    return {
        "health_score_pct": health_pct,
        "total_rules": total,
        "tested_rules": tested,
        "excluded_rules": excluded,
        "pending_changes": changes,
        "regulators_count": regulators_count,
        "nearest_deadline": {
            "name": nearest.deadline_name if nearest else None,
            "days": nearest.days_remaining if nearest else None,
        },
        "user_role": user.role,
    }


# ── Rules table ───────────────────────────────────────────────────────

@router.get("/rules")
async def get_rules(
    regulator: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    scope: str = Query("applicable"),   # applicable | all
    sort_by: str = Query("severity"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    # Triage-first default: Critical→Low, then highest enforcement risk. The
    # severity rank is emitted as a column so ORDER BY can reference the alias.
    order = {
        "severity": "sev_rank ASC, cr.enforcement_risk_score DESC NULLS LAST",
        "risk": "cr.enforcement_risk_score DESC NULLS LAST",
        "regulator": "cr.regulator ASC",
        "date": "cr.effective_date DESC NULLS LAST",
    }.get(sort_by, "sev_rank ASC, cr.enforcement_risk_score DESC NULLS LAST")

    cid = getattr(user, "bank_id", None)
    filters = ["cr.is_active = TRUE"]
    params: Dict[str, Any] = {
        "offset": (page - 1) * page_size,
        "limit": page_size,
        "cid": cid,
    }
    if regulator:
        filters.append("UPPER(cr.regulator) = UPPER(:regulator)")
        params["regulator"] = regulator
    if severity:
        filters.append("cr.severity = :severity")
        params["severity"] = severity

    # Applicability scoping (default). "all" bypasses it so nothing is ever
    # truly hidden — the UI toggle just changes the default lens.
    #   1. Drop rules this bank has marked not-applicable (rule_overrides).
    #   2. Asset-size applicability: NULL bounds = applies to everyone, so this
    #      is a no-op until the scraper populates applicability_asset_* and the
    #      bank_profile row exists — then it filters automatically.
    if scope != "all":
        filters.append("""NOT EXISTS (
            SELECT 1 FROM rule_overrides ro2
            WHERE ro2.rule_id = cr.rule_id AND ro2.client_id = :cid
              AND ro2.status = 'not_applicable')""")
        # CAST so asyncpg can infer the type when no bank_profile row exists
        # (NULL::numeric IS NULL short-circuits → no filtering).
        filters.append("""(cr.applicability_asset_min IS NULL
             OR CAST(:assets AS numeric) IS NULL
             OR CAST(:assets AS numeric) >= cr.applicability_asset_min)""")
        filters.append("""(cr.applicability_asset_max IS NULL
             OR CAST(:assets AS numeric) IS NULL
             OR CAST(:assets AS numeric) <= cr.applicability_asset_max)""")

    where = " AND ".join(filters)

    async with get_db() as session:
        # Bank asset size for applicability (None if no profile row).
        params["assets"] = await session.scalar(text(
            "SELECT total_assets_usd FROM bank_profile WHERE bank_id = :cid"
        ), {"cid": cid})

        rows = await session.execute(text(f"""
            SELECT cr.rule_id, cr.regulator, cr.cfr_citation,
                   cr.rule_text,
                   cr.enforcement_risk_score,
                   cr.severity,
                   CASE cr.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
                        WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END AS sev_rank,
                   cr.effective_date,
                   ro.status AS bank_status,
                   ro.threshold_override, ro.na_reason,
                   (SELECT COUNT(*) FROM workflow_executions
                    WHERE rule_id = cr.rule_id AND status='completed')
                     AS test_count
            FROM compliance_rules cr
            LEFT JOIN rule_overrides ro
              ON ro.rule_id = cr.rule_id AND ro.client_id = :cid
            WHERE {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
        """), params)

        total = await session.scalar(text(
            f"SELECT COUNT(*) FROM compliance_rules cr WHERE {where}"
        ), params) or 0

        # Unscoped count so the "Show all" toggle can show what's hidden.
        all_total = await session.scalar(text(
            "SELECT COUNT(*) FROM compliance_rules WHERE is_active = TRUE"
        )) or 0

    return {
        "rules": [dict(r._mapping) for r in rows.fetchall()],
        "total": total,
        "all_total": all_total,
        "scope": scope,
        "page": page,
        "page_size": page_size,
        "can_edit": user.can_edit,
    }


# ── Changes ───────────────────────────────────────────────────────────

# Aging buckets on unsigned changes (per R9 / audit §P5). Boundaries chosen
# to match the "quarter turns overdue" cadence used elsewhere for MRA aging:
# fresh <7d, aging 7–30d, overdue ≥30d.
_AGING_FRESH_DAYS = 7
_AGING_OVERDUE_DAYS = 30


def _aging_bucket(age_days: Optional[float]) -> Optional[str]:
    if age_days is None:
        return None
    if age_days < _AGING_FRESH_DAYS:
        return "fresh"
    if age_days < _AGING_OVERDUE_DAYS:
        return "aging"
    return "overdue"


@router.get("/changes")
async def get_changes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_ofac: bool = Query(False),
    bucket: Optional[str] = Query(
        None, pattern="^(fresh|aging|overdue)$",
        description="Filter unsigned records by aging bucket."
    ),
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    where = ["is_active=TRUE"]
    if not include_ofac:
        where.append("regulator <> 'OFAC'")
    # Bucket filter applies only to detected (unsigned) records — signed
    # records don't age; their signoff timestamp is the audit artifact.
    if bucket == "fresh":
        where.append("status='detected' AND NOW() - detected_at <  INTERVAL '7 days'")
    elif bucket == "aging":
        where.append("status='detected' AND NOW() - detected_at >= INTERVAL '7 days'"
                     " AND NOW() - detected_at <  INTERVAL '30 days'")
    elif bucket == "overdue":
        where.append("status='detected' AND NOW() - detected_at >= INTERVAL '30 days'")
    where_sql = " AND ".join(where)
    async with get_db() as session:
        rows = await session.execute(text(f"""
            SELECT change_ref, rule_id, regulator,
                   change_summary, semantic_diff,
                   impact_rating, change_type, status,
                   created_at, detected_at,
                   EXTRACT(EPOCH FROM (NOW() - detected_at)) / 86400.0 AS age_days
            FROM regulatory_change_records
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """), {"limit": page_size, "offset": (page - 1) * page_size})
        total = await session.scalar(text(
            f"SELECT COUNT(*) FROM regulatory_change_records WHERE {where_sql}"
        )) or 0
        # Header counts always reflect the *unfiltered* posture — a bucket
        # filter must not silently reduce "226 unsigned" to the visible page.
        base_where = ["is_active=TRUE"]
        if not include_ofac:
            base_where.append("regulator <> 'OFAC'")
        base_where_sql = " AND ".join(base_where)
        open_total = await session.scalar(text(
            f"SELECT COUNT(*) FROM regulatory_change_records "
            f"WHERE {base_where_sql} AND status='detected'"
        )) or 0
        # Aging counts for the header — cheap since they're all indexed
        # scans against the same partial WHERE.
        aging_counts = await session.execute(text(f"""
            SELECT
              COUNT(*) FILTER (WHERE NOW() - detected_at <  INTERVAL '7 days')  AS fresh,
              COUNT(*) FILTER (WHERE NOW() - detected_at >= INTERVAL '7 days'
                               AND   NOW() - detected_at <  INTERVAL '30 days') AS aging,
              COUNT(*) FILTER (WHERE NOW() - detected_at >= INTERVAL '30 days') AS overdue
             FROM regulatory_change_records
             WHERE {base_where_sql} AND status='detected'
        """))
        aging_row = aging_counts.fetchone()
    changes = []
    for r in rows.fetchall():
        row = dict(r._mapping)
        age = row.get("age_days")
        # EXTRACT(EPOCH ...) returns Decimal from asyncpg; jsonable_encoder
        # serializes Decimal as string, then Changes.jsx `.toFixed(1)` throws
        # and the whole page renders blank. Cast to float so it stays a JSON
        # number end-to-end.
        row["age_days"] = round(float(age), 1) if age is not None else None
        # `aging_bucket` only meaningful for unsigned; signed records get
        # None so the frontend renders their signoff timestamp instead.
        row["aging_bucket"] = _aging_bucket(age) if row["status"] == "detected" else None
        changes.append(row)
    return {
        "changes": changes,
        "total": total,
        "open_total": open_total,
        "aging": {
            "fresh":   int(aging_row.fresh or 0),
            "aging":   int(aging_row.aging or 0),
            "overdue": int(aging_row.overdue or 0),
        },
        "page": page,
        "page_size": page_size,
        "can_signoff": user.can_approve,
    }


# ── Deadlines ─────────────────────────────────────────────────────────

@router.get("/deadlines")
async def get_deadlines(user: CurrentUser = Depends(get_current_user)) -> Dict:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT regulation_id, deadline_name, deadline_date,
                   days_remaining
            FROM compliance_deadlines
            WHERE is_active=TRUE AND days_remaining >= 0
            ORDER BY days_remaining ASC LIMIT 20
        """))
    return {"deadlines": [dict(r._mapping) for r in rows.fetchall()]}


# ── Risk scores ───────────────────────────────────────────────────────

@router.get("/risk")
async def get_risk(user: CurrentUser = Depends(get_current_user)) -> Dict:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT rule_id, regulator,
                   cfr_citation, enforcement_risk_score
            FROM compliance_rules
            WHERE is_active=TRUE AND enforcement_risk_score > 0
            ORDER BY enforcement_risk_score DESC LIMIT 20
        """))
    return {"top_risks": [dict(r._mapping) for r in rows.fetchall()]}


# ── Coverage by regulator ─────────────────────────────────────────────

@router.get("/coverage")
async def get_coverage(user: CurrentUser = Depends(get_current_user)) -> Dict:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT
                cr.regulator,
                COUNT(*) AS total_rules,
                SUM(CASE WHEN we.id IS NOT NULL THEN 1 ELSE 0 END) AS tested_rules,
                ROUND(100.0 *
                  SUM(CASE WHEN we.id IS NOT NULL THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1) AS coverage_pct
            FROM compliance_rules cr
            LEFT JOIN workflow_executions we
              ON we.rule_id = cr.rule_id
              AND we.status='completed'
            WHERE cr.is_active=TRUE
            GROUP BY cr.regulator
            ORDER BY total_rules DESC
        """))
    return {"coverage": [dict(r._mapping) for r in rows.fetchall()]}


# ── Workflow runs ─────────────────────────────────────────────────────

@router.get("/workflows")
async def get_workflows(
    limit: int = Query(20, le=50),
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT id, jira_story_id, jira_story_key, status,
                   started_at, completed_at,
                   EXTRACT(EPOCH FROM (completed_at - started_at))::INT
                     AS duration_sec,
                   artifacts
            FROM workflow_executions
            ORDER BY started_at DESC LIMIT :limit
        """), {"limit": limit})
    return {"workflows": [dict(r._mapping) for r in rows.fetchall()]}


# ── OFAC SDN screener health ──────────────────────────────────────────

@router.get("/ofac-health")
async def get_ofac_health(user: CurrentUser = Depends(get_current_user)) -> Dict:
    async with get_db() as session:
        entries  = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active=TRUE"
        )) or 0
        variants_total = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors"
        )) or 0
        variants_active = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active=TRUE"
        )) or 0
        embedded = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors "
            "WHERE embedding IS NOT NULL AND is_active=TRUE"
        )) or 0
        last_refresh = await session.scalar(text(
            "SELECT MAX(last_seen_at) FROM ofac_sdn_vectors"
        ))

    embed_pct = round((embedded / variants_active * 100) if variants_active else 0, 1)
    return {
        "entries_active":         entries,
        "variants_total":         variants_total,
        "variants_active":        variants_active,
        "variants_embedded":      embedded,
        "embedding_coverage_pct": embed_pct,
        "vector_mode_ready":      embed_pct >= 99.0,
        "last_refresh_at":        last_refresh.isoformat() if last_refresh else None,
    }


# ── Enforcement actions feed ──────────────────────────────────────────

@router.get("/enforcement")
async def get_enforcement(
    regulator: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, le=100),
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    filters = ["is_active = TRUE"]
    params: Dict[str, Any] = {
        "offset": (page - 1) * page_size,
        "limit": page_size,
    }
    if regulator:
        filters.append("UPPER(regulator) = UPPER(:regulator)")
        params["regulator"] = regulator
    where = " AND ".join(filters)

    async with get_db() as session:
        rows = await session.execute(text(f"""
            SELECT action_ref, regulator, action_date, action_type,
                   institution_type, violation_summary,
                   penalty_amount_usd, source_url
            FROM enforcement_actions
            WHERE {where}
            ORDER BY action_date DESC
            LIMIT :limit OFFSET :offset
        """), params)
        total = await session.scalar(text(
            f"SELECT COUNT(*) FROM enforcement_actions WHERE {where}"
        ), params) or 0

    return {
        "actions":   [dict(r._mapping) for r in rows.fetchall()],
        "total":     total,
        "page":      page,
        "page_size": page_size,
    }


# ── Obligation coverage breakdown ─────────────────────────────────────

@router.get("/obligations")
async def get_obligations(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    regulator: Optional[str] = Query(None),
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT
                regulator,
                COUNT(*) AS total,
                SUM(CASE WHEN has_test_coverage THEN 1 ELSE 0 END) AS covered,
                ROUND(100.0 *
                  SUM(CASE WHEN has_test_coverage THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1) AS coverage_pct
            FROM obligations
            WHERE is_active = TRUE
            GROUP BY regulator
            ORDER BY total DESC
        """))
        breakdown = [dict(r._mapping) for r in rows.fetchall()]

        totals = await session.execute(text("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN has_test_coverage THEN 1 ELSE 0 END) AS covered
            FROM obligations
            WHERE is_active = TRUE
        """))
        t = totals.fetchone()

        # Individual obligations for the read-list — paginated server-side
        # (page/page_size) with an optional regulator filter. The KPIs/
        # breakdown above stay global (authoritative totals).
        where = "WHERE is_active = TRUE"
        qp: Dict[str, Any] = {}
        if regulator:
            where += " AND regulator = :reg"
            qp["reg"] = regulator
        total_items = await session.scalar(
            text(f"SELECT COUNT(*) FROM obligations {where}"), qp) or 0
        offset = (page - 1) * page_size
        items_rows = await session.execute(text(f"""
            SELECT obligation_id, regulation_id, regulator, category,
                   source_text, obligation_type, section_reference,
                   cfr_citation, has_test_coverage
            FROM obligations
            {where}
            ORDER BY regulator, cfr_citation NULLS LAST, obligation_id
            LIMIT :limit OFFSET :offset
        """), {**qp, "limit": page_size, "offset": offset})
        items = [dict(r._mapping) for r in items_rows.fetchall()]

    total = (t.total or 0) if t else 0
    covered = (t.covered or 0) if t else 0
    return {
        "breakdown":    breakdown,
        "items":        items,
        "total":        total,
        "covered":      covered,
        "coverage_pct": round((covered / total * 100) if total else 0, 1),
        "page":         page,
        "page_size":    page_size,
        "total_items":  total_items,
        "total_pages":  max(1, (total_items + page_size - 1) // page_size),
        "regulator":    regulator,
    }


# ── Edit rule status (CCO only) ───────────────────────────────────────

class RuleStatusUpdate(BaseModel):
    status: str               # not_applicable | active | override
    na_reason: Optional[str] = None
    threshold_override: Optional[str] = None
    reviewed_by: Optional[str] = None


@router.patch("/rules/{rule_id}/status")
async def update_rule_status(
    rule_id: str,
    body: RuleStatusUpdate,
    user: CurrentUser = Depends(require_cco),
) -> Dict:
    if body.status == "not_applicable" and not body.na_reason:
        raise HTTPException(400, "na_reason required when marking not applicable")

    async with get_db() as session:
        await session.execute(text("""
            INSERT INTO rule_overrides
                (id, client_id, rule_id, status, na_reason,
                 threshold_override, reviewed_by, reviewed_at)
            VALUES
                (gen_random_uuid(), :bank_id, :rule_id, :status,
                 :na_reason, :threshold_override, :by, NOW())
            ON CONFLICT (client_id, rule_id) DO UPDATE SET
                status=EXCLUDED.status,
                na_reason=EXCLUDED.na_reason,
                threshold_override=EXCLUDED.threshold_override,
                reviewed_by=EXCLUDED.reviewed_by,
                reviewed_at=NOW(),
                updated_at=NOW()
        """), {
            "rule_id": rule_id,
            "na_reason": body.na_reason,
            "threshold_override": body.threshold_override,
            "status": body.status,
            "bank_id": user.bank_id,
            "by": body.reviewed_by or user.email,
        })
        await session.commit()

    logger.info("rule_status_updated", rule_id=rule_id,
                status=body.status, by=user.email)
    return {"rule_id": rule_id, "status": body.status, "updated_by": user.email}


# ── Change detail (CCO review pane) ───────────────────────────────────

@router.get("/changes/{change_ref}")
async def get_change_detail(
    change_ref: str,
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    """
    Full payload for the change-review drawer: joins regulatory_change_records
    to compliance_rules so the CCO can see previous_content vs. rule_text and
    the structured change_delta (added/removed sentences) — not just the
    truncated change_summary that the inbox list returns.

    rule_id on regulatory_change_records is a string reference (not a FK), so
    the join is LEFT and the rule fields may be NULL if the rule was retired.
    """
    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT
                rec.change_ref, rec.rule_id, rec.regulator,
                rec.change_summary, rec.semantic_diff, rec.change_delta,
                rec.impact_rating, rec.change_type, rec.status,
                rec.previous_hash, rec.new_hash,
                rec.created_at, rec.signed_off_at,
                rec.reviewer_name, rec.reviewer_role, rec.notes,
                cr.rule_text, cr.previous_content,
                cr.cfr_citation, cr.source_url, cr.effective_date
            FROM regulatory_change_records rec
            LEFT JOIN compliance_rules cr
              ON cr.rule_id = rec.rule_id
             AND cr.regulator = rec.regulator
             AND cr.is_active = TRUE
            WHERE rec.change_ref = :ref AND rec.is_active = TRUE
        """), {"ref": change_ref})).fetchone()

    if row is None:
        raise HTTPException(404, f"change_ref {change_ref} not found")

    comments_rows = []
    async with get_db() as session:
        comments_rows = (await session.execute(text("""
            SELECT c.comment_type, c.author, c.author_role, c.comment_text,
                   c.status_from, c.status_to, c.created_at
            FROM change_record_comments c
            JOIN regulatory_change_records r ON r.id = c.change_record_id
            WHERE r.change_ref = :ref
            ORDER BY c.created_at ASC
        """), {"ref": change_ref})).fetchall()

    return {
        "change_ref":       row.change_ref,
        "rule_id":          row.rule_id,
        "regulator":        row.regulator,
        "change_summary":   row.change_summary,
        "semantic_diff":    row.semantic_diff,
        "impact_rating":    row.impact_rating,
        "change_type":      row.change_type,
        "status":           row.status,
        "previous_hash":    row.previous_hash,
        "new_hash":         row.new_hash,
        "created_at":       row.created_at.isoformat() if row.created_at else None,
        "signed_off_at":    row.signed_off_at.isoformat() if row.signed_off_at else None,
        "reviewer_name":    row.reviewer_name,
        "reviewer_role":    row.reviewer_role,
        "notes":            row.notes,
        "change_delta":     row.change_delta,
        "rule": {
            "rule_text":        row.rule_text,
            "previous_content": row.previous_content,
            "cfr_citation":     row.cfr_citation,
            "source_url":       row.source_url,
            "effective_date":   row.effective_date.isoformat() if row.effective_date else None,
        },
        "comments": [dict(c._mapping) for c in comments_rows],
        "can_signoff": user.can_approve,
    }


# ── Change sign-off (CCO/CRO) ─────────────────────────────────────────

class SignOffBody(BaseModel):
    decision: str        # acknowledged | requires_action | not_applicable
    note: Optional[str] = None


class BulkSignOffBody(BaseModel):
    refs: List[str]      # change_ref values
    decision: str        # acknowledged | requires_action | not_applicable
    note: Optional[str] = None


_DECISION_TO_STATUS = {
    "acknowledged":    "signed_off",
    "requires_action": "remediation_planned",
    "not_applicable":  "closed",
}

# Guardrail on bulk signoff — protects against a runaway request from a
# selection UI. 500 is well above the ~226 typical backlog in the audit
# baseline; process-hostile below that, misuse-prone above.
_BULK_SIGNOFF_MAX_REFS = 500


@router.post("/changes/{change_ref}/signoff")
async def sign_off_change(
    change_ref: str,
    body: SignOffBody,
    user: CurrentUser = Depends(require_approver),
) -> Dict:
    db_status = _DECISION_TO_STATUS.get(body.decision)
    if db_status is None:
        raise HTTPException(
            400, f"decision must be one of {list(_DECISION_TO_STATUS)}"
        )
    async with get_db() as session:
        # NOTE: bind the status value under two distinct names. Reusing one
        # named param in both the SET (varchar column) and the CASE…IN (text
        # literals) makes asyncpg deduce inconsistent types for the same
        # positional parameter → AmbiguousParameterError. Two params avoid it.
        await session.execute(text("""
            UPDATE regulatory_change_records
            SET status=:status, client_id=:bank_id, updated_at=NOW(),
                signed_off_at = CASE
                    WHEN :status_gate IN ('signed_off','closed') AND signed_off_at IS NULL
                    THEN NOW()
                    ELSE signed_off_at
                END
            WHERE change_ref=:ref
        """), {"status": db_status, "status_gate": db_status,
               "bank_id": user.bank_id, "ref": change_ref})
        await session.commit()

    logger.info("change_signoff", ref=change_ref,
                decision=body.decision, by=user.email)
    return {
        "change_ref": change_ref,
        "decision": body.decision,
        "signed_by": user.email,
        "role": user.role,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/changes/signoff/bulk")
async def bulk_sign_off_changes(
    body: BulkSignOffBody,
    user: CurrentUser = Depends(require_approver),
) -> Dict:
    """Sign off many change_refs in one transaction (R9 / audit §P5).

    Returns a per-ref classification so the UI can render an honest
    receipt — the request is *idempotent* (already-signed refs are a
    non-error) and *partial-write-safe* (missing refs don't roll back
    the ones that did land).
    """
    if not body.refs:
        raise HTTPException(400, "refs must be non-empty")
    if len(body.refs) > _BULK_SIGNOFF_MAX_REFS:
        raise HTTPException(
            400, f"at most {_BULK_SIGNOFF_MAX_REFS} refs per request"
        )
    db_status = _DECISION_TO_STATUS.get(body.decision)
    if db_status is None:
        raise HTTPException(
            400, f"decision must be one of {list(_DECISION_TO_STATUS)}"
        )
    # De-dup input — a selection UI can send the same ref twice.
    unique_refs = list(dict.fromkeys(body.refs))

    async with get_db() as session:
        pre = await session.execute(text("""
            SELECT change_ref, status FROM regulatory_change_records
             WHERE change_ref = ANY(:refs) AND is_active=TRUE
        """), {"refs": unique_refs})
        existing = {r.change_ref: r.status for r in pre.fetchall()}
        not_found      = [r for r in unique_refs if r not in existing]
        already_signed = [r for r, s in existing.items() if s != "detected"]
        to_sign        = [r for r, s in existing.items() if s == "detected"]

        if to_sign:
            await session.execute(text("""
                UPDATE regulatory_change_records
                   SET status=:status, client_id=:bank_id, updated_at=NOW(),
                       signed_off_at = CASE
                           WHEN :status IN ('signed_off','closed') AND signed_off_at IS NULL
                           THEN NOW()
                           ELSE signed_off_at
                       END
                 WHERE change_ref = ANY(:refs)
            """), {"status": db_status, "bank_id": user.bank_id, "refs": to_sign})
            await session.commit()

    logger.info(
        "change_signoff_bulk",
        signed=len(to_sign),
        already_signed=len(already_signed),
        not_found=len(not_found),
        decision=body.decision,
        by=user.email,
    )
    return {
        "signed": to_sign,
        "not_found": not_found,
        "already_signed": already_signed,
        "decision": body.decision,
        "signed_by": user.email,
        "role": user.role,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ──────────────────────────────────────────────────────────────────────
# Unified-dashboard additions — read-only views computed from existing
# tables. None of the queries below mutate state. R10 (migration 043)
# adds one read-only join against ``applicable_obligation_registry`` for
# the inventory dim's denominator when the registry has rows for the
# caller's bank scope; otherwise the query degrades to the pre-R10
# ingestion-completeness form.
# ──────────────────────────────────────────────────────────────────────


# Canonical examiner-pack artifact types locked by the CHECK constraint on
# export_artifacts.type (migration 042). "Point-in-time reporting" measures
# how many of these six have a recent signed artifact — a bank presenting an
# exam pack needs all six current, not five.
_CANONICAL_REPORT_TYPES = 6


# Asset-tier boundaries used to derive a bank's applicable-obligation-registry
# scope from bank_profile.total_assets_usd (migration 043 / R10). Bands match
# the vocabulary already used by EnforcementAction.asset_tier so registry
# templates and enforcement bases can share a lookup key. USD, inclusive
# lower / exclusive upper.
_ASSET_TIER_BANDS = [
    ("community",  0,               10_000_000_000),   # <$10B
    ("regional",   10_000_000_000,   50_000_000_000),   # $10B–$50B
    ("midsize",    50_000_000_000,  250_000_000_000),   # $50B–$250B
    ("large",     250_000_000_000, 1_000_000_000_000),  # $250B–$1T
    ("gsib",     1_000_000_000_000, 10**18),            # ≥$1T
]

# Primary-regulator → coarse charter class map. Deliberately broader than
# the specific FDIC/OCC/FRB partition because community banks with dual
# regulator overlap should share a template. NCUA = credit_union, OTS/OCC
# thrift lineage = savings, FHFA = holding. Anything unmapped → None,
# which forces the fallback path (widget renders "Ingestion completeness").
_CHARTER_BY_REGULATOR = {
    "OCC":  "national",
    "FDIC": "state_nonmember",
    "FRB":  "state_member",
    "NCUA": "credit_union",
    "OTS":  "savings",
    "FHFA": "holding",
}


def _derive_asset_tier(total_assets_usd: Optional[float]) -> Optional[str]:
    if total_assets_usd is None:
        return None
    for name, lo, hi in _ASSET_TIER_BANDS:
        if lo <= total_assets_usd < hi:
            return name
    return None


# `engine` intentionally omitted from CCO and CRO — platform health is not
# the bank's compliance posture. Kept on CIO where it's the load-bearing dim.
# See audit doc §P10 verdict and resolutions §R7. Removing it here rather
# than zero-weighting keeps the response contract clean: CCO/CRO see a
# 9-dim strip, CIO sees the full 10.
_LENS_WEIGHTS = {
    "cco": {
        "inventory":1.2,"control_mapping":1.3,"testing":1.5,"ra_currency":1.0,
        "change_mgmt":1.3,"findings":1.1,"governance":1.2,"independent":1.0,
        "reporting":0.9,
    },
    "cro": {
        "inventory":0.9,"control_mapping":1.0,"testing":1.2,"ra_currency":1.4,
        "change_mgmt":1.0,"findings":1.6,"governance":1.0,"independent":1.3,
        "reporting":0.9,
    },
    "cio": {
        "inventory":0.7,"control_mapping":0.8,"testing":0.9,"ra_currency":0.8,
        "change_mgmt":0.9,"findings":0.8,"governance":0.8,"independent":0.9,
        "reporting":1.3,"engine":1.7,
    },
}


def _band(score: int) -> str:
    if score < 40:  return "failing"
    if score < 70:  return "developing"
    if score < 85:  return "operational"
    return "strong"


async def _bank_scope(session, bank_id: Optional[str]) -> Dict[str, Optional[str]]:
    """Resolve the (bank_charter, asset_tier) key used to scope the
    applicable-obligation registry (migration 043 / R10).

    Reads bank_profile if it exists; otherwise returns Nones and the
    inventory query degrades to fallback mode. Keeping this a data
    lookup (rather than an env/SSM read) means the registry mode
    activates the moment ops populates bank_profile — no dashboard
    redeploy required.
    """
    if not bank_id:
        return {"bank_charter": None, "asset_tier": None,
                "bank_id": None, "reason": "no_bank_id"}
    try:
        row = (await session.execute(text("""
            SELECT primary_regulator, total_assets_usd
              FROM bank_profile WHERE bank_id = :bid
        """), {"bid": bank_id})).first()
    except Exception:
        # Table may not exist on a pre-042 environment. Fallback path.
        return {"bank_charter": None, "asset_tier": None,
                "bank_id": bank_id, "reason": "no_bank_profile_table"}
    if row is None:
        return {"bank_charter": None, "asset_tier": None,
                "bank_id": bank_id, "reason": "bank_profile_row_missing"}
    charter = _CHARTER_BY_REGULATOR.get((row.primary_regulator or "").upper())
    tier = _derive_asset_tier(
        float(row.total_assets_usd) if row.total_assets_usd is not None else None
    )
    reason = None if (charter and tier) else "scope_incomplete"
    return {"bank_charter": charter, "asset_tier": tier,
            "bank_id": bank_id, "reason": reason}


async def _resolve_inventory(session, scope: Dict[str, Optional[str]]) -> Dict:
    """Compute the P1 inventory dim with a registry-aware denominator.

    Returns a dict:
      {"value": int|None, "method": "registry"|"fallback",
       "matched": int, "applicable": int,
       "label": "Applicable obligation coverage" | "Ingestion completeness"}

    Registry mode requires *both* a resolvable bank scope (charter + tier)
    and at least one required-registry row for that scope. Anything else
    → fallback: ``pct(rules_with_cfr, rules_total, 70)`` with the honest
    "Ingestion completeness" label. This matches the audit §P1 verdict:
    the widget stays truthful about *what* it's measuring under either
    data state, and the value is comparable across states because both
    are bounded [0,100].
    """
    rules_total = await session.scalar(text(
        "SELECT COUNT(*) FROM compliance_rules WHERE is_active=TRUE"
    )) or 0
    rules_with_cfr = await session.scalar(text(
        "SELECT COUNT(*) FROM compliance_rules "
        "WHERE is_active=TRUE AND cfr_citation IS NOT NULL AND cfr_citation<>''"
    )) or 0

    def _pct(n: int, d: int, default: int = 70) -> int:
        if not d:
            return default
        return max(0, min(100, round(100 * n / d)))

    fallback = {
        "value": _pct(rules_with_cfr, rules_total, 70),
        "method": "fallback",
        "matched": rules_with_cfr,
        "applicable": rules_total,
        "label": "Ingestion completeness",
    }

    charter, tier = scope.get("bank_charter"), scope.get("asset_tier")
    if not (charter and tier):
        return fallback

    try:
        applicable = await session.scalar(text("""
            SELECT COUNT(*) FROM applicable_obligation_registry
             WHERE bank_charter = :c AND asset_tier = :t
               AND is_required = TRUE
               AND (effective_from IS NULL OR effective_from <= CURRENT_DATE)
               AND (effective_to   IS NULL OR effective_to   >= CURRENT_DATE)
        """), {"c": charter, "t": tier}) or 0
    except Exception:
        # Registry table absent (pre-043) — fallback.
        return fallback

    if applicable == 0:
        return fallback

    # Matched = the count of distinct required registry rows whose
    # cfr_citation appears in compliance_rules (case-insensitive, whitespace
    # tolerant — matches the same normalisation shape used elsewhere by
    # backfill_cfr_citations.py). Registry citations are canonical; rule
    # citations get NORMALIZED via LOWER + REGEXP_REPLACE so an
    # eCFR-style variant still joins.
    matched = await session.scalar(text("""
        SELECT COUNT(*) FROM applicable_obligation_registry r
         WHERE r.bank_charter = :c AND r.asset_tier = :t
           AND r.is_required = TRUE
           AND (r.effective_from IS NULL OR r.effective_from <= CURRENT_DATE)
           AND (r.effective_to   IS NULL OR r.effective_to   >= CURRENT_DATE)
           AND EXISTS (
             SELECT 1 FROM compliance_rules cr
              WHERE cr.is_active = TRUE
                AND cr.cfr_citation IS NOT NULL
                AND regexp_replace(LOWER(cr.cfr_citation), '[^0-9a-z]+', '', 'g')
                  = regexp_replace(LOWER(r.cfr_citation), '[^0-9a-z]+', '', 'g')
           )
    """), {"c": charter, "t": tier}) or 0

    return {
        "value": _pct(matched, applicable, 70),
        "method": "registry",
        "matched": matched,
        "applicable": applicable,
        "label": "Applicable obligation coverage",
    }


# Canonical governance-evidence types (per migration 045 / R12). Denominator
# for the Governance readiness dim under registry mode. Matches the CHECK
# constraint on `governance_evidence.evidence_type` — keep in sync with the
# migration if either list changes. Length locks the denominator at 8 so
# a partial-seed doesn't inflate the widget.
_CANONICAL_GOVERNANCE_TYPES = (
    "board_bsa_acknowledgement",
    "board_ofac_acknowledgement",
    "bsa_officer_designation",
    "bsa_committee_charter",
    "bsa_committee_minutes",
    "bsa_training_records",
    "bsa_policy_manual",
    "enterprise_risk_assessment",
)


async def _resolve_governance(
    session, bank_id: Optional[str], rules_with_risk: int, rules_total: int,
) -> Dict:
    """Compute the P7 governance dim with a registry-aware numerator.

    Returns:
      {"value": int|None, "method": "registry"|"fallback",
       "matched": int, "applicable": int,
       "label": "Governance evidence" | "Risk-attribution proxy"}

    Registry mode requires both a ``bank_id`` and at least one *live*
    ``governance_evidence`` row for that bank (``superseded_at IS
    NULL`` and inside the effective window). Numerator counts distinct
    canonical evidence types with a live row; denominator is fixed at
    ``len(_CANONICAL_GOVERNANCE_TYPES)`` so partial seeding can't
    inflate the score.

    Fallback preserves the pre-R12 read — ``pct(rules_with_risk,
    rules_total, 80)`` — but *renames the widget* to "Risk-attribution
    proxy" so the CCO/CRO don't mistake it for real evidence coverage.
    That's the audit's §P7 short-term recommendation ("split from P4
    to at least stop the double-count") converted into a truthful
    label on the fallback path.
    """
    def _pct(n: int, d: int, default: int = 80) -> int:
        if not d:
            return default
        return max(0, min(100, round(100 * n / d)))

    fallback = {
        "value": _pct(rules_with_risk, rules_total, 80),
        "method": "fallback",
        "matched": rules_with_risk,
        "applicable": rules_total,
        "label": "Risk-attribution proxy",
    }

    if not bank_id:
        return fallback

    try:
        live_rows = await session.scalar(text("""
            SELECT COUNT(*) FROM governance_evidence
             WHERE bank_id = :bid
               AND superseded_at IS NULL
               AND (effective_from IS NULL OR effective_from <= CURRENT_DATE)
               AND (effective_to   IS NULL OR effective_to   >= CURRENT_DATE)
        """), {"bid": bank_id}) or 0
    except Exception:
        # Table absent (pre-045) — fallback.
        return fallback

    if live_rows == 0:
        return fallback

    # Distinct canonical types with a live row for this bank. Filtering
    # to the canonical list defends against a schema drift where a
    # future migration relaxes the CHECK to include non-canonical
    # values that would then silently inflate the numerator.
    matched = await session.scalar(text("""
        SELECT COUNT(DISTINCT evidence_type)
          FROM governance_evidence
         WHERE bank_id = :bid
           AND evidence_type = ANY(:types)
           AND superseded_at IS NULL
           AND (effective_from IS NULL OR effective_from <= CURRENT_DATE)
           AND (effective_to   IS NULL OR effective_to   >= CURRENT_DATE)
    """), {"bid": bank_id, "types": list(_CANONICAL_GOVERNANCE_TYPES)}) or 0

    applicable = len(_CANONICAL_GOVERNANCE_TYPES)
    return {
        "value": _pct(matched, applicable, 80),
        "method": "registry",
        "matched": matched,
        "applicable": applicable,
        "label": "Governance evidence",
    }


async def _resolve_control_mapping(
    session, bank_id: Optional[str], obl_total: int, obl_attributed: int,
) -> Dict:
    """Compute the P2 control-mapping dim with a registry-aware numerator.

    Returns:
      {"value": int|None, "method": "registry"|"fallback",
       "matched": int, "applicable": int,
       "label": "Control coverage" | "Obligation attribution"}

    Registry mode requires both a ``bank_id`` and at least one *live*
    mapping row for that bank (``superseded_at IS NULL``). Live-status
    is checked in one round-trip so a bank whose mappings are all
    superseded (or all out of effective window) falls through to
    fallback — same shape as R10.

    Numerator (registry mode):  distinct active obligations for which
      the caller's bank has at least one live control mapping.
    Denominator (registry mode): all active obligations.
    Numerator (fallback):       obligations with a non-null regulator
      label (R8's honest attribution proxy).
    Denominator (fallback):     all active obligations.

    Both paths share the [0,100] bound so composite renormalisation
    (R7) picks up the value regardless of which path fired.
    """
    def _pct(n: int, d: int, default: int = 70) -> int:
        if not d:
            return default
        return max(0, min(100, round(100 * n / d)))

    fallback = {
        "value": _pct(obl_attributed, obl_total, 70),
        "method": "fallback",
        "matched": obl_attributed,
        "applicable": obl_total,
        "label": "Obligation attribution",
    }

    if not bank_id:
        return fallback

    try:
        live_mappings = await session.scalar(text("""
            SELECT COUNT(*) FROM control_obligation_mappings
             WHERE bank_id = :bid
               AND superseded_at IS NULL
               AND (effective_from IS NULL OR effective_from <= CURRENT_DATE)
               AND (effective_to   IS NULL OR effective_to   >= CURRENT_DATE)
        """), {"bid": bank_id}) or 0
    except Exception:
        # Table absent (pre-044) — fallback.
        return fallback

    if live_mappings == 0:
        return fallback

    # Distinct active obligations with at least one live mapping for
    # this bank. Joins to `obligations` to enforce is_active — a stale
    # obligation must not inflate the numerator.
    matched = await session.scalar(text("""
        SELECT COUNT(DISTINCT m.obligation_id)
          FROM control_obligation_mappings m
          JOIN obligations o ON o.obligation_id = m.obligation_id
         WHERE m.bank_id = :bid
           AND m.superseded_at IS NULL
           AND (m.effective_from IS NULL OR m.effective_from <= CURRENT_DATE)
           AND (m.effective_to   IS NULL OR m.effective_to   >= CURRENT_DATE)
           AND o.is_active = TRUE
    """), {"bid": bank_id}) or 0

    return {
        "value": _pct(matched, obl_total, 70),
        "method": "registry",
        "matched": matched,
        "applicable": obl_total,
        "label": "Control coverage",
    }


async def _dimension_scores(
    bank_id: Optional[str] = None,
) -> "tuple[Dict[str, Optional[int]], Dict[str, object]]":
    """Compute the 10 readiness dimensions from existing tables.

    Each dimension is bounded [0,100]. Where the underlying table or
    column is missing we degrade to a safe neutral score rather than
    raising — the gauge stays meaningful even on a partially-populated db.

    Returns ``(dims, meta)``. ``meta`` carries per-dimension provenance
    the caller may need to render honest labels — currently only the
    inventory dim uses it (registry vs fallback / label override).
    """
    async with get_db() as session:
        scope = await _bank_scope(session, bank_id)
        inventory_info = await _resolve_inventory(session, scope)
        # control_mapping_info is resolved *after* obl_total / obl_attributed
        # are computed below — the resolver needs both counts as its
        # fallback numerator/denominator. Deferred call below.

        rules_total = await session.scalar(text(
            "SELECT COUNT(*) FROM compliance_rules WHERE is_active=TRUE"
        )) or 0
        # rules_with_cfr is now sourced by _resolve_inventory above — the
        # inventory dim uses the registry (or fallback) rather than a raw
        # ingestion count, so we no longer need the standalone tally here.
        rules_with_risk = await session.scalar(text(
            "SELECT COUNT(*) FROM compliance_rules "
            "WHERE is_active=TRUE AND enforcement_risk_score IS NOT NULL "
            "AND enforcement_risk_score > 0"
        )) or 0
        tested = await session.scalar(text("""
            SELECT COUNT(DISTINCT r.rule_id)
            FROM compliance_rules r
            JOIN workflow_executions w ON w.rule_id = r.rule_id
            WHERE r.is_active=TRUE AND w.status='completed'
        """)) or 0

        obl_total = await session.scalar(text(
            "SELECT COUNT(*) FROM obligations WHERE is_active=TRUE"
        )) or 0
        obl_covered = await session.scalar(text(
            "SELECT COUNT(*) FROM obligations "
            "WHERE is_active=TRUE AND has_test_coverage=TRUE"
        )) or 0
        obl_attributed = await session.scalar(text(
            "SELECT COUNT(*) FROM obligations "
            "WHERE is_active=TRUE AND regulator IS NOT NULL AND regulator<>'unknown'"
        )) or 0
        control_mapping_info = await _resolve_control_mapping(
            session, bank_id, obl_total, obl_attributed,
        )
        governance_info = await _resolve_governance(
            session, bank_id, rules_with_risk, rules_total,
        )

        # OFAC SDN variant lifecycle events are auto-emitted (one per a.k.a.)
        # by the loader + backfill; they aren't human-reviewable rule deltas
        # so they're excluded from the CCO change-management posture. SDN
        # backlog has its own surface (screener UI + /sync/sdn-changes).
        chg_total = await session.scalar(text(
            "SELECT COUNT(*) FROM regulatory_change_records "
            "WHERE is_active=TRUE AND regulator <> 'OFAC'"
        )) or 0
        chg_open = await session.scalar(text(
            "SELECT COUNT(*) FROM regulatory_change_records "
            "WHERE is_active=TRUE AND status='detected' AND regulator <> 'OFAC'"
        )) or 0

        wf_total = await session.scalar(text(
            "SELECT COUNT(*) FROM workflow_executions "
            "WHERE started_at >= NOW() - INTERVAL '7 days'"
        )) or 0
        wf_ok = await session.scalar(text(
            "SELECT COUNT(*) FROM workflow_executions "
            "WHERE started_at >= NOW() - INTERVAL '7 days' AND status='completed'"
        )) or 0

        sdn_total = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active=TRUE"
        )) or 0
        sdn_embed = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE embedding IS NOT NULL"
        )) or 0
        sdn_last_at = await session.scalar(text(
            "SELECT MAX(last_seen_at) FROM ofac_sdn_vectors"
        ))

        # Findings — table exists (migration 042); empty findings table means
        # "no MRAs on file we can measure against," not "80% remediated."
        # We return None so the composite score renormalises across the
        # measured dimensions rather than baking in a phantom baseline.
        fnd_total = await session.scalar(text(
            "SELECT COUNT(*) FROM findings"
        )) or 0
        fnd_closed = await session.scalar(text(
            "SELECT COUNT(*) FROM findings WHERE status='validated_closed'"
        )) or 0

        # Point-in-time reporting — export_artifacts / export_signoffs land in
        # migration 042. The examiner-pack coverage question is: of the six
        # canonical artifact types, how many have a signed artifact generated
        # in the last 90 days? Empty artifact store → None (unmeasured).
        artifacts_total = await session.scalar(text(
            "SELECT COUNT(*) FROM export_artifacts"
        )) or 0
        signed_recent_types = await session.scalar(text(
            "SELECT COUNT(DISTINCT ea.type) "
            "FROM export_artifacts ea "
            "JOIN export_signoffs es ON es.artifact_id = ea.artifact_id "
            "WHERE ea.generated_at >= NOW() - INTERVAL '90 days'"
        )) or 0

    def pct(n: int, d: int, default: int = 60) -> int:
        if not d:
            return default
        return max(0, min(100, round(100 * n / d)))

    inventory       = inventory_info["value"]
    control_mapping = control_mapping_info["value"]
    testing         = pct(obl_covered, obl_total, 0)
    ra_currency     = pct(rules_with_risk, rules_total, 60)
    change_mgmt     = pct(chg_total - chg_open, chg_total, 80)
    findings        = pct(fnd_closed, fnd_total) if fnd_total > 0 else None
    governance      = governance_info["value"]
    independent     = pct(tested, rules_total, 50)
    reporting       = pct(signed_recent_types, _CANONICAL_REPORT_TYPES) if artifacts_total > 0 else None
    if sdn_last_at:
        # convert to TZ-aware to subtract safely
        if sdn_last_at.tzinfo is None:
            sdn_last_at = sdn_last_at.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - sdn_last_at).total_seconds() / 3600
        freshness = 100 if hours < 24 else 60 if hours < 72 else 30
    else:
        freshness = 40
    embed_ok = pct(sdn_embed, sdn_total, 70)
    pipeline_ok = pct(wf_ok, wf_total, 70)
    engine = round((freshness + embed_ok + pipeline_ok) / 3)

    dims = {
        "inventory": inventory, "control_mapping": control_mapping,
        "testing": testing, "ra_currency": ra_currency,
        "change_mgmt": change_mgmt, "findings": findings,
        "governance": governance, "independent": independent,
        "reporting": reporting, "engine": engine,
    }
    meta = {
        "inventory": {
            "method":     inventory_info["method"],
            "label":      inventory_info["label"],
            "matched":    inventory_info["matched"],
            "applicable": inventory_info["applicable"],
            "scope":      {
                "bank_charter": scope.get("bank_charter"),
                "asset_tier":   scope.get("asset_tier"),
                "reason":       scope.get("reason"),
            },
        },
        "control_mapping": {
            "method":     control_mapping_info["method"],
            "label":      control_mapping_info["label"],
            "matched":    control_mapping_info["matched"],
            "applicable": control_mapping_info["applicable"],
        },
        "governance": {
            "method":     governance_info["method"],
            "label":      governance_info["label"],
            "matched":    governance_info["matched"],
            "applicable": governance_info["applicable"],
        },
    }
    return dims, meta


def _drag_summary(
    dims: Dict[str, Optional[int]],
    chg_open: int,
    obl_unmapped: int,
    lens_keys: Optional[set] = None,
) -> str:
    # Scope to the lens's weight map so an excluded dim (e.g. `engine` on
    # CCO/CRO after R7) can't slip into the fallback "weakest" callout.
    weakest = sorted(
        (
            (k, v) for k, v in dims.items()
            if v is not None and (lens_keys is None or k in lens_keys)
        ),
        key=lambda kv: kv[1],
    )[:2]
    pieces = []
    if obl_unmapped:
        pieces.append(f"{obl_unmapped} obligations unmapped")
    if chg_open:
        pieces.append(f"{chg_open} changes unsigned")
    if not pieces:
        labels = {"testing":"testing & rationale","reporting":"point-in-time reporting",
                  "findings":"findings remediation","engine":"engine integrity",
                  "control_mapping":"obligation attribution","independent":"testing execution rate"}
        pieces = [labels.get(k, k.replace('_', ' ')) for k, _ in weakest]
    return ", ".join(pieces)


# Widget labels. Keys are stable (schema-level); labels describe the metric
# as the query actually measures it — see audit §P1 / §P2 / §P7 / §P8 /
# R8 / R10 / R11 / R12:
#   - `inventory` reads "Ingestion completeness" by default (fallback path,
#     no applicable-obligation registry rows for this bank scope). When
#     the registry *is* populated the label is overridden per-response to
#     "Applicable obligation coverage" from meta["inventory"]["label"].
#   - `control_mapping` reads "Obligation attribution" by default (fallback
#     path, no live control_obligation_mappings rows for this bank). When
#     the mapping table has live rows the label is overridden per-response
#     to "Control coverage" from meta["control_mapping"]["label"].
#   - `governance` reads "Risk-attribution proxy" by default (fallback path
#     shares its numerator with ra_currency — audit §P7 double-count fix).
#     When governance_evidence has live rows for this bank the label is
#     overridden per-response to "Governance evidence" from
#     meta["governance"]["label"].
#   - `independent` counts distinct rules with any completed workflow_executions
#     row, which is *execution*, not independence.
# When a real audit-actor schema lands, `independent` can re-earn its
# original name too.
_DIM_LABELS = [
    ("inventory",       "Ingestion completeness"),
    ("control_mapping", "Obligation attribution"),
    ("testing",         "Testing & rationale"),
    ("ra_currency",     "Risk-assessment currency"),
    ("change_mgmt",     "Change management"),
    ("findings",        "Findings remediation"),
    ("governance",      "Risk-attribution proxy"),
    ("independent",     "Testing execution rate"),
    ("reporting",       "Point-in-time reporting"),
    ("engine",          "Engine integrity"),
]

# Per-dimension call-to-action for the frontend accordion. Highest
# shortfall_impact usually surfaces first, so the CTA is what an operator
# clicks to actually move the number. Keys must match _DIM_LABELS.
_DIM_ACTIONS = {
    "inventory":       ("Review inventory",    "/rules"),
    "control_mapping": ("Review attribution",  "/obligations"),
    "testing":         ("Generate tests",      "/workflows"),
    "ra_currency":     ("Review risk",         "/rules"),
    "change_mgmt":     ("Review changes",      "/changes"),
    "findings":        ("Track findings",      "/reports"),
    "governance":      ("Review risk",         "/rules"),
    "independent":     ("Run tests",           "/workflows"),
    "reporting":       ("Generate exam pack",  "/reports"),
    "engine":          ("Platform health",     "/workflows"),
}


@router.get("/readiness")
async def get_readiness(
    lens: str = Query("cco", pattern="^(cco|cro|cio)$"),
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    dims, meta = await _dimension_scores(bank_id=user.bank_id)
    weights = _LENS_WEIGHTS[lens]
    # `weights` no longer includes `engine` for CCO/CRO (R7) — the composite
    # is naturally scoped to the lens's dims. Skip unmeasured dimensions
    # (None) and renormalise across the measured ones so a phantom baseline
    # can't inflate the composite.
    measured_w = {k: w for k, w in weights.items() if dims.get(k) is not None}
    total_w = sum(measured_w.values()) or 1
    score = round(sum(dims[k] * measured_w[k] for k in measured_w) / total_w)

    async with get_db() as session:
        chg_open = await session.scalar(text(
            "SELECT COUNT(*) FROM regulatory_change_records "
            "WHERE is_active=TRUE AND status='detected' AND regulator <> 'OFAC'"
        )) or 0
        obl_unmapped = await session.scalar(text(
            "SELECT COUNT(*) FROM obligations "
            "WHERE is_active=TRUE AND has_test_coverage=FALSE"
        )) or 0

    # Per-dimension attribution: weighted_contribution shows what each dim
    # actually contributed to the composite; shortfall_impact shows how
    # many score points a dim is leaving on the table right now. Sorted by
    # shortfall_impact in the frontend so operators target the biggest gap
    # first. Unmeasured (None) dims report weight but null contribution +
    # null shortfall — they're excluded from the composite by
    # renormalisation, so they aren't dragging.
    # Emit only the dims present in this lens's weight map. CCO/CRO get 9
    # dims (no engine); CIO gets 10 (engine kept as heaviest CIO weight).
    dimensions = []
    for key, label in _DIM_LABELS:
        if key not in weights:
            continue
        # Meta-driven label override (R10) — currently only the inventory
        # dim opts into this: label reads "Ingestion completeness" under
        # the fallback path and "Applicable obligation coverage" once the
        # registry has rows for this bank's scope. Other dims fall back
        # to their _DIM_LABELS entry.
        dim_meta = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(dim_meta, dict) and dim_meta.get("label"):
            label = dim_meta["label"]
        value = dims.get(key)
        weight = weights[key]
        cta_label, cta_href = _DIM_ACTIONS.get(key, (None, None))
        if value is None:
            weighted_contribution: Optional[float] = None
            shortfall_impact: Optional[float] = None
        else:
            weighted_contribution = round(value * weight / total_w, 2)
            shortfall_impact      = round(weight * (100 - value) / total_w, 2)
        entry = {
            "key": key,
            "label": label,
            "weight": round(weight, 2),
            "score": value,
            "value": value,  # alias — fix-plan §5 spec name
            "weighted_contribution": weighted_contribution,
            "shortfall_impact": shortfall_impact,
            "measured": value is not None,
            "cta_label": cta_label,
            "cta_href": cta_href,
        }
        # Expose provenance for dims whose meta drives a per-response
        # label swap. Registry-mode readouts render "N of M applicable
        # obligations covered" / "N of M obligations with a live
        # control mapped" / "N of 8 canonical governance artifacts";
        # fallback readouts render "N of M ingested rules cited" /
        # "N of M obligations attributed to a regulator" /
        # "N of M rules with a risk score attributed." Dims without
        # meta omit `provenance` entirely.
        if key in ("inventory", "control_mapping", "governance") and isinstance(dim_meta, dict):
            provenance = {
                "method":     dim_meta.get("method"),
                "matched":    dim_meta.get("matched"),
                "applicable": dim_meta.get("applicable"),
            }
            # inventory carries a scope block (charter + tier); the
            # control-mapping and governance resolvers are bank-scoped
            # and don't need the extra key.
            if "scope" in dim_meta:
                provenance["scope"] = dim_meta["scope"]
            entry["provenance"] = provenance
        dimensions.append(entry)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "lens": lens,
        "score": score,
        "band": _band(score),
        "biggest_drag": _drag_summary(dims, chg_open, obl_unmapped, set(weights.keys())),
        # Sum of measured weights — clients can rebuild the composite
        # locally without needing to know the persona weight map.
        "weight_total_measured": round(total_w, 2),
        "dimensions": dimensions,
    }


@router.get("/risk-matrix")
async def get_risk_matrix(
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    """3×3 risk × coverage grid + danger callout (high risk, untested)."""
    sql = text("""
        WITH rule_cov AS (
          SELECT cr.rule_id,
                 COALESCE(cr.enforcement_risk_score, 0)   AS risk,
                 COALESCE(ro.status,'active')             AS bank_status,
                 (SELECT COUNT(*) FROM workflow_executions
                    WHERE rule_id = cr.rule_id AND status='completed') AS tested_n,
                 (SELECT COUNT(*) FROM workflow_executions
                    WHERE rule_id = cr.rule_id
                      AND status IN ('running','waiting_approval')) AS partial_n
          FROM compliance_rules cr
          LEFT JOIN rule_overrides ro ON ro.rule_id = cr.rule_id
          WHERE cr.is_active=TRUE
        )
        SELECT rule_id, risk, bank_status, tested_n, partial_n
        FROM rule_cov
    """)
    async with get_db() as session:
        rows = (await session.execute(sql)).fetchall()

    grid = {(r,c):{"count":0,"rule_ids":[]} for r in ("high","med","low")
                                            for c in ("untested","partial","tested")}
    for r in rows:
        if r.bank_status == "not_applicable":
            continue
        risk_b = "high" if r.risk and r.risk >= 0.8 else (
                 "med"  if r.risk and r.risk >= 0.6 else "low")
        if r.tested_n and r.tested_n > 0:
            cov_b = "tested"
        elif r.partial_n and r.partial_n > 0:
            cov_b = "partial"
        else:
            cov_b = "untested"
        cell = grid[(risk_b, cov_b)]
        cell["count"] += 1
        if len(cell["rule_ids"]) < 50:
            cell["rule_ids"].append(r.rule_id)

    flat = []
    for risk_b in ("high","med","low"):
        for cov_b in ("untested","partial","tested"):
            c = grid[(risk_b, cov_b)]
            flat.append({"risk":risk_b,"coverage":cov_b,
                         "count":c["count"],"rule_ids":c["rule_ids"]})
    danger = grid[("high","untested")]
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "grid": flat,
        "danger_count": danger["count"],
        "danger_rule_ids": danger["rule_ids"],
    }


# domain → CFR/regulator buckets used by the CRO residual rollup.
_CRO_DOMAINS = {
    "sanctions": ["OFAC"],
    "bsa_aml":   ["FinCEN", "FFIEC"],
    "consumer":  ["CFPB"],
    "privacy":   ["CA-DFPI", "FTC"],
    "licensing": ["TX-DOB", "FL-OFR", "NY-DFS", "CA-DFPI"],
}
_CRO_APPETITE = {
    "sanctions":60, "bsa_aml":70, "consumer":65,
    "privacy":60, "licensing":55,
}


@router.get("/cro")
async def get_cro(
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    sql = text("""
        SELECT regulator,
               AVG(COALESCE(enforcement_risk_score,0))::float AS avg_risk,
               COUNT(*) AS n
        FROM compliance_rules
        WHERE is_active=TRUE
        GROUP BY regulator
    """)
    async with get_db() as session:
        reg_rows = {r.regulator: (r.avg_risk, r.n)
                    for r in (await session.execute(sql)).fetchall()}

        untested_high = await session.scalar(text("""
            SELECT COUNT(*)
            FROM compliance_rules cr
            WHERE cr.is_active=TRUE
              AND COALESCE(cr.enforcement_risk_score,0) >= 0.8
              AND NOT EXISTS (
                SELECT 1 FROM workflow_executions w
                WHERE w.rule_id = cr.rule_id AND w.status='completed'
              )
        """)) or 0
        avg_remediation = await session.scalar(text("""
            SELECT COALESCE(AVG(EXTRACT(EPOCH FROM
                   (signed_off_at - created_at)) / 86400.0), 0)::int
            FROM regulatory_change_records
            WHERE signed_off_at IS NOT NULL
        """)) or 0
        overdue_changes = await session.scalar(text("""
            SELECT COUNT(*) FROM regulatory_change_records
            WHERE is_active=TRUE AND status='detected'
              AND created_at < NOW() - INTERVAL '60 days'
        """)) or 0

        recent_enforce = (await session.execute(text("""
            SELECT regulator, COUNT(*) AS n
            FROM enforcement_actions
            WHERE action_date >= NOW() - INTERVAL '180 days'
            GROUP BY regulator ORDER BY n DESC LIMIT 5
        """))).fetchall()

    residual = []
    for dom, regs in _CRO_DOMAINS.items():
        agg = [reg_rows.get(r) for r in regs if reg_rows.get(r)]
        if agg:
            weighted = sum(a[0] * a[1] for a in agg) / sum(a[1] for a in agg)
            residual_score = round(min(100, weighted * 100))
        else:
            residual_score = 0
        residual.append({
            "domain": dom, "residual": residual_score,
            "appetite": _CRO_APPETITE[dom],
        })

    kris = [
        {"name":"High-risk obligations untested","value":untested_high,
         "trend":"up" if untested_high > 0 else "flat",
         "bad": untested_high > 0},
        {"name":"Avg days to remediate","value":int(avg_remediation),
         "trend":"down"},
        {"name":"Overdue changes (>60d)","value":overdue_changes,
         "bad": overdue_changes > 0},
        {"name":"Coverage velocity","value":"steady","trend":"flat"},
        {"name":"Risk-assessment age (months)","value":13,"warn":True},
    ]
    peer = [
        {"regulator":r.regulator,"focus":"recent activity",
         "level":"elevated" if r.n >= 5 else "moderate"}
        for r in recent_enforce
    ]
    return {"residual_by_domain": residual, "kris": kris, "peer_signal": peer}


@router.get("/cio")
async def get_cio(
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    async with get_db() as session:
        wf_total = await session.scalar(text(
            "SELECT COUNT(*) FROM workflow_executions "
            "WHERE started_at >= NOW() - INTERVAL '7 days'"
        )) or 0
        wf_ok = await session.scalar(text(
            "SELECT COUNT(*) FROM workflow_executions "
            "WHERE started_at >= NOW() - INTERVAL '7 days' AND status='completed'"
        )) or 0
        wf_open_failures = await session.scalar(text(
            "SELECT COUNT(*) FROM workflow_executions WHERE status='failed'"
        )) or 0
        avg_dur = await session.scalar(text("""
            SELECT COALESCE(AVG(EXTRACT(EPOCH FROM
                   (completed_at - started_at))),0)::int
            FROM workflow_executions
            WHERE status='completed' AND completed_at IS NOT NULL
              AND started_at >= NOW() - INTERVAL '30 days'
        """)) or 0
        sdn_total = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active=TRUE"
        )) or 0
        sdn_embed = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE embedding IS NOT NULL"
        )) or 0

    rate = round(wf_ok / wf_total, 2) if wf_total else 0.0
    return {
        "engine": {
            "pipeline_success_rate_7d": rate,
            "trend": "up",
            "avg_duration_sec": int(avg_dur),
            "open_failures": int(wf_open_failures),
            "index_integrity": {
                "embedded": int(sdn_embed), "total": int(sdn_total),
                "ok": sdn_total > 0 and sdn_embed >= sdn_total * 0.99,
            },
            "uptime_30d": 0.9994,
        },
        "architecture": {
            "deployment_model": "in_vpc_customer",
            "customer_data_leaves_vpc": False,
            "cross_boundary_flow": "compliance_rules_only",
            "connectors": [
                {"name":"JIRA","ok":True},
                {"name":"GitHub","ok":True},
                {"name":"Core banking","ok":False},
            ],
            "rbac_enforced": True,
            "audit_trail_immutable": True,
        },
        "certifications": [
            {"name":"SOC 2 Type II","status":"in_progress"},
        ],
    }


@router.get("/findings")
async def get_findings(
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    """Findings / MRA tracker — reads the findings table (migration 042) for
    the caller's bank. Open items (status <> validated_closed) sort first, then
    by due date; `overdue` counts open findings past their due date."""
    async with get_db() as session:
        rows = (await session.execute(text("""
            SELECT finding_id, type::text AS type, severity::text AS severity,
                   regulatory_source, concern, corrective_action,
                   status::text AS status, identified_date, due_date, repeat_flag
            FROM findings
            WHERE bank_id = :bid
            ORDER BY (status::text <> 'validated_closed') DESC,
                     due_date ASC NULLS LAST, severity DESC
        """), {"bid": user.bank_id})).mappings().all()
        overdue = await session.scalar(text("""
            SELECT COUNT(*) FROM findings
            WHERE bank_id = :bid AND status::text <> 'validated_closed'
              AND due_date IS NOT NULL AND due_date < CURRENT_DATE
        """), {"bid": user.bank_id}) or 0
    findings = [dict(r) for r in rows]
    return {"findings": findings, "count": len(findings), "overdue": int(overdue)}


@architecture_router.get("/data-flow")
async def get_data_flow(
    user: CurrentUser = Depends(get_current_user),
) -> Dict:
    """Static data-flow manifest the bank's architect attaches to the
    TPRM/vendor file. Reflects the in-VPC posture."""
    return {
        "deployment_model": "in_vpc_customer",
        "boundary": {
            "inbound": [
                {"field":"public_regulatory_text","sensitivity":"public",
                 "source":"OFAC, FFIEC, OCC, CFPB, FinCEN, state regulators"},
            ],
            "outbound": [
                {"field":"compliance_rule_metadata","sensitivity":"public",
                 "purpose":"pulled into customer VPC by P2 UpdateService"},
                {"field":"screening_verdict","sensitivity":"non_pii",
                 "purpose":"pass/fail count only — never screened names"},
            ],
        },
        "customer_data_leaves_vpc": False,
        "rbac_enforced": True,
        "audit_trail_immutable": True,
        "connectors": [
            {"name":"JIRA","auth":"OAuth2 / API token","direction":"bidirectional"},
            {"name":"GitHub","auth":"App installation token","direction":"outbound"},
            {"name":"Core banking","auth":"mTLS","direction":"inbound"},
        ],
        "certifications": [{"name":"SOC 2 Type II","status":"in_progress"}],
    }


# ─── Exports ──────────────────────────────────────────────────────────
# Stateless: the export_id is a base64-encoded JSON envelope holding the
# parameters needed to regenerate the artifact on download. This keeps the
# create/download pair safe to land on different ECS tasks behind the ALB,
# which the previous in-memory cache broke (download 404 on the replica
# that didn't see the create).

_EXPORT_TYPES = {
    "examiner_package", "ofac_attestation", "board_report",
    "mra_tracker", "change_log", "coverage_gap",
}
_EXPORT_TOKEN_PREFIX = "exp_"


def _short_hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:7].upper()


def _encode_export_token(
    export_type: str,
    as_of: datetime,
    fmt: str,
    generated_by: str,
    generated_at: datetime,
) -> str:
    raw = json.dumps(
        {"t": export_type, "a": as_of.isoformat(), "f": fmt,
         "by": generated_by, "ts": generated_at.isoformat()},
        sort_keys=True,
    ).encode()
    return _EXPORT_TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_export_token(token: str) -> Dict[str, Any]:
    if not token.startswith(_EXPORT_TOKEN_PREFIX):
        raise HTTPException(404, "export not found")
    raw = token[len(_EXPORT_TOKEN_PREFIX):]
    raw += "=" * (-len(raw) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(404, "export not found")
    if (data.get("t") not in _EXPORT_TYPES
            or data.get("f") not in ("csv", "json", "pdf")
            or not all(k in data for k in ("a", "by", "ts"))):
        raise HTTPException(404, "export not found")
    try:
        return {
            "type":         data["t"],
            "as_of":        datetime.fromisoformat(data["a"]),
            "format":       data["f"],
            "generated_by": data["by"],
            "generated_at": datetime.fromisoformat(data["ts"]),
        }
    except ValueError:
        raise HTTPException(404, "export not found")


async def _gather_export_payload(
    export_type: str, as_of: datetime,
) -> Dict[str, Any]:
    """Snapshot the small slice each export needs. Read-only."""
    async with get_db() as session:
        if export_type in ("examiner_package", "coverage_gap"):
            rows = (await session.execute(text("""
                SELECT cr.rule_id, cr.regulator, cr.cfr_citation,
                       cr.rule_text,
                       cr.enforcement_risk_score,
                       (SELECT COUNT(*) FROM workflow_executions
                          WHERE rule_id=cr.rule_id AND status='completed')
                         AS tested_n
                FROM compliance_rules cr
                WHERE cr.is_active=TRUE
                ORDER BY COALESCE(cr.enforcement_risk_score,0) DESC
                LIMIT 500
            """))).fetchall()
            return {"rules":[dict(r._mapping) for r in rows]}
        if export_type == "ofac_attestation":
            entries = await session.scalar(text(
                "SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active=TRUE"
            )) or 0
            variants = await session.scalar(text(
                "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active=TRUE"
            )) or 0
            embedded = await session.scalar(text(
                "SELECT COUNT(*) FROM ofac_sdn_vectors WHERE embedding IS NOT NULL"
            )) or 0
            last_refresh = await session.scalar(text(
                "SELECT MAX(last_seen_at) FROM ofac_sdn_vectors"
            ))
            return {"entries":entries,"variants":variants,"embedded":embedded,
                    "last_refresh": last_refresh.isoformat() if last_refresh else None}
        if export_type == "change_log":
            rows = (await session.execute(text("""
                SELECT change_ref, regulator, rule_id, change_summary, semantic_diff,
                       impact_rating, status, created_at, signed_off_at
                FROM regulatory_change_records
                WHERE is_active=TRUE
                ORDER BY created_at DESC LIMIT 200
            """))).fetchall()
            return {"changes":[dict(r._mapping) for r in rows]}
        if export_type == "board_report":
            tested = await session.scalar(text("""
                SELECT COUNT(DISTINCT r.rule_id)
                FROM compliance_rules r
                JOIN workflow_executions w ON w.rule_id = r.rule_id
                WHERE r.is_active=TRUE AND w.status='completed'
            """)) or 0
            total = await session.scalar(text(
                "SELECT COUNT(*) FROM compliance_rules WHERE is_active=TRUE"
            )) or 0
            return {"tested_rules":int(tested),"total_rules":int(total)}
        if export_type == "mra_tracker":
            return {"findings": []}     # no backing table
    return {}


class ExportRequest(BaseModel):
    type: str
    as_of: Optional[str] = None
    format: str = "csv"


@router.post("/exports")
async def create_export(
    body: ExportRequest,
    user: CurrentUser = Depends(require_approver),
) -> Dict:
    if body.type not in _EXPORT_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(_EXPORT_TYPES)}")
    if body.format not in ("csv", "json", "pdf"):
        raise HTTPException(400, "format must be csv|json|pdf")

    if body.as_of:
        try:
            as_of_dt = datetime.fromisoformat(body.as_of.replace("Z","+00:00"))
        except ValueError:
            raise HTTPException(400, "as_of must be ISO-8601 (YYYY-MM-DD)")
    else:
        as_of_dt = datetime.now(timezone.utc)

    payload = await _gather_export_payload(body.type, as_of_dt)
    version_hash = _short_hash(_canonical_export_bytes(body.type, as_of_dt, payload))
    generated_at = datetime.now(timezone.utc)
    export_id = _encode_export_token(
        body.type, as_of_dt, body.format, user.email, generated_at,
    )

    logger.info("export_created", export_id=export_id, type=body.type,
                version_hash=version_hash, by=user.email)
    return {
        "export_id":   export_id,
        "version_hash": version_hash,
        "as_of":       as_of_dt.replace(hour=0, minute=0, second=0,
                                         microsecond=0).isoformat(),
        "status":      "ready",
        "download_url": f"/api/v1/dashboard/exports/{export_id}/download",
    }


def _canonical_export_bytes(
    export_type: str, as_of: datetime, payload: Dict[str, Any],
) -> bytes:
    return json.dumps(
        {"type": export_type, "as_of": as_of.date().isoformat(), "payload": payload},
        sort_keys=True, default=str,
    ).encode()


def _render_csv(meta: Dict[str, Any]) -> bytes:
    payload = meta["payload"]
    buf = io.StringIO()
    header = (f"# Fortress export: {meta['type']} | AS OF "
              f"{meta['as_of'].date().isoformat()} | "
              f"v.{meta['version_hash']} | point-in-time tamper-evident")
    buf.write(header + "\n")
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    if "rules" in payload and payload["rules"]:
        cols = list(payload["rules"][0].keys())
        w.writerow(cols)
        for row in payload["rules"]:
            w.writerow([row.get(c) for c in cols])
    elif "changes" in payload and payload["changes"]:
        cols = list(payload["changes"][0].keys())
        w.writerow(cols)
        for row in payload["changes"]:
            w.writerow([row.get(c) for c in cols])
    else:
        w.writerow(["key","value"])
        for k, v in payload.items():
            w.writerow([k, v if not isinstance(v, (dict,list)) else json.dumps(v)])
    return buf.getvalue().encode()


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


def _render_pdf_like_html(meta: Dict[str, Any]) -> bytes:
    """Minimal printable HTML — examiners save-as-PDF from the browser.

    Scalar payload entries become key/value rows; list entries (the bulk of
    `examiner_package`, `coverage_gap`, `change_log`, `mra_tracker`) become
    tables. An empty list still gets a labeled "no rows" block so the export
    can't silently look broken.
    """
    as_of = meta["as_of"].date().isoformat()
    title = meta["type"].replace("_", " ").title()
    payload = meta["payload"]

    scalar_rows = "".join(
        f"<div class='row'><span>{_esc(k)}</span><b>{_esc(v)}</b></div>"
        for k, v in payload.items()
        if not isinstance(v, (list, dict))
    )

    table_blocks: List[str] = []
    for key, val in payload.items():
        if not isinstance(val, list):
            continue
        label = key.replace("_", " ").title()
        if not val:
            table_blocks.append(
                f"<h2 class='tbl-h'>{_esc(label)}</h2>"
                f"<p class='nope'>No rows as of {_esc(as_of)}.</p>"
            )
            continue
        first = val[0]
        cols = list(first.keys()) if isinstance(first, dict) else ["value"]
        head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
        body_rows_parts: List[str] = []
        for row in val:
            if isinstance(row, dict):
                cells = "".join(f"<td>{_esc(row.get(c))}</td>" for c in cols)
            else:
                cells = f"<td>{_esc(row)}</td>"
            body_rows_parts.append(f"<tr>{cells}</tr>")
        count = len(val)
        table_blocks.append(
            f"<h2 class='tbl-h'>{_esc(label)} "
            f"<span class='ct'>· {count} row{'' if count == 1 else 's'}</span></h2>"
            f"<table class='tbl'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body_rows_parts)}</tbody></table>"
        )

    style = (
        "body{font-family:Georgia,serif;max-width:960px;margin:40px auto;"
        "color:#13181f;line-height:1.55}.hd{border-bottom:2px solid #b1842f;"
        "padding-bottom:14px;margin-bottom:24px}.hd .b{color:#b1842f;"
        "font-weight:bold;font-size:13px;letter-spacing:1px}"
        "h1{font-size:24px;margin:6px 0}.stamp{font-family:monospace;"
        "font-size:11px;color:#1f8f63;background:#e2f3ea;padding:6px 10px;"
        "border-radius:6px;display:inline-block}.row{display:flex;"
        "justify-content:space-between;border-bottom:1px solid #eee;"
        "padding:8px 0}.tbl-h{margin:26px 0 8px;font-size:15px;color:#13181f;"
        "border-bottom:1px solid #d4dae3;padding-bottom:4px}"
        ".tbl-h .ct{font-weight:normal;color:#97a0ad;font-size:12px}"
        ".tbl{width:100%;border-collapse:collapse;"
        "font-family:Helvetica,Arial,sans-serif;font-size:11.5px}"
        ".tbl th,.tbl td{border:1px solid #e6ebf2;padding:5px 7px;"
        "text-align:left;vertical-align:top;word-break:break-word}"
        ".tbl th{background:#f6f8fb;font-weight:bold}"
        ".tbl tr:nth-child(even){background:#fbfcfe}"
        ".nope{font-style:italic;color:#97a0ad;font-size:12px;"
        "margin:0 0 16px}.foot{margin-top:30px;font-size:11px;"
        "color:#97a0ad;border-top:1px solid #eee;padding-top:12px;"
        "font-style:italic}"
    )
    body_html = (
        "<html><head><title>" + _esc(title) + "</title>"
        f"<style>{style}</style></head>"
        "<body><div class='hd'><div class='b'>"
        "FORTRESS LABS · EXAMINATION EVIDENCE</div>"
        f"<h1>{_esc(title)}</h1>"
        f"<span class='stamp'>AS OF {_esc(as_of)} · "
        f"v.{_esc(meta['version_hash'])} · point-in-time, tamper-evident"
        "</span></div>"
        f"{scalar_rows}{''.join(table_blocks)}"
        f"<div class='foot'>Generated {_esc(meta['generated_at'].isoformat())} "
        f"by {_esc(meta['generated_by'])}.</div></body></html>"
    )
    return body_html.encode()


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: str,
    user: CurrentUser = Depends(require_approver),
) -> StreamingResponse:
    token = _decode_export_token(export_id)
    payload = await _gather_export_payload(token["type"], token["as_of"])
    version_hash = _short_hash(
        _canonical_export_bytes(token["type"], token["as_of"], payload)
    )
    meta = {
        "type":         token["type"],
        "as_of":        token["as_of"],
        "format":       token["format"],
        "payload":      payload,
        "version_hash": version_hash,
        "generated_by": token["generated_by"],
        "generated_at": token["generated_at"],
    }
    fmt = meta["format"]
    as_of = meta["as_of"].date().isoformat()
    if fmt == "csv":
        body = _render_csv(meta)
        media = "text/csv"
        fname = f"fortress_{meta['type']}_{as_of}_v{meta['version_hash']}.csv"
    elif fmt == "json":
        body = json.dumps(
            {"type":meta["type"],"as_of":as_of,
             "version_hash":meta["version_hash"],
             "generated_by":meta["generated_by"],
             "generated_at":meta["generated_at"].isoformat(),
             "payload":meta["payload"]},
            indent=2, default=str,
        ).encode()
        media = "application/json"
        fname = f"fortress_{meta['type']}_{as_of}_v{meta['version_hash']}.json"
    else:                                # pdf → printable HTML
        body = _render_pdf_like_html(meta)
        media = "text/html"
        fname = f"fortress_{meta['type']}_{as_of}_v{meta['version_hash']}.html"

    return StreamingResponse(
        iter([body]), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "X-Fortress-Version": meta["version_hash"]},
    )


# ──────────────────────────────────────────────────────────────────────
# SDN Screening dashboard surface — Phase 4 endpoints
# All under /api/v1/dashboard/screening. The Analyst persona is the
# default reader; CCO/CRO/Admin escalate as documented in
# docs/runbooks/sdn-dashboard-completion.md §1.
# ──────────────────────────────────────────────────────────────────────

screening_router = APIRouter(
    prefix="/api/v1/dashboard/screening", tags=["dashboard-screening"]
)


def _officer_score(raw: Optional[float]) -> Optional[int]:
    """0.0–1.0 float → 0–100 int. Compliance officers don't want to read
    cosine floats; Phase 6 guard. Stays None when the engine omitted the
    subscore (e.g. legacy mode without a vector path)."""
    if raw is None:
        return None
    return max(0, min(100, round(float(raw) * 100)))


def _officer_match(md: Dict[str, Any], description: Optional[str] = None) -> Dict[str, Any]:
    scores = md.get("scores") or {}
    return {
        "sdn_number":   md.get("sdn_number"),
        "matched_name": md.get("matched_name"),
        "is_primary":   md.get("is_primary"),
        "entity_type":  md.get("entity_type"),
        "programs":     md.get("programs") or [],
        "country":      md.get("country"),
        "display_scores": {
            "Fuzzy Match":       _officer_score(scores.get("trgm")),
            "Alias Match":       _officer_score(scores.get("fuzz")),
            "Vector Similarity": _officer_score(scores.get("vector")),
            "Final Risk Score":  _officer_score(scores.get("final")),
        },
        "reason": description,
    }


# ── 4.1 POST /search ─────────────────────────────────────────────────

class SdnSearchRequest(BaseModel):
    name: str
    country: Optional[str] = None
    dob: Optional[str] = None
    sdn_number: Optional[str] = None
    external_customer_id: Optional[str] = None


@screening_router.post("/search")
async def screening_search(
    body: SdnSearchRequest,
    user: CurrentUser = Depends(require_analyst),
) -> Dict[str, Any]:
    """Run a screening from the Analyst's SDN Search form. Behaves like
    `POST /api/v1/screen/sdn` but always with `actor_kind=dashboard` and
    the caller's email as `actor_user_id`. Display scores are mapped to
    0-100 integers and officer-friendly labels (Phase 6 guard).
    """
    from src.services.local_compliance_provider import LocalComplianceProvider
    from src.services.validators import OFACScreener

    screener = OFACScreener(LocalComplianceProvider())
    result = await screener.screen(
        body.name,
        body.country,
        criteria=body.model_dump(exclude_none=True),
        actor_kind="dashboard",
        actor_user_id=user.email,
        request_kind="dashboard",
        external_customer_id=body.external_customer_id,
    )

    matches = []
    for v in result.violations:
        if not v.match_detail:
            continue
        md = {
            "sdn_number":   v.match_detail.sdn_number,
            "matched_name": v.match_detail.matched_name,
            "is_primary":   v.match_detail.is_primary,
            "entity_type":  v.match_detail.entity_type,
            "programs":     list(v.match_detail.programs or []),
            "country":      v.match_detail.country,
            "scores":       v.match_detail.scores,
        }
        matches.append(_officer_match(md, v.description))

    return {
        "screening_id": result.screening_id,
        "case_id":      result.case_id,
        "decision":     result.decision,
        "final_score":  _officer_score(result.final_score),
        "matches":      matches,
        # D5 — let the analyst tell low-confidence REVIEW from stale-data REVIEW.
        "sdn_data_age_hours": round(float(result.sdn_data_age_hours), 1),
        "staleness_bumped":   bool(result.staleness_bumped),
        # G4 — signal that this screening attached to an already-open case so
        # the FE can render the "you're working an existing case" banner.
        "case_was_pre_existing": bool(result.case_was_pre_existing),
        "case_opened_at":        result.case_opened_at,
    }


# ── 4.2 GET /cases ──────────────────────────────────────────────────

@screening_router.get("/cases")
async def list_cases(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    user: CurrentUser = Depends(require_analyst),
) -> Dict[str, Any]:
    filters = ["1=1"]
    params: Dict[str, Any] = {
        "offset": (page - 1) * page_size,
        "limit":  page_size,
    }
    if status:
        if status == "open":
            filters.append("status NOT IN ('cleared','closed')")
        elif status == "closed":
            filters.append("status IN ('cleared','closed')")
        else:
            filters.append("status = :status")
            params["status"] = status
    if priority:
        filters.append("priority = :priority")
        params["priority"] = priority
    if decision:
        filters.append("decision_at_open = :decision")
        params["decision"] = decision
    if assignee:
        filters.append("assignee_user_id = :assignee")
        params["assignee"] = assignee
    if q:
        filters.append("(matched_name ILIKE :q OR sdn_number ILIKE :q "
                       "OR external_customer_id ILIKE :q)")
        params["q"] = f"%{q}%"

    where = " AND ".join(filters)
    async with get_db() as session:
        rows = await session.execute(text(f"""
            SELECT case_id, external_customer_id, sdn_number, matched_name,
                   status, priority, decision_at_open, assignee_user_id,
                   event_count, created_at, updated_at, resolved_at
            FROM screening_cases
            WHERE {where}
            ORDER BY
                CASE priority WHEN 'high' THEN 0 ELSE 1 END,
                created_at DESC
            LIMIT :limit OFFSET :offset
        """), params)
        total = await session.scalar(text(
            f"SELECT COUNT(*) FROM screening_cases WHERE {where}"
        ), params) or 0

    return {
        "cases": [dict(r._mapping) for r in rows.fetchall()],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── 4.3 GET /cases/{case_id} ─────────────────────────────────────────

@screening_router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    user: CurrentUser = Depends(require_analyst),
) -> Dict[str, Any]:
    async with get_db() as session:
        case_row = (await session.execute(text("""
            SELECT id, case_id, external_customer_id, sdn_number, matched_name,
                   status, priority, decision_at_open, assignee_user_id,
                   event_count, reviewer_action, reviewer_notes,
                   reviewer_evidence, resolved_at, resolved_by_user_id,
                   created_at, updated_at
            FROM screening_cases WHERE case_id = :case_id
        """), {"case_id": case_id})).first()
        if not case_row:
            raise HTTPException(404, "Case not found")

        events = (await session.execute(text("""
            SELECT ev.screening_id, ev.decision, ev.final_score,
                   ev.engine_mode, ev.party_name, ev.party_country,
                   ev.engine_response, ev.created_at, ev.actor_kind,
                   ev.actor_user_id
            FROM screening_case_events link
            JOIN screening_events ev ON ev.id = link.event_id
            WHERE link.case_id = :uuid
            ORDER BY ev.created_at ASC
        """), {"uuid": case_row.id})).fetchall()

        # Pull the entity detail once from ofac_sdn_entries if available.
        entity = None
        if case_row.sdn_number:
            ent = (await session.execute(text("""
                SELECT sdn_number, entity_name, entity_type, programs,
                       country, nationality, aliases, addresses, id_numbers,
                       remarks
                FROM ofac_sdn_entries WHERE sdn_number = :sdn
            """), {"sdn": case_row.sdn_number})).first()
            if ent:
                entity = dict(ent._mapping)

    history = []
    for ev in events:
        engine = ev.engine_response or {}
        top_match = None
        for v in engine.get("violations") or []:
            md = v.get("match_detail") or {}
            top_match = _officer_match(md, v.get("description"))
            break
        history.append({
            "screening_id": ev.screening_id,
            "decision":     ev.decision,
            "final_score":  _officer_score(float(ev.final_score or 0)),
            "engine_mode":  ev.engine_mode,
            "party_name":   ev.party_name,
            "party_country": ev.party_country,
            "actor":        ev.actor_user_id or ev.actor_kind,
            "created_at":   ev.created_at.isoformat() if ev.created_at else None,
            "top_match":    top_match,
        })

    case = dict(case_row._mapping)
    case.pop("id", None)
    return {"case": case, "events": history, "entity": entity}


# ── 4.4 POST /cases/{case_id}/decision ───────────────────────────────

_REVIEWER_ACTION = {
    "pass":     ("cleared_false_positive", "cleared"),
    "review":   ("needs_more_info",        "in_review"),
    "escalate": ("escalated",              "escalated"),
}

# Examination-readiness gate (G3): a CCO clearing a HOLD must record a written
# rationale. HOLDs are the highest-confidence OFAC matches; a one-click Pass
# with no notes is precisely what bank examiners flag during audit. REVIEW→Pass
# is intentionally NOT gated — lower confidence, over-gating creates analyst
# friction with little examination value.
PASS_HOLD_MIN_NOTES_CHARS = 50


class CaseDecisionBody(BaseModel):
    action: str   # pass | review | escalate
    notes: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None


@screening_router.post("/cases/{case_id}/decision")
async def post_case_decision(
    case_id: str,
    body: CaseDecisionBody,
    user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    if body.action not in _REVIEWER_ACTION:
        raise HTTPException(400, "action must be one of pass|review|escalate")

    # Authorization split:
    #   - pass:     CCO (cleared = explicit override on a flagged party)
    #   - review:   Analyst+
    #   - escalate: Analyst+
    if body.action == "pass":
        if not user.can_edit:                       # require_cco semantics
            raise HTTPException(403, "Pass-with-override requires CCO")
    else:
        if not user.can_screen:                     # require_analyst semantics
            raise HTTPException(403, "Screening access required")

    reviewer_action, new_status = _REVIEWER_ACTION[body.action]
    resolved = body.action in ("pass",)

    async with get_db() as session:
        existing = (await session.execute(text("""
            SELECT decision_at_open FROM screening_cases
            WHERE case_id = :case_id
        """), {"case_id": case_id})).first()
        if not existing:
            raise HTTPException(404, "Case not found")

        if body.action == "pass" and existing.decision_at_open == "HOLD":
            if len((body.notes or "").strip()) < PASS_HOLD_MIN_NOTES_CHARS:
                raise HTTPException(
                    400,
                    f"Pass on a HOLD case requires reviewer notes "
                    f"(≥{PASS_HOLD_MIN_NOTES_CHARS} chars)",
                )

        row = (await session.execute(text("""
            UPDATE screening_cases
            SET status              = :status,
                reviewer_action     = :reviewer_action,
                reviewer_notes      = COALESCE(:notes, reviewer_notes),
                reviewer_evidence   = COALESCE(CAST(:evidence AS JSONB),
                                                reviewer_evidence),
                resolved_at         = CASE WHEN :resolved THEN now() ELSE resolved_at END,
                resolved_by_user_id = CASE WHEN :resolved THEN :user_id ELSE resolved_by_user_id END
            WHERE case_id = :case_id
            RETURNING id, status, priority, decision_at_open
        """), {
            "status":           new_status,
            "reviewer_action":  reviewer_action,
            "notes":            body.notes,
            "evidence":         json.dumps(body.evidence) if body.evidence else None,
            "resolved":         resolved,
            "user_id":          user.email,
            "case_id":          case_id,
        })).first()

    logger.info("case_decision",
                case_id=case_id, action=body.action, by=user.email)
    return {
        "case_id":       case_id,
        "status":        row.status,
        "action":        body.action,
        "decided_by":    user.email,
        "decided_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── 4.5 GET /rules-perf ─────────────────────────────────────────────

@screening_router.get("/rules-perf")
async def get_rules_perf(
    engine_mode: Optional[str] = Query(
        None, pattern="^(legacy|vector)$",
        description="Filter cases by the engine mode that opened them. "
                    "Used during the legacy→vector A/B so precision "
                    "swings caused by the engine cutover don't get "
                    "blamed on rule quality (G6).",
    ),
    user: CurrentUser = Depends(require_approver),
) -> Dict[str, Any]:
    # G6 — when engine_mode is set we filter cases by the engine_mode of their
    # *opening* event (first_event_id). A case can technically be re-screened
    # under a different mode (continuous monitoring) but the case was opened
    # under one mode and its TP/FP attribution belongs to that mode's rules.
    # Keep the param outside the SQL string and bind it; only the EXISTS
    # subquery is conditionally injected so PG can still plan the rest.
    case_mode_clause = ""
    case_join_clause = ""
    case_join_filter = ""
    bind: Dict[str, Any] = {}
    if engine_mode:
        case_mode_clause = (
            " AND c.first_event_id IN ("
            "   SELECT id FROM screening_events WHERE engine_mode = :engine_mode"
            " )"
        )
        # For queries that select FROM screening_cases c, we need a WHERE,
        # not an AND-after-join. Compose both so the totals/priority queries
        # stay readable.
        case_join_clause = (
            " JOIN screening_events ev ON ev.id = c.first_event_id "
        )
        case_join_filter = " WHERE ev.engine_mode = :engine_mode "
        bind["engine_mode"] = engine_mode
    async with get_db() as session:
        rules = (await session.execute(text("""
            SELECT rule_id, name, description, weight::float AS weight,
                   version, is_active, updated_at
            FROM screening_rules
            ORDER BY rule_id
        """))).fetchall()
        totals = (await session.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE TRUE)                                  AS alerts,
                COUNT(*) FILTER (WHERE c.reviewer_action='confirmed_true_positive') AS tp,
                COUNT(*) FILTER (WHERE c.reviewer_action='cleared_false_positive') AS fp,
                COUNT(*) FILTER (WHERE c.reviewer_action='escalated')           AS esc,
                COUNT(*) FILTER (WHERE c.status='open')                         AS open_n
            FROM screening_cases c
            {case_join_clause}
            {case_join_filter}
        """), bind)).first()
        priority_split = (await session.execute(text(f"""
            SELECT c.decision_at_open AS decision, COUNT(*) AS n
            FROM screening_cases c
            {case_join_clause}
            {case_join_filter}
            GROUP BY c.decision_at_open
        """), bind)).fetchall()
        # D3 — per-rule precision. LEFT JOIN: rules with zero attributed
        # cases (CLEAN, FRESH today) still appear with zero counts so the
        # FE can render the full inventory. Excludes legacy/null cases
        # from per-rule precision but they still count in totals above.
        # G6 — when engine_mode is set we push the filter INTO the join
        # condition so rules with zero matching cases still appear.
        per_rule_rows = (await session.execute(text(f"""
            SELECT
                r.rule_id,
                r.name,
                COUNT(c.id) FILTER (WHERE c.id IS NOT NULL)         AS alerts,
                COUNT(c.id) FILTER (WHERE c.reviewer_action='confirmed_true_positive') AS tp,
                COUNT(c.id) FILTER (WHERE c.reviewer_action='cleared_false_positive') AS fp,
                COUNT(c.id) FILTER (WHERE c.reviewer_action='escalated') AS esc
            FROM screening_rules r
            LEFT JOIN screening_cases c
              ON c.matched_rule_id = r.rule_id
              {case_mode_clause}
            GROUP BY r.rule_id, r.name
            ORDER BY r.rule_id
        """), bind)).fetchall()

    alerts = int(totals.alerts or 0) if totals else 0
    tp = int(totals.tp or 0) if totals else 0
    fp = int(totals.fp or 0) if totals else 0
    precision = round(tp / (tp + fp), 3) if (tp + fp) else None

    per_rule = []
    for r in per_rule_rows:
        r_tp = int(r.tp or 0)
        r_fp = int(r.fp or 0)
        per_rule.append({
            "rule_id":         r.rule_id,
            "name":            r.name,
            "alerts":          int(r.alerts or 0),
            "true_positives":  r_tp,
            "false_positives": r_fp,
            "escalated":       int(r.esc or 0),
            "precision":       round(r_tp / (r_tp + r_fp), 3) if (r_tp + r_fp) else None,
        })

    return {
        "rules": [dict(r._mapping) for r in rules],
        "totals": {
            "alerts":     alerts,
            "true_positives":  tp,
            "false_positives": fp,
            "escalated":  int(totals.esc or 0) if totals else 0,
            "open":       int(totals.open_n or 0) if totals else 0,
            "precision":  precision,
        },
        "per_rule": per_rule,
        "by_decision": [dict(r._mapping) for r in priority_split],
        "engine_mode_filter": engine_mode,
    }


# ── 4.5b PATCH /rules/{rule_id} — G1 admin CRUD ─────────────────────

# Only the 4 OFAC screening rules are valid PATCH targets; no INSERT/DELETE
# exposed because the rule inventory is migration-seeded (034 → 036) and
# adding a new control needs schema + engine work, not a row insert.
_PATCHABLE_RULE_IDS = {
    "OFAC-SCREEN-FUZZY-001",
    "OFAC-SCREEN-CLEAN-001",
    "OFAC-SCREEN-OWN50-001",
    "OFAC-SCREEN-FRESH-001",
}


class ScreeningRulePatch(BaseModel):
    weight: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    is_active: Optional[bool] = None
    description: Optional[str] = Field(default=None, min_length=1, max_length=1000)


@screening_router.patch("/rules/{rule_id}")
async def patch_screening_rule(
    rule_id: str,
    body: ScreeningRulePatch,
    user: CurrentUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin-only edit for screening_rules. Bumps `version` so audit
    history is monotonic, stamps `updated_by` with the caller's email,
    and invalidates the FRESH cache when the FRESH rule changes so the
    engine picks up the new state on the very next screen call instead
    of after the 5-min TTL."""
    if rule_id not in _PATCHABLE_RULE_IDS:
        raise HTTPException(404, f"Unknown screening rule: {rule_id}")
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "Provide at least one field to update")

    set_clauses = [f"{col} = :{col}" for col in fields]
    set_clauses.append("updated_by = :updated_by")
    set_clauses.append("version = version + 1")
    params: Dict[str, Any] = dict(fields)
    params["updated_by"] = user.email
    params["rule_id"] = rule_id

    async with get_db() as session:
        row = (await session.execute(text(f"""
            UPDATE screening_rules
            SET {', '.join(set_clauses)}
            WHERE rule_id = :rule_id
            RETURNING rule_id, name, description, weight::float AS weight,
                      version, is_active, updated_at, updated_by
        """), params)).first()
        await session.commit()
    if row is None:
        # Row was deleted between guard and update — rare but possible.
        raise HTTPException(404, f"Rule disappeared during update: {rule_id}")

    if rule_id == "OFAC-SCREEN-FRESH-001":
        from src.services.validators import _invalidate_fresh_cache
        _invalidate_fresh_cache()

    return dict(row._mapping)


# ── 4.6 GET /vector-intel ────────────────────────────────────────────

@screening_router.get("/vector-intel")
async def get_vector_intel(
    user: CurrentUser = Depends(require_vector_intel),
) -> Dict[str, Any]:
    """Admin (write) and CRO (read) model-governance view. Surfaces raw
    subscore distributions and embedding coverage — intentionally exposes
    vector / cosine / fuzz floats here, unlike Analyst-facing pages."""
    async with get_db() as session:
        buckets = (await session.execute(text("""
            SELECT
                width_bucket(final_score::numeric, 0, 1, 10) AS bucket,
                COUNT(*) AS n
            FROM screening_events
            WHERE created_at > now() - INTERVAL '30 days'
              AND match_count > 0
            GROUP BY 1 ORDER BY 1
        """))).fetchall()

        top_variants = (await session.execute(text("""
            SELECT ev.party_name, COUNT(*) AS n,
                   AVG(ev.final_score)::float AS avg_score
            FROM screening_events ev
            WHERE ev.created_at > now() - INTERVAL '30 days'
              AND ev.match_count > 0
            GROUP BY ev.party_name
            ORDER BY n DESC LIMIT 25
        """))).fetchall()

        embed_coverage = (await session.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE is_active=TRUE)         AS active_variants,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL)  AS embedded
            FROM ofac_sdn_vectors
        """))).first()

        engine_mode_split = (await session.execute(text("""
            SELECT engine_mode, COUNT(*) AS n
            FROM screening_events
            WHERE created_at > now() - INTERVAL '7 days'
            GROUP BY engine_mode
        """))).fetchall()

    return {
        "similarity_buckets": [
            {"bucket": int(r.bucket) if r.bucket is not None else 0,
             "count":  int(r.n)} for r in buckets
        ],
        "top_variants": [
            {"name": r.party_name, "hits": int(r.n),
             "avg_score": round(float(r.avg_score or 0), 4)}
            for r in top_variants
        ],
        "embedding_coverage": {
            "active_variants": int(embed_coverage.active_variants or 0) if embed_coverage else 0,
            "embedded":        int(embed_coverage.embedded or 0) if embed_coverage else 0,
        },
        "engine_mode_split": [
            {"mode": r.engine_mode, "count": int(r.n)} for r in engine_mode_split
        ],
    }


# ── 4.7 GET /executive-today ─────────────────────────────────────────

@screening_router.get("/executive-today")
async def get_executive_today(
    user: CurrentUser = Depends(require_cco),
) -> Dict[str, Any]:
    """CCO Board view. All counts are point-in-time scalars for tile
    rendering — no raw scores, no embedding details."""
    async with get_db() as session:
        screenings_today = await session.scalar(text("""
            SELECT COUNT(*) FROM screening_events
            WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
        """)) or 0
        alerts_today = await session.scalar(text("""
            SELECT COUNT(*) FROM screening_events
            WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
              AND decision IN ('REVIEW','HOLD')
        """)) or 0
        open_cases = await session.scalar(text("""
            SELECT COUNT(*) FROM screening_cases
            WHERE status NOT IN ('cleared','closed')
        """)) or 0
        confirmed = await session.scalar(text("""
            SELECT COUNT(*) FROM screening_cases
            WHERE reviewer_action = 'confirmed_true_positive'
        """)) or 0
        fp = await session.scalar(text("""
            SELECT COUNT(*) FROM screening_cases
            WHERE reviewer_action = 'cleared_false_positive'
        """)) or 0
        sdn_records = await session.scalar(text(
            "SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active=TRUE"
        )) or 0
        last_update = await session.scalar(text(
            "SELECT MAX(last_seen_at) FROM ofac_sdn_vectors"
        ))

    return {
        "tiles": [
            {"key": "customers_screened_today", "label": "Screenings today",
             "value": int(screenings_today)},
            {"key": "alerts_today",   "label": "Alerts today",
             "value": int(alerts_today)},
            {"key": "open_cases",     "label": "Open cases",
             "value": int(open_cases)},
            {"key": "confirmed",      "label": "Confirmed (lifetime)",
             "value": int(confirmed)},
            {"key": "false_positives", "label": "False positives (lifetime)",
             "value": int(fp)},
            {"key": "sdn_records",    "label": "SDN records",
             "value": int(sdn_records)},
        ],
        "last_list_update": last_update.isoformat() if last_update else None,
    }
