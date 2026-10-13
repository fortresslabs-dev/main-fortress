"""
Pipeline 1 Integration Tests — Fortress Cloud (Read-Only API).

Tests:
  - Compliance rules API returns data from DB
  - OFAC SDN API returns parsed entries
  - PII rejection middleware blocks customer data
  - Internal agent routes are guarded
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.main_pipeline1 import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Health ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pipeline"] == 1
    assert "read-only" in data["note"].lower() or "no customer data" in data["note"].lower()


# ── Compliance Rules API ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_rules_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/rules", params={"geography": "usa"})
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data or "count" in data


@pytest.mark.asyncio
async def test_get_ofac_sdn_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/ofac/sdn-list", params={"limit": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data or "count" in data


@pytest.mark.asyncio
async def test_get_rule_changes_returns_200(client: AsyncClient):
    resp = await client.get("/api/v1/rules/changes", params={"since": "2026-01-01"})
    assert resp.status_code == 200


# ── PII Rejection Middleware ────────────────────────────────────────

@pytest.mark.asyncio
async def test_pii_in_query_param_rejected(client: AsyncClient):
    """Pipeline 1 must reject requests containing PII query params."""
    resp = await client.get("/api/v1/rules", params={"geography": "usa", "account_number": "1234"})
    assert resp.status_code == 400
    assert "customer data" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pii_in_body_rejected(client: AsyncClient):
    """Pipeline 1 must reject POST bodies containing PII fields."""
    resp = await client.post(
        "/internal/agent/run",
        json={"source_id": "ofac_sdn", "beneficiary_name": "John Doe"},
    )
    assert resp.status_code in (400, 403)


# ── Internal Route Guard ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_internal_route_requires_secret(client: AsyncClient, monkeypatch):
    """If FORTRESS_INTERNAL_SECRET is set, unauthenticated callers get 403."""
    import src.main_pipeline1 as p1
    monkeypatch.setattr(p1, "_INTERNAL_SECRET", "my-secret-token")

    resp = await client.post(
        "/internal/agent/run",
        json={"source_id": "ofac_sdn"},
        headers={"X-Internal-Secret": "wrong-token"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_public_routes_always_accessible(client: AsyncClient):
    """Public routes should work regardless of internal secret."""
    resp = await client.get("/api/v1/rules", params={"geography": "usa"})
    assert resp.status_code == 200
