"""TEXT MIND Backend - Main Application"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from src.api import health, webhooks, approvals, agents_api
from src.api.agent_api import router as compliance_agent_router
from src.services.database import init_db, close_db
from src.services.redis_service import init_redis, close_redis
from src.utils.config import settings
from src.utils.logger import setup_logging

setup_logging()
logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting TEXT MIND Backend")
    await init_db()
    await init_redis()
    yield
    await close_db()
    await close_redis()

app = FastAPI(
    title="TEXT MIND Backend",
    description="AI-Powered Banking Test Automation",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(approvals.router, prefix="/approvals", tags=["Approvals"])
app.include_router(agents_api.router, prefix="/agents", tags=["Agents"])
app.include_router(compliance_agent_router)

@app.get("/")
async def root():
    return {"service": "TEXT MIND", "version": "1.0.0", "status": "running"}
