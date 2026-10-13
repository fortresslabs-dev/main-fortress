"""Internal sync trigger endpoint — manually pull rules from P1.

Runbook Step 9 calls POST /internal/sync/trigger to seed P2 Aurora on first
boot (or to force a re-sync after a P1 schema change). Gated by a shared
secret to avoid exposing a costly operation to the open internet.
"""
import hmac

from fastapi import APIRouter, HTTPException, Request
import structlog

from src.utils.config import settings

router = APIRouter()
logger = structlog.get_logger()


def _verify_internal_secret(header: str | None) -> bool:
    expected = settings.INTERNAL_SYNC_SECRET
    if not expected:
        logger.warning("internal_sync_secret_unset — verification skipped")
        return True
    if not header:
        return False
    return hmac.compare_digest(expected, header)


@router.get("/counts")
async def sync_counts(request: Request):
    """Return local compliance_rules counts + latest sync_version.

    Read-only diagnostic for reconciling against P1 totals.
    """
    if not _verify_internal_secret(request.headers.get("X-Internal-Secret")):
        raise HTTPException(401, "Invalid internal secret")

    from src.services.database import get_db
    from sqlalchemy import text as sqla_text

    async with get_db() as session:
        async def _c(sql: str) -> int:
            return (await session.execute(sqla_text(sql))).scalar() or 0

        total    = await _c("SELECT COUNT(*) FROM compliance_rules")
        active   = await _c("SELECT COUNT(*) FROM compliance_rules WHERE is_active = TRUE")
        embedded = await _c("SELECT COUNT(*) FROM compliance_rules WHERE embedding IS NOT NULL")
        sdn      = await _c("SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active = TRUE")
        oblig    = await _c("SELECT COUNT(*) FROM obligations WHERE is_active = TRUE")
        enf      = await _c("SELECT COUNT(*) FROM enforcement_actions WHERE is_active = TRUE")
        risk     = await _c("SELECT COUNT(*) FROM enforcement_risk_scores")
        deadlines = await _c("SELECT COUNT(*) FROM compliance_deadlines WHERE is_active = TRUE")
        changes  = await _c("SELECT COUNT(*) FROM regulatory_change_records WHERE is_active = TRUE")
        peers    = await _c("SELECT COUNT(*) FROM peer_benchmarks")
        version  = await _c("SELECT COALESCE(MAX(sync_version), 0) FROM local_sync_status")

    return {
        "compliance_rules_total":             total,
        "compliance_rules_active":            active,
        "compliance_rules_with_embedding":    embedded,
        "ofac_sdn_entries_active":            sdn,
        "obligations_active":                 oblig,
        "enforcement_actions_active":         enf,
        "enforcement_risk_scores":            risk,
        "compliance_deadlines_active":        deadlines,
        "regulatory_change_records_active":   changes,
        "peer_benchmarks":                    peers,
        "local_sync_version":                 version,
    }


@router.post("/trigger")
async def trigger_sync(request: Request, force_initial: bool = False):
    """Force an immediate sync from P1 → local Aurora.

    `force_initial=true` bypasses the version check and runs the full bootstrap
    even when local_sync_status reports a non-zero version — useful when the
    bookkeeping row survived a partial sync but rule data was rolled back.
    """
    if not _verify_internal_secret(request.headers.get("X-Internal-Secret")):
        raise HTTPException(401, "Invalid internal secret")

    update_service = getattr(request.app.state, "update_service", None)
    if update_service is None:
        raise HTTPException(503, "Update service not initialised")

    logger.info("internal_sync_triggered", force_initial=force_initial)
    if force_initial:
        result = await update_service._initial_sync()
    else:
        result = await update_service.sync_all()
    return {"status": "triggered", "result": result}
