"""
src/api/screening_api.py
Production OFAC/SDN screening API consumed by bank KYC + wire systems.

Endpoints:
  POST /api/v1/screen/sdn               Screen a party. Returns decision + matches.
  GET  /api/v1/screen/sdn/{screening_id} Replay a prior screening by its id.

Auth: dual — Cognito Bearer (require_analyst) OR HMAC header
(`X-Api-Key` + `X-Customer-Id`). Banks call from their backend with HMAC;
the dashboard calls with Cognito.

Idempotency: a POST with the same canonicalised criteria within 24h returns
the prior screening_event verbatim, with header `X-Idempotent: true`. Cache
auto-invalidates whenever the underlying SDN list is refreshed.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.api.auth import _DEV_TOKEN, _decode_cognito_token  # type: ignore[attr-defined]
from src.api.sync_auth import require_sync_auth
from src.services.database import get_db
from src.services.local_compliance_provider import LocalComplianceProvider
from src.services.validators import OFACScreener, _criteria_hash

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/screen", tags=["Screening"])

_provider = LocalComplianceProvider()


# ── Dual auth: Cognito Bearer OR HMAC header ─────────────────────────

async def require_screening_auth(
    x_api_key:     Annotated[Optional[str], Header()] = None,
    x_customer_id: Annotated[Optional[str], Header()] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict[str, Any]:
    """Accept either:
       - HMAC: X-Api-Key + X-Customer-Id (used by bank backends)
       - Cognito: Authorization: Bearer <jwt> from Analyst+ (used by the dashboard)

       Returns a uniform ctx: `{actor_kind, actor_user_id, customer_id}`."""
    if x_api_key and x_customer_id:
        # HMAC path. Reuses sync_auth so any change there propagates here.
        ctx = await require_sync_auth(x_api_key=x_api_key,
                                      x_customer_id=x_customer_id)
        return {
            "actor_kind":    "api",
            "actor_user_id": None,
            "customer_id":   ctx["customer_id"],
        }
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        if token == _DEV_TOKEN:
            return {"actor_kind": "dashboard",
                    "actor_user_id": "dev",
                    "customer_id": None}
        claims = _decode_cognito_token(token)
        groups = claims.get("cognito:groups", [])
        role = groups[0] if groups else "viewer"
        # Reuse the can_screen rule from CurrentUser.
        if role not in ("Analyst", "CCO", "CRO", "Admin", "admin"):
            raise HTTPException(403, "Screening access required")
        return {
            "actor_kind":    "dashboard",
            "actor_user_id": claims.get("email") or claims.get("sub"),
            "customer_id":   None,
        }
    raise HTTPException(401, "Unauthorized")


# ── Request / Response schemas ────────────────────────────────────────

class ScreenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300,
                      description="Party name to screen")
    country: Optional[str] = Field(default=None, max_length=2,
                                   description="ISO-3166-1 alpha-2")
    dob: Optional[str] = Field(default=None, max_length=10,
                               description="YYYY-MM-DD")
    address: Optional[str] = Field(default=None, max_length=500)
    sdn_number: Optional[str] = Field(default=None, max_length=50,
                                      description="Recheck a specific SDN id")
    external_customer_id: Optional[str] = Field(default=None, max_length=128,
                                                description="Bank's internal customer id")
    request_kind: str = Field(default="customer",
                              pattern="^(customer|transaction|bulk|monitoring|dashboard)$")
    request_group_id: Optional[str] = None
    party_role: Optional[str] = Field(default=None, max_length=32)


class MatchOut(BaseModel):
    sdn_number: Optional[str] = None
    matched_name: Optional[str] = None
    is_primary: Optional[bool] = None
    entity_type: Optional[str] = None
    programs: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    scores: Dict[str, float] = Field(default_factory=dict)
    reason: Optional[str] = None


class ScreenResponse(BaseModel):
    screening_id: str
    decision: str           # PASS | REVIEW | HOLD
    final_score: float
    matches: List[MatchOut]
    screened_at: str
    latency_ms: float


# ── POST /screen/sdn ─────────────────────────────────────────────────

@router.post("/sdn", response_model=ScreenResponse)
async def screen_sdn(
    body: ScreenRequest,
    response: Response,
    ctx: Annotated[Dict[str, Any], Depends(require_screening_auth)],
) -> Dict[str, Any]:
    criteria = body.model_dump(exclude_none=True)
    crit_hash = _criteria_hash({
        "name":    body.name,
        "country": body.country,
        "dob":     body.dob,
        "address": body.address,
    })

    # ── Idempotency: replay if same criteria within 24h AND no SDN refresh since.
    cached = await _try_idempotent(crit_hash)
    if cached is not None:
        response.headers["X-Idempotent"] = "true"
        return cached

    screener = OFACScreener(_provider)
    result = await screener.screen(
        body.name,
        body.country,
        criteria=criteria,
        actor_kind=ctx["actor_kind"],
        actor_user_id=ctx["actor_user_id"],
        request_kind=body.request_kind,
        request_group_id=body.request_group_id,
        external_customer_id=body.external_customer_id,
        party_role=body.party_role,
    )

    return _to_response(result)


def _to_response(result) -> Dict[str, Any]:
    matches: List[Dict[str, Any]] = []
    for v in result.violations:
        md = v.match_detail
        if not md:
            continue
        matches.append({
            "sdn_number":   md.sdn_number,
            "matched_name": md.matched_name,
            "is_primary":   md.is_primary,
            "entity_type":  md.entity_type,
            "programs":     list(md.programs or []),
            "country":      md.country,
            "scores":       {k: float(v) for k, v in (md.scores or {}).items()},
            "reason":       v.description,
        })
    return {
        "screening_id": result.screening_id or "",
        "decision":     result.decision or "PASS",
        "final_score":  float(result.final_score),
        "matches":      matches,
        "screened_at":  result.screening_timestamp,
        "latency_ms":   float(result.latency_ms),
    }


async def _try_idempotent(crit_hash: str) -> Optional[Dict[str, Any]]:
    """Return the most recent screening_events row in the last 24h for
    this criteria_hash, but only if the SDN data has not refreshed since.

    A refresh invalidates because new SDN entries may now match where
    none did before — silently returning stale verdicts there is unsafe.
    """
    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT screening_id, decision, final_score, engine_response,
                   created_at, latency_ms
            FROM screening_events
            WHERE criteria_hash = :h
              AND created_at > now() - INTERVAL '24 hours'
            ORDER BY created_at DESC LIMIT 1
        """), {"h": crit_hash})).first()
        if not row:
            return None
        last_sdn_refresh = await session.scalar(text(
            "SELECT MAX(last_seen_at) FROM ofac_sdn_vectors"
        ))
    if last_sdn_refresh and row.created_at and last_sdn_refresh > row.created_at:
        return None

    engine = row.engine_response or {}
    # `engine_response` already has the shape `_to_response` produces minus
    # the screening_id (which the engine doesn't know at to_dict() time).
    matches = []
    for v in engine.get("violations") or []:
        md = v.get("match_detail") or {}
        matches.append({
            "sdn_number":   md.get("sdn_number"),
            "matched_name": md.get("matched_name"),
            "is_primary":   md.get("is_primary"),
            "entity_type":  md.get("entity_type"),
            "programs":     md.get("programs") or [],
            "country":      md.get("country"),
            "scores":       {k: float(x) for k, x in (md.get("scores") or {}).items()},
            "reason":       v.get("description"),
        })
    return {
        "screening_id": row.screening_id,
        "decision":     row.decision,
        "final_score":  float(row.final_score or 0),
        "matches":      matches,
        "screened_at":  row.created_at.isoformat() if row.created_at else "",
        "latency_ms":   float(row.latency_ms or 0),
    }


# ── GET /screen/sdn/{screening_id} ───────────────────────────────────

@router.get("/sdn/{screening_id}", response_model=ScreenResponse)
async def get_screening(
    screening_id: str,
    ctx: Annotated[Dict[str, Any], Depends(require_screening_auth)],
) -> Dict[str, Any]:
    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT screening_id, decision, final_score, engine_response,
                   created_at, latency_ms
            FROM screening_events
            WHERE screening_id = :id
        """), {"id": screening_id})).first()
    if not row:
        raise HTTPException(404, "Screening not found")

    engine = row.engine_response or {}
    matches = []
    for v in engine.get("violations") or []:
        md = v.get("match_detail") or {}
        matches.append({
            "sdn_number":   md.get("sdn_number"),
            "matched_name": md.get("matched_name"),
            "is_primary":   md.get("is_primary"),
            "entity_type":  md.get("entity_type"),
            "programs":     md.get("programs") or [],
            "country":      md.get("country"),
            "scores":       {k: float(x) for k, x in (md.get("scores") or {}).items()},
            "reason":       v.get("description"),
        })
    return {
        "screening_id": row.screening_id,
        "decision":     row.decision,
        "final_score":  float(row.final_score or 0),
        "matches":      matches,
        "screened_at":  row.created_at.isoformat() if row.created_at else "",
        "latency_ms":   float(row.latency_ms or 0),
    }
