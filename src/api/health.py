"""Health Check Endpoints"""
from datetime import datetime, timezone
from typing import Dict

import boto3
import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import func, select, text

from src.models.database import ComplianceRule, LocalSyncStatus
from src.services.database import get_db
from src.services.redis_service import get_redis
from src.utils.config import settings

logger = structlog.get_logger(__name__)
router = APIRouter()

# Deps whose `down` status fails the liveness probe → HTTP 503.
# Redis/Bedrock/S3 are non-critical: cache + LLM + object store all have
# fallback paths in-process, so a transient outage there must not pull the
# whole task. DB has no fallback — if Postgres is gone, every endpoint 5xxs.
_CRITICAL_DEPS = {"db"}


async def _probe_db() -> str:
    try:
        async with get_db() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        logger.warning("health_probe_db_failed", error=str(exc))
        return "down"


async def _probe_redis() -> str:
    try:
        client = await get_redis()
    except Exception as exc:
        logger.warning("health_probe_redis_resolve_failed", error=str(exc))
        return "down"
    # init_redis() soft-fails when Redis is unreachable at startup —
    # client stays None, app boots, cache layer is disabled.
    if client is None:
        return "degraded"
    try:
        await client.ping()
        return "ok"
    except Exception as exc:
        logger.warning("health_probe_redis_ping_failed", error=str(exc))
        return "down"


def _probe_bedrock() -> str:
    """Lightweight Bedrock reachability check.

    Validates region + model are configured and a bedrock-runtime client
    constructs. Does NOT invoke the model — a deep probe burns IAM
    quota, costs money, and inflates p99 on /health. ECS liveness
    catches misconfigs (no region, no model, no boto3); Bedrock-side
    outages are owned by the invoke-time retry layer.
    """
    if not settings.AWS_REGION or not settings.BEDROCK_MODEL:
        return "degraded"
    try:
        boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
        return "ok"
    except Exception as exc:
        logger.warning("health_probe_bedrock_failed", error=str(exc))
        return "down"


def _probe_s3() -> str:
    """Lightweight S3 reachability check — constructor only.

    Mirrors `_probe_bedrock`: don't HeadBucket on every health hit.
    Catches missing region / boto3 runtime; runtime S3 errors are
    owned by their call sites.
    """
    if not settings.AWS_REGION:
        return "degraded"
    try:
        boto3.client("s3", region_name=settings.AWS_REGION)
        return "ok"
    except Exception as exc:
        logger.warning("health_probe_s3_failed", error=str(exc))
        return "down"


@router.get("/")
async def health_check(response: Response) -> Dict:
    """Liveness probe with per-dep status.

    A `down` in `_CRITICAL_DEPS` flips the response to 503 so ECS pulls
    the task. Non-critical `degraded`/`down` deps surface in the body
    without taking the task out of service — fallback paths handle them.
    """
    checks: Dict[str, str] = {
        "db": await _probe_db(),
        "redis": await _probe_redis(),
        "bedrock": _probe_bedrock(),
        "s3": _probe_s3(),
    }
    critical_down = any(checks.get(dep) == "down" for dep in _CRITICAL_DEPS)
    if critical_down:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "unhealthy" if critical_down else "healthy",
        "service": "Fortress Labs Backend",
        "checks": checks,
    }


@router.get("/ready")
async def readiness_check():
    return {"ready": True}


@router.get("/sync")
async def sync_health():
    """P2 sync freshness — reports last sync version, age, and rule count.

    Status:
      - never_synced: no LocalSyncStatus rows
      - ok:    age <= 25h and last sync SUCCESS
      - stale: age > 25h
      - failed: last sync row marked FAILED
    """
    async with get_db() as session:
        last = (
            await session.execute(
                select(LocalSyncStatus)
                .order_by(LocalSyncStatus.sync_timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        rules_count = (
            await session.execute(
                select(func.count())
                .select_from(ComplianceRule)
                .where(ComplianceRule.is_active.is_(True))
            )
        ).scalar() or 0

    if last is None:
        return {
            "status": "never_synced",
            "sync_version": 0,
            "last_synced_at": None,
            "sync_age_hours": None,
            "rules_count": rules_count,
        }

    now = datetime.now(timezone.utc)
    ts = last.sync_timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_hours = (now - ts).total_seconds() / 3600.0

    if last.sync_status == "FAILED":
        status_val = "failed"
    elif age_hours > 25:
        status_val = "stale"
    else:
        status_val = "ok"

    return {
        "status": status_val,
        "sync_version": last.sync_version,
        "last_synced_at": ts.isoformat(),
        "sync_age_hours": round(age_hours, 2),
        "rules_count": rules_count,
        "sync_status": last.sync_status,
    }
