"""
PIPELINE 2: Customer Production Infrastructure.

Deployed as a Docker container INSIDE the customer's VPC / on-premises.
ALL customer data (JIRA stories, transactions, PII) stays local.
Only compliance rules are fetched from Fortress API (public regulatory data).

Workflows:
  A) Developer — VS Code plugin → JIRA story → 6-agent pipeline → pytest code
  B) Production — Core banking → wire transfer validation → block/allow
  C) OFAC — Transaction → beneficiary screening → block if match
  D) Loans — Loan application → Section 1071 demographic check

Endpoints:
  GET  /health                              Health check
  POST /webhooks/jira                       JIRA webhook → 6-agent pipeline
  GET  /workflows/{id}                      Workflow status
  POST /approvals                           Checkpoint approval
  GET  /agents/status                       Agent pipeline status
  POST /validate/wire-transfer              Wire transfer compliance check
  POST /validate/ofac-screen                OFAC sanctions screening
  POST /validate/loan-application           Section 1071 validation
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import health, webhooks, approvals, agents_api
from src.api import workflows
from src.api.validation_api import router as validation_router
from src.api.dashboard_api import router as dashboard_router
from src.api.dashboard_api import architecture_router
from src.api.dashboard_api import screening_router as dashboard_screening_router
from src.api.generate_test_api import router as generate_test_router
from src.api.internal_sync import router as internal_sync_router
from src.api.screening_api import router as screening_router
from src.api.exports import router as exports_router
from src.services.database import init_db, close_db
from src.services.redis_service import init_redis, close_redis
from src.services.update_service import UpdateService
from src.utils.config import settings
from src.utils.logger import setup_logging

setup_logging()
logger = structlog.get_logger()

_update_service = UpdateService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fortress Agent — Customer Pipeline 2")
    await init_db()
    await init_redis()
    _update_service.start()
    app.state.update_service = _update_service
    yield
    _update_service.stop()
    await close_db()
    await close_redis()


app = FastAPI(
    title="Fortress Agent — Customer Infrastructure",
    description=(
        "Pipeline 2: Runs inside customer VPC. "
        "All customer data stays local. "
        "Fetches compliance rules from Fortress API (read-only)."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-E2E-Role"],
)

# ── Core workflows (developer + ops) ───────────────────────────────
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(approvals.router, prefix="/approvals", tags=["Approvals"])
app.include_router(agents_api.router, prefix="/agents", tags=["Agents"])

# ── Workflow status ─────────────────────────────────────────────────
try:
    app.include_router(workflows.router, prefix="/workflows", tags=["Workflows"])
except AttributeError:
    pass  # workflows module may not have router yet

# ── Production validation endpoints ─────────────────────────────────
app.include_router(validation_router)

# ── Production OFAC/SDN screening API (bank backends + dashboard) ──
app.include_router(screening_router)

# ── CCO/CRO/CIO dashboard (React SPA) ──────────────────────────────
app.include_router(dashboard_router)
app.include_router(architecture_router)
app.include_router(dashboard_screening_router)
app.include_router(generate_test_router)

# ── Examination Evidence & Reports exports (design doc PR #7) ──────
app.include_router(exports_router)

# ── Internal ops (sync trigger) ────────────────────────────────────
app.include_router(internal_sync_router, prefix="/internal/sync", tags=["Internal"])


@app.get("/")
async def root():
    return {
        "service": "Fortress Agent — Customer Infrastructure",
        "pipeline": 2,
        "version": "2.0.0",
        "status": "running",
        "note": "All customer data stays local. Only compliance rules fetched from Fortress API.",
    }
