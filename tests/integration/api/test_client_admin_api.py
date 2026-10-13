"""
H3 — /internal/admin/clients regression locks.

`ClientCredentialService` was missing `list_clients` / `get_client` /
`update_status` / `rotate_key`, so the four admin endpoints 500'd in
production while only POST provision worked. These tests pin the
contract end-to-end against the real Postgres + InternalRouteGuard.

Auth: when `FORTRESS_INTERNAL_SECRET` is empty (as in test env), the
guard passes everything through, so we exercise the actual handler
logic without faking auth.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def new_customer_id() -> str:
    return f"h3-test-{uuid.uuid4().hex[:8]}"


# ── POST provision ───────────────────────────────────────────────────

async def test_h3_provision_new_client(p1_client, new_customer_id):
    resp = await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "H3 Test Bank"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["customer_id"] == new_customer_id
    assert body["bank_name"] == "H3 Test Bank"
    assert body["tier"] == "standard"
    assert len(body["api_key"]) == 64  # secrets.token_hex(32)


async def test_h3_provision_duplicate_returns_409(p1_client, new_customer_id):
    await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "First"},
    )
    resp = await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "Second"},
    )
    assert resp.status_code == 409


# ── GET list ─────────────────────────────────────────────────────────

async def test_h3_list_clients(p1_client, new_customer_id):
    """Was 500 in prod — `_svc.list_clients` did not exist."""
    await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "H3 List Bank"},
    )
    resp = await p1_client.get("/internal/admin/clients")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list)
    matching = [r for r in rows if r["customer_id"] == new_customer_id]
    assert matching, f"provisioned client missing from list: {new_customer_id}"
    row = matching[0]
    assert row["bank_name"] == "H3 List Bank"
    assert row["is_active"] is True
    assert "api_key" not in row  # never leak the hash or key in list


# ── GET single ───────────────────────────────────────────────────────

async def test_h3_get_client_found(p1_client, new_customer_id):
    """Was 500 in prod — `_svc.get_client` did not exist."""
    await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "H3 Get Bank"},
    )
    resp = await p1_client.get(f"/internal/admin/clients/{new_customer_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer_id"] == new_customer_id
    assert body["bank_name"] == "H3 Get Bank"


async def test_h3_get_client_not_found_returns_404(p1_client):
    resp = await p1_client.get("/internal/admin/clients/no-such-bank-xyz")
    assert resp.status_code == 404


# ── PATCH (update_status / tier) ─────────────────────────────────────

async def test_h3_patch_deactivates_client(p1_client, new_customer_id):
    """Was 500 in prod — `_svc.update_status` did not exist."""
    await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "H3 Patch Bank"},
    )
    resp = await p1_client.patch(
        f"/internal/admin/clients/{new_customer_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


async def test_h3_patch_unknown_client_returns_404(p1_client):
    resp = await p1_client.patch(
        "/internal/admin/clients/no-such-bank",
        json={"is_active": False},
    )
    assert resp.status_code == 404


# ── POST rotate ──────────────────────────────────────────────────────

async def test_h3_rotate_issues_new_key(p1_client, new_customer_id):
    """Was 500 in prod — `_svc.rotate_key` did not exist."""
    prov = await p1_client.post(
        "/internal/admin/clients",
        json={"customer_id": new_customer_id, "bank_name": "H3 Rotate Bank"},
    )
    original_key = prov.json()["api_key"]

    resp = await p1_client.post(
        f"/internal/admin/clients/{new_customer_id}/rotate"
    )
    assert resp.status_code == 200, resp.text
    new_key = resp.json()["api_key"]
    assert len(new_key) == 64
    assert new_key != original_key
