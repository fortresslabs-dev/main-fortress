"""
src/api/sync_auth.py
FastAPI dependency that validates every P2 → P1 sync request.

Two checks must both pass:
  1. IP allowlist  — bank's NAT Gateway EIP must be in WAF allowlist
                     (enforced at WAF level — handled outside this code)
  2. API key       — X-Api-Key header must hash-match client_credentials table

Usage in sync_api.py:
    from src.api.sync_auth import require_sync_auth
    @router.post("/postgres")
    async def sync_postgres(auth=Depends(require_sync_auth), ...):
        customer_id = auth["customer_id"]
"""
from __future__ import annotations

import structlog
from fastapi import Depends, HTTPException, Header
from typing import Annotated, Optional

from src.services.client_credential_service import ClientCredentialService
from src.services.database import get_db

logger = structlog.get_logger(__name__)

_svc = ClientCredentialService()


async def require_sync_auth(
    x_api_key:    Annotated[Optional[str], Header()] = None,
    x_customer_id: Annotated[Optional[str], Header()] = None,
) -> dict:
    """
    FastAPI dependency for all /api/sync/* endpoints.

    P2 must send:
      X-Customer-Id: firstnational
      X-Api-Key:     <raw 64-char hex key>

    Returns {"customer_id": "firstnational"} on success.
    Raises HTTP 401 on any failure — never reveals why.
    """
    if not x_api_key or not x_customer_id:
        logger.warning("sync_auth_missing_headers",
                       has_key=bool(x_api_key),
                       has_customer=bool(x_customer_id))
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with get_db() as session:
        valid = await _svc.validate(session, x_customer_id, x_api_key)

    if not valid:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {"customer_id": x_customer_id}


# Type alias for use in route signatures
SyncAuth = Annotated[dict, Depends(require_sync_auth)]
