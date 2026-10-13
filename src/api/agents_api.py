"""Agent Management Endpoints"""
from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
async def get_agents_status():
    """Get status of all agents"""
    return {"agents": ["discovery", "understanding", "framework", "generation", "validation", "execution"]}
