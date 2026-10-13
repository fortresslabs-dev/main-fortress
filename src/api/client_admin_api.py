"""
Client Admin API — provision and manage per-bank API credentials.

All endpoints are under /internal/admin/clients and require the
X-Internal-Secret header (enforced by InternalRouteGuard in main_pipeline1.py).

Endpoints:
  POST   /internal/admin/clients                       Provision new bank
  GET    /internal/admin/clients                       List all banks
  GET    /internal/admin/clients/{customer_id}         Get single bank
  PATCH  /internal/admin/clients/{customer_id}         Update tier / active flag
  POST   /internal/admin/clients/{customer_id}/rotate  Issue new API key
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.services.client_credential_service import ClientCredentialService
from src.services.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/clients", tags=["Client Admin"])

_svc = ClientCredentialService()


# ── Schemas ──────────────────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    customer_id: str = Field(..., min_length=2, max_length=100,
                             pattern=r"^[a-z0-9_\-]+$",
                             description="Lowercase slug, e.g. first_national_bank")
    bank_name: str = Field(..., min_length=2, max_length=200)
    tier: str = Field("standard", pattern=r"^(standard|premium)$")


class ProvisionResponse(BaseModel):
    customer_id: str
    bank_name: str
    tier: str
    api_key: str
    note: str = "Store this key securely — it will not be shown again."


class ClientView(BaseModel):
    customer_id: str
    bank_name: str
    tier: str
    is_active: bool
    created_at: Optional[datetime]
    last_seen_at: Optional[datetime]


class PatchRequest(BaseModel):
    is_active: Optional[bool] = None
    tier: Optional[str] = Field(None, pattern=r"^(standard|premium)$")


class RotateResponse(BaseModel):
    customer_id: str
    api_key: str
    note: str = "Store this key securely — it will not be shown again."


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("", response_model=ProvisionResponse, status_code=201)
async def provision_client(body: ProvisionRequest):
    """Provision a new bank and return their one-time API key."""
    async with get_db() as session:
        try:
            raw_key = await _svc.provision(
                session, body.customer_id, body.bank_name, body.tier
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return ProvisionResponse(
        customer_id=body.customer_id,
        bank_name=body.bank_name,
        tier=body.tier,
        api_key=raw_key,
    )


@router.get("", response_model=List[ClientView])
async def list_clients():
    """List all registered banks (no keys exposed)."""
    async with get_db() as session:
        clients = await _svc.list_clients(session)
    return [
        ClientView(
            customer_id=c.customer_id,
            bank_name=c.bank_name,
            tier=c.tier,
            is_active=c.is_active,
            created_at=c.created_at,
            last_seen_at=c.last_seen_at,
        )
        for c in clients
    ]


@router.get("/{customer_id}", response_model=ClientView)
async def get_client(customer_id: str):
    async with get_db() as session:
        cred = await _svc.get_client(session, customer_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Client not found")
    return ClientView(
        customer_id=cred.customer_id,
        bank_name=cred.bank_name,
        tier=cred.tier,
        is_active=cred.is_active,
        created_at=cred.created_at,
        last_seen_at=cred.last_seen_at,
    )


@router.patch("/{customer_id}", response_model=ClientView)
async def update_client(customer_id: str, body: PatchRequest):
    """Update tier or active status for a bank."""
    async with get_db() as session:
        cred = await _svc.get_client(session, customer_id)
        if not cred:
            raise HTTPException(status_code=404, detail="Client not found")

        try:
            if body.is_active is not None:
                await _svc.set_active(session, customer_id, body.is_active)
            if body.tier is not None:
                from sqlalchemy import update as sa_update
                from src.models.database import ClientCredential
                await session.execute(
                    sa_update(ClientCredential)
                    .where(ClientCredential.customer_id == customer_id)
                    .values(tier=body.tier)
                )
                await session.commit()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        cred = await _svc.get_client(session, customer_id)

    return ClientView(
        customer_id=cred.customer_id,
        bank_name=cred.bank_name,
        tier=cred.tier,
        is_active=cred.is_active,
        created_at=cred.created_at,
        last_seen_at=cred.last_seen_at,
    )


@router.post("/{customer_id}/rotate", response_model=RotateResponse)
async def rotate_key(customer_id: str):
    """Issue a new API key for an existing bank. Invalidates the old key immediately."""
    async with get_db() as session:
        try:
            raw_key = await _svc.rotate_key(session, customer_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    return RotateResponse(customer_id=customer_id, api_key=raw_key)
