"""
F1 — SDN screening contract tests.

Covered:
  POST /api/v1/screen/sdn
    - auth fail (no creds)            → 401
    - dashboard Bearer (dev token)    → 200, decision in {PASS, REVIEW, HOLD}
    - HMAC (X-Api-Key + X-Customer-Id) → 200
    - response shape: screening_id, decision, final_score, matches[], screened_at, latency_ms
  GET  /api/v1/screen/sdn/{id}
    - unknown id → 404
    - auth fail  → 401

Notes:
  The OFAC engine + DB are real; on an empty test DB the screener returns
  PASS with zero matches. We assert *contract*, not match content — match
  quality is covered separately by the calibration runbook.
"""
from __future__ import annotations

import pytest


# ── POST /screen/sdn ─────────────────────────────────────────────────

async def test_screen_sdn_no_auth_returns_401(p2_client):
    resp = await p2_client.post("/api/v1/screen/sdn", json={"name": "John Doe"})
    assert resp.status_code == 401


async def test_screen_sdn_dashboard_bearer_ok(p2_client, override_screening_auth):
    override_screening_auth(actor_kind="dashboard")
    resp = await p2_client.post(
        "/api/v1/screen/sdn",
        json={"name": "Jane Doe", "country": "US"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Contract shape
    for key in ("screening_id", "decision", "final_score", "matches", "screened_at", "latency_ms"):
        assert key in body, f"missing key: {key}"
    assert body["decision"] in {"PASS", "REVIEW", "HOLD"}
    assert isinstance(body["matches"], list)


async def test_screen_sdn_hmac_path_ok(p2_client, override_screening_auth):
    """HMAC headers map through require_screening_auth → require_sync_auth."""
    override_screening_auth(actor_kind="api", customer_id="testbank-demo")
    resp = await p2_client.post(
        "/api/v1/screen/sdn",
        json={"name": "Acme Corp"},
    )
    assert resp.status_code == 200


async def test_screen_sdn_validates_request_body(p2_client, override_screening_auth):
    """Pydantic must reject empty name (Field min_length=1)."""
    override_screening_auth()
    resp = await p2_client.post("/api/v1/screen/sdn", json={"name": ""})
    assert resp.status_code == 422


# ── GET /screen/sdn/{id} ─────────────────────────────────────────────

async def test_get_screening_unknown_id_returns_404(p2_client, override_screening_auth):
    override_screening_auth()
    resp = await p2_client.get("/api/v1/screen/sdn/does-not-exist-xyz")
    assert resp.status_code == 404


async def test_get_screening_no_auth_returns_401(p2_client):
    resp = await p2_client.get("/api/v1/screen/sdn/anything")
    assert resp.status_code == 401


# ── B3 regression: dev-token bypass requires DEV_MODE ────────────────

async def test_dev_token_rejected_without_dev_mode(p2_client, bearer_headers):
    """B3 regression: bearer == PIPELINE2_API_TOKEN must NOT short-circuit
    auth when DEV_MODE is false. Otherwise a missing COGNITO_USER_POOL_ID
    silently grants CCO to anyone with the historical "changeme-token"."""
    # Hit a P2 endpoint that goes through get_current_user (not the screening
    # dual-auth dep, which we override elsewhere). /api/v1/dashboard/overview
    # is the canonical Depends(get_current_user) path.
    resp = await p2_client.get("/api/v1/dashboard/overview", headers=bearer_headers)
    # With DEV_MODE=false in conftest and no Cognito pool in test env, the
    # request must NOT succeed via the dev-token branch. Either 401 (token
    # mismatch) or 503 (no Cognito pool) is acceptable — both fail closed.
    assert resp.status_code in (401, 503), (
        f"dev-token leaked past auth without DEV_MODE — got {resp.status_code}: {resp.text[:200]}"
    )
