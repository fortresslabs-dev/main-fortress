"""Admin API — compliance data loading and system management"""
from fastapi import APIRouter
import structlog

from src.services.compliance_service import get_compliance_service

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/load-compliance-rules")
async def load_compliance_rules(skip_sdn_download: bool = True):
    """
    Load USA compliance rules from connectors (OCC BSA, FinCEN SAR, OFAC SDN).

    Query params:
        skip_sdn_download: Skip the live OFAC SDN CSV download (default True for dev).
    """
    svc = get_compliance_service()
    summary = svc.load_from_connectors(skip_sdn_download=skip_sdn_download)
    logger.info("admin_compliance_loaded", **summary)
    return {"status": "loaded", **summary}


@router.get("/compliance-status")
async def compliance_status():
    """Check whether compliance rules have been loaded."""
    svc = get_compliance_service()
    if not svc.is_loaded:
        return {"loaded": False, "message": "No rules loaded yet. POST /admin/load-compliance-rules to load."}

    return {
        "loaded": True,
        "regulations": len(svc.get_all_regulations()),
        "requirements": len(svc.get_all_requirements()),
        "test_scenarios": len(svc.get_all_test_scenarios()),
    }
