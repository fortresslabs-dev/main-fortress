"""
FastAPI routes for the LangGraph Autonomous Compliance Agent.

Endpoints:
    POST /agent/run           — Run agent for a single source
    POST /agent/run-all       — Run agent for all / filtered sources
    GET  /agent/sources       — List available compliance sources
    GET  /agent/runs          — List past agent runs (from DB)
    GET  /agent/runs/{run_id} — Get details of a specific run
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from src.agents.langgraph.compliance_agent import (
        load_sources,
        run_all_sources,
        run_compliance_agent,
    )
except ImportError:
    load_sources = None
    run_all_sources = None
    run_compliance_agent = None

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/agent", tags=["Compliance Agent"])


# ── Request / Response schemas ──────────────────────────────────────

class RunSourceRequest(BaseModel):
    source_id: str = Field(..., description="ID from compliance_sources.yaml")
    max_iterations: int = Field(5, ge=1, le=10)


class RunAllRequest(BaseModel):
    priority: Optional[int] = Field(None, ge=1, le=3)
    max_iterations: int = Field(5, ge=1, le=10)


class RunResponse(BaseModel):
    status: str
    source_id: str
    message: str


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/sources")
async def list_sources():
    """Return the list of configured compliance sources."""
    if load_sources is None:
        raise HTTPException(503, "LangGraph agent is not available (langgraph not installed)")
    sources = load_sources()
    return {"count": len(sources), "sources": sources}


@router.post("/run", response_model=RunResponse)
async def run_single_source(req: RunSourceRequest, background_tasks: BackgroundTasks):
    """Kick off the agent for a single compliance source (runs in background)."""
    if load_sources is None or run_compliance_agent is None:
        raise HTTPException(503, "LangGraph agent is not available (langgraph not installed)")
    sources = load_sources()
    match = [s for s in sources if s["source_id"] == req.source_id]
    if not match:
        available = [s["source_id"] for s in sources]
        raise HTTPException(404, f"Source '{req.source_id}' not found. Available: {available}")

    def _run():
        run_compliance_agent(match[0], max_iterations=req.max_iterations)

    background_tasks.add_task(_run)

    return RunResponse(
        status="started",
        source_id=req.source_id,
        message=f"Agent started for {match[0]['name']} (max {req.max_iterations} iterations)",
    )


@router.post("/run-all", response_model=RunResponse)
async def run_all(req: RunAllRequest, background_tasks: BackgroundTasks):
    """Kick off the agent for all sources (optionally filtered by priority)."""
    if run_all_sources is None:
        raise HTTPException(503, "LangGraph agent is not available (langgraph not installed)")

    def _run():
        run_all_sources(max_iterations=req.max_iterations, priority_filter=req.priority)

    background_tasks.add_task(_run)

    label = f"priority={req.priority}" if req.priority else "all"
    return RunResponse(
        status="started",
        source_id=label,
        message=f"Agent batch started ({label}, max {req.max_iterations} iterations)",
    )
