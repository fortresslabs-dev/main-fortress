"""
PIPELINE 1: Fortress Labs Cloud — Training & Storage Server.

This is the Fortress-hosted backend that:
  - Scrapes 18+ regulatory sources (via LangGraph autonomous agent)
  - Stores compliance rules in Aurora (pgvector) and OpenSearch Serverless
  - Serves a READ-ONLY API for customer agents to fetch rules
  - NEVER receives or stores customer data

Endpoints (public):
  GET  /health                                  Health check
  GET  /api/v1/rules                            Query compliance rules
  GET  /api/v1/rules/changes                    Get rule changes since date
  GET  /api/v1/ofac/sdn-list                    Full OFAC SDN list

Internal (network-guarded, not exposed to customers):
  GET  /internal/agent/sources                  List configured sources
  POST /internal/agent/run                      Trigger scraper
  POST /internal/agent/run-all                  Trigger all scrapers
"""
from contextlib import asynccontextmanager
import os

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from src.api import health
from src.api.compliance_rules_api import router as rules_router
from src.api.agent_api import router as agent_router
from src.api.sync_api import router as sync_router
from src.api.client_admin_api import router as client_admin_router
from src.services.database import init_db, close_db
from src.services.redis_service import init_redis, close_redis
from src.services.scraper_scheduler import ScraperScheduler
from src.utils.config import settings
from src.utils.logger import setup_logging

setup_logging()
logger = structlog.get_logger()


# ── PII Rejection Middleware ────────────────────────────────────────
# Defence-in-depth: reject any request containing customer PII fields.

_PII_FIELD_DENYLIST = frozenset({
    "account_number", "ssn", "social_security", "beneficiary_name",
    "sender_name", "party_name", "customer_name", "date_of_birth",
    "tax_id", "tin", "routing_number", "card_number", "cvv",
    "transaction_id", "aadhaar", "pan_number",
})


class PIIRejectionMiddleware(BaseHTTPMiddleware):
    """Reject requests that include customer PII fields in body or query."""

    async def dispatch(self, request: Request, call_next):
        # Check query parameters
        for param in request.query_params:
            if param.lower() in _PII_FIELD_DENYLIST:
                logger.warning(
                    "pii_rejected_query_param",
                    param=param,
                    path=request.url.path,
                )
                return Response(
                    content='{"detail":"Customer data not accepted by Pipeline 1"}',
                    status_code=400,
                    media_type="application/json",
                )

        # Check JSON body for POST/PUT/PATCH (internal agent routes only)
        if request.method in ("POST", "PUT", "PATCH"):
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = await request.json()
                    if isinstance(body, dict):
                        for key in body:
                            if key.lower() in _PII_FIELD_DENYLIST:
                                logger.warning(
                                    "pii_rejected_body_field",
                                    field=key,
                                    path=request.url.path,
                                )
                                return Response(
                                    content='{"detail":"Customer data not accepted by Pipeline 1"}',
                                    status_code=400,
                                    media_type="application/json",
                                )
                except Exception:
                    pass  # Not JSON body — let through to normal validation

        return await call_next(request)


# ── Internal-only route guard ───────────────────────────────────────

_INTERNAL_SECRET = os.getenv("FORTRESS_INTERNAL_SECRET", "")


class InternalRouteGuard(BaseHTTPMiddleware):
    """Restrict /internal/* to requests bearing the internal secret header."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/internal"):
            token = request.headers.get("X-Internal-Secret", "")
            if _INTERNAL_SECRET and token != _INTERNAL_SECRET:
                return Response(
                    content='{"detail":"Forbidden"}',
                    status_code=403,
                    media_type="application/json",
                )
        return await call_next(request)


# ── App lifecycle ───────────────────────────────────────────────────

async def _ensure_sync_version_seed() -> None:
    """Seed sync_versions(id=1, current_version=0) if the row is missing.

    /sync/postgres reads SyncVersion.id=1 on every customer pull. On a fresh
    schema (e.g. first deploy, demo reset, integration-test DB) the row only
    gets created when a scrape runs, so the first customer call sees
    current_version=NULL and the dashboard "what changed" panel breaks.
    Idempotent — ON CONFLICT DO NOTHING means repeat startups are no-ops.
    """
    from sqlalchemy import text as sqla_text
    from src.services.database import async_session_maker
    try:
        async with async_session_maker() as session:
            await session.execute(sqla_text("""
                INSERT INTO sync_versions (id, current_version, updated_at)
                VALUES (1, 0, NOW())
                ON CONFLICT (id) DO NOTHING
            """))
            await session.commit()
        logger.info("sync_version_seed_ensured")
    except Exception as exc:
        # Non-fatal: if the table doesn't exist yet (mid-migration), the
        # next scraper run still owns creation. Don't block boot on it.
        logger.warning("sync_version_seed_skipped", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fortress Labs Pipeline 1 — Training & Storage")
    await init_db()
    await init_redis()
    await _ensure_sync_version_seed()
    # In production EventBridge → EcsTaskDefScraperP1 (scripts/run_scrape.py) owns
    # the daily cron. Enabling the in-process scheduler here would cause two
    # scrapes per day at unrelated times. Gate it behind an opt-in env var so
    # local dev / failover scenarios can still use it.
    scheduler: ScraperScheduler | None = None
    if settings.SCRAPER_IN_PROCESS:
        scheduler = ScraperScheduler()
        scheduler.start()
    else:
        logger.info("scraper_in_process_disabled",
                    note="EventBridge → run_scrape.py is the active path")
    yield
    if scheduler is not None:
        scheduler.stop()
    await close_db()
    await close_redis()


app = FastAPI(
    title="Fortress Labs — Compliance Rules API",
    description=(
        "Pipeline 1: Read-only compliance rules serving. "
        "No customer data is accepted or stored."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# ── B8: structured error envelope ───────────────────────────────────
# Default FastAPI 500 returns `Internal Server Error` text. P2 clients
# need a stable JSON shape so retries / circuit breakers can key on
# `code`, not parse a string. The handler stays narrow on purpose: it
# only wraps un-handled exceptions (HTTPException flows through normally
# so per-endpoint 401/403/404 stay unchanged).

from fastapi import HTTPException as _HTTPException
from fastapi.responses import JSONResponse as _JSONResponse


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, _HTTPException):
        # Re-raise — FastAPI's HTTPException handler owns the shape.
        raise exc
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    return _JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "detail": "An internal error occurred. The request was not processed.",
            "path": request.url.path,
        },
    )


# Middleware (outermost first)
app.add_middleware(PIIRejectionMiddleware)
app.add_middleware(InternalRouteGuard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],  # Read-only: no POST/PUT/DELETE for rules
    allow_headers=["Authorization", "Content-Type", "X-Customer-Id", "X-Api-Key"],
)

# ── Public API (customer agents call these) ─────────────────────────
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(rules_router)

# ── Sync API (customer Pipeline 2 pulls updates from here) ─────────
app.include_router(sync_router)

# ── Internal API (fortress ops only, guarded by network/auth) ───────
app.include_router(agent_router, prefix="/internal")
app.include_router(client_admin_router, prefix="/internal")


@app.get("/")
async def root():
    return {
        "service": "Fortress Labs — Compliance Rules API",
        "pipeline": 1,
        "version": "2.0.0",
        "status": "running",
        "note": "Read-only compliance rules. No customer data accepted.",
    }
