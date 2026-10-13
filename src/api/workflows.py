"""Workflow API Endpoints"""
from fastapi import APIRouter, HTTPException
from typing import Dict
import structlog

from src.services.database import get_db
from src.models.database import WorkflowExecution

router = APIRouter()
logger = structlog.get_logger()

@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str) -> Dict:
    """Get workflow results"""
    try:
        async with get_db() as db:
            workflow = await db.get(WorkflowExecution, workflow_id)
            
            if not workflow:
                raise HTTPException(404, "Workflow not found")
            
            return {
                "workflow_id": str(workflow.id),
                "jira_key": workflow.jira_story_key,
                "status": workflow.status.value,
                "started_at": workflow.started_at.isoformat() if workflow.started_at else None,
                "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None,
                "artifacts": workflow.artifacts or {},
                "quality_score": workflow.artifacts.get("quality_score", 0) if workflow.artifacts else 0
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_workflow_failed", workflow_id=workflow_id, error=str(e))
        raise HTTPException(500, f"Failed to retrieve workflow: {str(e)}")