"""
Compliance Rules API — Read-Only endpoints for Pipeline 1.

These endpoints serve public regulatory data to customer-deployed agents.
NO customer data is accepted or returned — only compliance rules.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Compliance Rules (Read-Only)"])


# ── Response schemas ────────────────────────────────────────────────

class ComplianceRuleOut(BaseModel):
    rule_id: str
    regulator: str
    category: str
    geography: str
    rule_text: str
    rule_type: Optional[str] = None
    threshold_amount: Optional[float] = None
    threshold_currency: Optional[str] = None
    effective_date: Optional[str] = None
    source_url: Optional[str] = None
    test_template: Optional[str] = None
    # §11.3 hard-gate fields (migrations 046/047). Consumers use these
    # to route rules into blocking (LAW+verified) vs advisory rails. See
    # src/services/authority_gate.py — the gate reads these two fields
    # and nothing else.
    authority_type: Optional[str] = None
    citation_verified: Optional[bool] = None
    citation_verified_source: Optional[str] = None
    cfr_citation: Optional[str] = None
    authority_source: Optional[str] = None

    class Config:
        from_attributes = True


class RulesResponse(BaseModel):
    count: int
    rules: List[ComplianceRuleOut]
    last_updated: Optional[str] = None


class OFACEntryOut(BaseModel):
    sdn_number: str
    entity_name: str
    entity_type: Optional[str] = None
    programs: Optional[List[str]] = None
    remarks: Optional[str] = None

    class Config:
        from_attributes = True


class OFACListResponse(BaseModel):
    count: int
    entries: List[OFACEntryOut]
    last_updated: Optional[str] = None


class RuleChangeOut(BaseModel):
    rule_id: str
    change_type: str  # added | modified | removed
    changed_at: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/rules", response_model=RulesResponse)
async def get_rules(
    geography: str = Query("usa", description="Geography filter (usa, india)"),
    regulator: Optional[str] = Query(None, description="Regulator filter (occ, fdic, fincen, cfpb, frb, ofac)"),
    category: Optional[str] = Query(None, description="Category filter (bsa_aml, fair_lending, stress_testing, sanctions)"),
    domain: Optional[str] = Query(None, description="Domain filter (wire_transfer, sar, ctr, ofac, section_1071)"),
):
    """
    Return compliance rules (public regulatory data only).

    No customer data is required or accepted in the request.
    """
    # Build query against compliance_requirements table
    conditions = ["1=1"]
    params: dict = {}

    if geography:
        conditions.append("geography = :geography")
        params["geography"] = geography

    if regulator:
        conditions.append("regulator = :regulator")
        params["regulator"] = regulator.upper()

    if category:
        conditions.append("category = :category")
        params["category"] = category

    if domain:
        conditions.append("domain = :domain")
        params["domain"] = domain

    where_clause = " AND ".join(conditions)
    query = text(
        f"SELECT * FROM compliance_rules WHERE {where_clause} ORDER BY rule_id"
    )

    async with get_db() as session:
        result = await session.execute(query, params)
        rows = result.mappings().all()

    rules = [
        ComplianceRuleOut(
            rule_id=r.get("rule_id", ""),
            regulator=r.get("regulator", ""),
            category=r.get("category", ""),
            geography=r.get("geography", "usa"),
            rule_text=r.get("rule_text", ""),
            rule_type=r.get("rule_type"),
            threshold_amount=r.get("threshold_amount"),
            threshold_currency=r.get("threshold_currency"),
            effective_date=str(r.get("effective_date", "")),
            source_url=r.get("source_url"),
            test_template=r.get("test_template"),
            authority_type=r.get("authority_type"),
            citation_verified=r.get("citation_verified"),
            citation_verified_source=r.get("citation_verified_source"),
            cfr_citation=r.get("cfr_citation"),
            authority_source=r.get("authority_source"),
        )
        for r in rows
    ]

    return RulesResponse(count=len(rules), rules=rules)


@router.get("/ofac/sdn-list", response_model=OFACListResponse)
async def get_ofac_sdn_list(
    limit: int = Query(10000, ge=1, le=20000),
):
    """
    Return the public OFAC SDN list (8000+ entries).

    This is public data from treasury.gov — no customer data involved.
    """
    query = text("SELECT * FROM ofac_sdn_entries ORDER BY sdn_number LIMIT :limit")

    async with get_db() as session:
        result = await session.execute(query, {"limit": limit})
        rows = result.mappings().all()

    entries = [
        OFACEntryOut(
            sdn_number=r.get("sdn_number", ""),
            entity_name=r.get("entity_name", ""),
            entity_type=r.get("entity_type"),
            programs=r.get("programs"),
            remarks=r.get("remarks"),
        )
        for r in rows
    ]

    return OFACListResponse(count=len(entries), entries=entries)


@router.get("/rules/changes")
async def get_rule_changes(
    since: str = Query(..., description="ISO date (e.g. 2026-01-01)"),
    geography: str = Query("usa"),
):
    """
    Return rules that changed since a given date.

    Useful for customer agents to sync compliance rule updates.
    """
    query = text(
        "SELECT * FROM compliance_rule_changes "
        "WHERE changed_at >= :since AND geography = :geography "
        "ORDER BY changed_at DESC"
    )

    async with get_db() as session:
        result = await session.execute(query, {"since": since, "geography": geography})
        rows = result.mappings().all()

    changes = [
        RuleChangeOut(
            rule_id=r.get("rule_id", ""),
            change_type=r.get("change_type", ""),
            changed_at=str(r.get("changed_at", "")),
            old_value=r.get("old_value"),
            new_value=r.get("new_value"),
        )
        for r in rows
    ]

    return {"count": len(changes), "changes": changes, "since": since}
