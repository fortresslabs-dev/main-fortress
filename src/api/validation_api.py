"""
Validation API — Production validation endpoints for Pipeline 2.

All customer data processed here stays LOCAL inside customer infrastructure.
Compliance rules are read from the local PostgreSQL replica, kept in sync
by the UpdateService (daily incremental diffs from Fortress Cloud).

Endpoints:
  POST /validate/wire-transfer      → Wire transfer BSA/AML compliance
  POST /validate/ofac-screen        → OFAC sanctions screening
  POST /validate/loan-application   → CFPB Section 1071 validation
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.services.local_compliance_provider import LocalComplianceProvider
from src.services.validators import (
    LoanApplicationValidator,
    OFACScreener,
    WireTransferValidator,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/validate", tags=["Production Validation (Local)"])

# Local DB provider — reads from synced PostgreSQL replica (zero network calls)
_provider = LocalComplianceProvider()


# ── M8 — per-IP sliding-window rate limit on /validate/* ─────────────
#
# P2 runs one container per bank, so an in-process deque is enough. A
# bursty client at 5x the limit would otherwise exhaust the Redis
# connection pool the validators rely on; 60 req / 60 s per IP covers
# a teller-station load while killing scripted abuse.
#
# Override via env to allow load-test bursts:
#   FORTRESS_P2_VALIDATE_RPM=600
_VALIDATE_RPM = int(os.environ.get("FORTRESS_P2_VALIDATE_RPM", "60"))
_VALIDATE_WINDOW_S = 60.0
_validate_calls: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request) -> None:
    """Sliding-window rate limit keyed by client IP. Bypassed if disabled
    (FORTRESS_P2_VALIDATE_RPM=0) so unit tests can hammer."""
    if _VALIDATE_RPM <= 0:
        return
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    cutoff = now - _VALIDATE_WINDOW_S
    buf = _validate_calls[client]
    while buf and buf[0] < cutoff:
        buf.popleft()
    if len(buf) >= _VALIDATE_RPM:
        retry_after = max(1, int(_VALIDATE_WINDOW_S - (now - buf[0])))
        logger.warning(
            "validate_rate_limit_hit",
            client=client, rpm=_VALIDATE_RPM, retry_after_s=retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many validation requests",
            headers={"Retry-After": str(retry_after)},
        )
    buf.append(now)


# ── M14 — CloudWatch metrics from validate endpoints ────────────────
#
# Without this, P2 has zero prod visibility on wire/OFAC/loan latency
# or decision distribution. Emission is best-effort: a Bedrock or CW
# outage must not propagate into the validate response, which is the
# customer's serving path.

_CW_NAMESPACE = "Fortress/P2/Validation"
_CW_CLIENT = None


def _cw():
    global _CW_CLIENT
    if _CW_CLIENT is None:
        try:
            import boto3
            _CW_CLIENT = boto3.client(
                "cloudwatch",
                region_name=os.environ.get("AWS_REGION", "us-east-1"),
            )
        except Exception as exc:
            logger.warning("cloudwatch_init_failed", error=str(exc))
            _CW_CLIENT = False  # sentinel: tried and failed, skip future attempts
    return _CW_CLIENT or None


def _emit_validation_metric(
    endpoint: str, decision: str | None, latency_ms: float
) -> None:
    """Push (latency, decision counter) to CloudWatch. Never raises."""
    cw = _cw()
    if cw is None:
        return
    bank_id = os.environ.get("CLIENT_NAME", "unknown")
    dims = [
        {"Name": "Bank", "Value": bank_id},
        {"Name": "Endpoint", "Value": endpoint},
    ]
    try:
        cw.put_metric_data(
            Namespace=_CW_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "LatencyMs",
                    "Dimensions": dims,
                    "Value": float(latency_ms),
                    "Unit": "Milliseconds",
                },
                {
                    "MetricName": "Decisions",
                    "Dimensions": dims + [
                        {"Name": "Decision", "Value": decision or "UNKNOWN"},
                    ],
                    "Value": 1.0,
                    "Unit": "Count",
                },
                # M9 / dimensionless heartbeat — keeps "did /validate fire
                # at all" alarmable without crossing the per-dim metric.
                {
                    "MetricName": "Calls",
                    "Dimensions": [],
                    "Value": 1.0,
                    "Unit": "Count",
                },
            ],
        )
    except Exception as exc:
        logger.warning(
            "cloudwatch_emit_failed",
            endpoint=endpoint, error=str(exc),
        )


# ── Request / Response schemas ──────────────────────────────────────

class WireTransferRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Transfer amount")
    currency: str = Field(default="USD", max_length=3)
    sender_name: str = Field(..., min_length=1, max_length=200)
    beneficiary_name: str = Field(..., min_length=1, max_length=200)
    beneficiary_country: str = Field(default="US", max_length=2)
    is_international: bool = False
    approvals: List[Dict[str, str]] = Field(default_factory=list)


class OFACScreenRequest(BaseModel):
    party_name: str = Field(..., min_length=1, max_length=300)
    party_country: Optional[str] = Field(default=None, max_length=2)


class LoanApplicationRequest(BaseModel):
    race_of_principal_owner: Optional[str] = None
    sex_of_principal_owner: Optional[str] = None
    ethnicity_of_principal_owner: Optional[str] = None
    naics_code: Optional[str] = None
    business_gross_revenue: Optional[float] = None
    time_in_business: Optional[str] = None
    number_of_workers: Optional[int] = None
    minority_owned_business: Optional[bool] = None
    women_owned_business: Optional[bool] = None


class MatchDetailOut(BaseModel):
    sdn_number: Optional[str] = None
    matched_name: Optional[str] = None
    is_primary: Optional[bool] = None
    entity_type: Optional[str] = None
    programs: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    scores: Dict[str, float] = Field(default_factory=dict)


class ViolationOut(BaseModel):
    regulation: str
    rule_id: str
    description: str
    severity: str
    recommendation: str
    match_detail: Optional[MatchDetailOut] = None


class ValidationResponse(BaseModel):
    action: str
    decision: Optional[str] = None         # PASS | REVIEW | HOLD
    screening_id: Optional[str] = None
    final_score: float = 0.0
    violations: List[ViolationOut]
    screening_timestamp: str
    latency_ms: float


# ── Endpoints ───────────────────────────────────────────────────────

@router.post("/wire-transfer", response_model=ValidationResponse)
async def validate_wire_transfer(req: WireTransferRequest, request: Request):
    """
    Validate a wire transfer against OCC BSA/AML rules.
    Customer data stays LOCAL — only rules are fetched from Fortress API.
    """
    _rate_limit(request)
    t0 = time.monotonic()
    validator = WireTransferValidator(_provider)
    result = await validator.validate(req.model_dump())
    response = result.to_dict()
    _emit_validation_metric(
        "wire-transfer", response.get("decision"),
        (time.monotonic() - t0) * 1000.0,
    )
    return response


@router.post("/ofac-screen", response_model=ValidationResponse)
async def ofac_screen(req: OFACScreenRequest, request: Request):
    """
    Screen a party against the OFAC SDN sanctions list.
    Party name stays LOCAL — only the SDN list (public data) is fetched.
    """
    _rate_limit(request)
    t0 = time.monotonic()
    screener = OFACScreener(_provider)
    result = await screener.screen(
        req.party_name,
        req.party_country,
        actor_kind="api",
        request_kind="customer",
    )
    response = result.to_dict()
    _emit_validation_metric(
        "ofac-screen", response.get("decision"),
        (time.monotonic() - t0) * 1000.0,
    )
    return response


@router.post("/loan-application", response_model=ValidationResponse)
async def validate_loan_application(req: LoanApplicationRequest, request: Request):
    """
    Validate a small-business loan application for CFPB Section 1071 compliance.
    All applicant demographic data stays LOCAL.
    """
    _rate_limit(request)
    t0 = time.monotonic()
    validator = LoanApplicationValidator(_provider)
    result = await validator.validate(req.model_dump())
    response = result.to_dict()
    _emit_validation_metric(
        "loan-application", response.get("decision"),
        (time.monotonic() - t0) * 1000.0,
    )
    return response
