"""
F3 — JIRA webhook contract tests.

Covered:
  POST /webhooks/jira
    - no auth header              → 401 (fail-closed)
    - wrong bearer secret         → 401
    - correct bearer secret       → 202/200, returns workflow_id + status
    - HMAC mode with valid sig    → 200
    - HMAC mode with bad sig      → 401
    - DEV_MODE=true bypass        → 200 (must NEVER be true in prod — see B4)

Conftest sets JIRA_WEBHOOK_SECRET=test-webhook-secret and JIRA_AUTH_MODE=bearer.
Tests that need HMAC mode monkeypatch env in-place.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os

import pytest


VALID_PAYLOAD = {
    "issue": {
        "key": "DEMO-1",
        "fields": {
            "summary": "Wire transfer compliance check",
            "description": (
                "When a wire transfer exceeds $10,000 USD, the system must "
                "log a CTR. Validate against BSA threshold."
            ),
        },
    }
}


# ── Bearer mode (default) ────────────────────────────────────────────

async def test_jira_webhook_no_auth_returns_401(p2_client, clean_volatile_tables):
    resp = await p2_client.post("/webhooks/jira", json=VALID_PAYLOAD)
    assert resp.status_code == 401


async def test_jira_webhook_wrong_bearer_returns_401(p2_client, clean_volatile_tables):
    resp = await p2_client.post(
        "/webhooks/jira",
        json=VALID_PAYLOAD,
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


async def test_jira_webhook_bearer_ok_returns_workflow_id(p2_client, clean_volatile_tables):
    resp = await p2_client.post(
        "/webhooks/jira",
        json=VALID_PAYLOAD,
        headers={"Authorization": "Bearer test-webhook-secret"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "processing"
    assert "workflow_id" in body
    assert body["jira_key"] == "DEMO-1"


async def test_jira_webhook_missing_description_returns_400(p2_client, clean_volatile_tables):
    resp = await p2_client.post(
        "/webhooks/jira",
        json={"issue": {"key": "DEMO-2", "fields": {"summary": "x"}}},
        headers={"Authorization": "Bearer test-webhook-secret"},
    )
    assert resp.status_code == 400


# ── HMAC mode ────────────────────────────────────────────────────────

async def test_jira_webhook_hmac_valid_sig_ok(p2_client, monkeypatch, clean_volatile_tables):
    monkeypatch.setenv("JIRA_AUTH_MODE", "hmac")
    body = json.dumps(VALID_PAYLOAD).encode()
    sig = _hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    resp = await p2_client.post(
        "/webhooks/jira",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={sig}",
        },
    )
    assert resp.status_code == 200, resp.text


async def test_jira_webhook_hmac_bad_sig_returns_401(p2_client, monkeypatch, clean_volatile_tables):
    monkeypatch.setenv("JIRA_AUTH_MODE", "hmac")
    body = json.dumps(VALID_PAYLOAD).encode()
    resp = await p2_client.post(
        "/webhooks/jira",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert resp.status_code == 401


# ── DEV_MODE bypass (gap B4) ─────────────────────────────────────────

async def test_jira_webhook_dev_mode_bypasses_auth(p2_client, monkeypatch, clean_volatile_tables):
    """DEV_MODE=true allows unauthenticated webhooks in non-production envs only.
    Conftest sets ENVIRONMENT=test, so the bypass fires here."""
    monkeypatch.setenv("DEV_MODE", "true")
    resp = await p2_client.post("/webhooks/jira", json=VALID_PAYLOAD)
    assert resp.status_code == 200


async def test_jira_webhook_dev_mode_in_production_refused(
    p2_client, monkeypatch, clean_volatile_tables
):
    """B4 regression: even with DEV_MODE=true, ENVIRONMENT=production must
    fail-closed. Otherwise one stray env var on a prod stack silently turns
    off webhook signature verification."""
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    resp = await p2_client.post("/webhooks/jira", json=VALID_PAYLOAD)
    assert resp.status_code == 401
