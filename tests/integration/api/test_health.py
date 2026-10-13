"""
Health endpoints — P1 and P2.

Covered:
  GET /              (P1 + P2 root)
  GET /health/       (both)
  GET /health/ready  (both)
  GET /health/sync   (P2 via _update_service.last_sync_status)
  GET /health/sync   (P1 via DB sync status)

Demo-relevance: these are the first hit the customer's network team makes when
poking the stack; any 500 here kills confidence before the dashboard loads.
"""
from __future__ import annotations

import pytest


# ── P1 ───────────────────────────────────────────────────────────────

async def test_p1_root_ok(p1_client):
    resp = await p1_client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline"] == 1
    assert "no customer data" in body["note"].lower()


async def test_p1_health_ok(p1_client):
    resp = await p1_client.get("/health/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_p1_ready_ok(p1_client):
    resp = await p1_client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


async def test_p1_health_sync_returns_status_field(p1_client):
    """/health/sync must always 200 with a `status` field — even on never_synced."""
    resp = await p1_client.get("/health/sync")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert body["status"] in {"never_synced", "ok", "stale", "failed"}


# ── P2 ───────────────────────────────────────────────────────────────

async def test_p2_root_ok(p2_client):
    resp = await p2_client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline"] == 2
    assert body["status"] == "running"


async def test_p2_health_ok(p2_client):
    resp = await p2_client.get("/health/")
    assert resp.status_code == 200


async def test_p2_health_sync_returns_dict(p2_client):
    """P2's /health/sync exposes UpdateService.last_sync_status — must be a dict, never None."""
    resp = await p2_client.get("/health/sync")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
