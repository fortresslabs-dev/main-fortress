"""
F3 — Workflows + Approvals contract tests.

Covered:
  GET  /workflows/{id}              → 200 (happy) | 404 (unknown id)
  POST /approvals/                  → 401 no auth | 403 non-approver | 200 CCO
  GET  /approvals/{workflow_id}     → 200 with `approvals` list

Notes:
  - /workflows/{id} currently has NO auth (open endpoint, gap to flag for pilot).
    Test pins current behavior; flip to expect 401 when auth is added.
  - /approvals/ POST flips workflow status to running/failed and writes to
    checkpoint_approvals. Cleanup via `clean_volatile_tables`.
"""
from __future__ import annotations

import pytest


# ── GET /workflows/{id} ──────────────────────────────────────────────

async def test_get_workflow_unknown_returns_404(p2_client):
    resp = await p2_client.get("/workflows/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_get_workflow_happy(p2_client, seed_workflow, clean_volatile_tables):
    resp = await p2_client.get(f"/workflows/{seed_workflow['workflow_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workflow_id"] == seed_workflow["workflow_id"]
    assert body["status"] == "running"
    assert body["jira_key"] == "DEMO-1"


# ── POST /approvals/ ────────────────────────────────────────────────

async def test_create_approval_no_auth_returns_401(p2_client, seed_workflow, clean_volatile_tables):
    resp = await p2_client.post("/approvals/", json={
        "workflow_id": seed_workflow["workflow_id"],
        "checkpoint": "pre_generation",
        "decision": "approved",
    })
    assert resp.status_code == 401


async def test_create_approval_analyst_role_forbidden(
    p2_client, override_user, seed_workflow, clean_volatile_tables
):
    """Analyst is NOT an approver — must 403, not 200."""
    override_user("Analyst")
    # override_user sets *every* role dep to the same user. require_approver
    # checks can_approve, which Analyst lacks — but the override blanket-injects
    # the user past the dep. To meaningfully test RBAC, override only
    # get_current_user and let the role-gating dep do its work.
    from src.api.auth import get_current_user, CurrentUser
    from src.main_pipeline2 import app
    # Restrict the override to get_current_user so require_approver re-evaluates.
    app.dependency_overrides.clear()
    analyst = CurrentUser(
        sub="t", email="a@t", role="Analyst", groups=["Analyst"], bank_id="testbank-demo"
    )
    app.dependency_overrides[get_current_user] = lambda: analyst

    resp = await p2_client.post("/approvals/", json={
        "workflow_id": seed_workflow["workflow_id"],
        "checkpoint": "pre_generation",
        "decision": "approved",
    })
    assert resp.status_code == 403


async def test_create_approval_cco_happy(
    p2_client, override_user, seed_workflow, clean_volatile_tables
):
    override_user("CCO")
    resp = await p2_client.post("/approvals/", json={
        "workflow_id": seed_workflow["workflow_id"],
        "checkpoint": "pre_generation",
        "decision": "approved",
        "note": "Looks good for demo.",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "recorded"
    assert body["decision"] == "approved"
    assert body["workflow_id"] == seed_workflow["workflow_id"]


async def test_create_approval_unknown_workflow_returns_404(
    p2_client, override_user, clean_volatile_tables
):
    override_user("CCO")
    resp = await p2_client.post("/approvals/", json={
        "workflow_id": "00000000-0000-0000-0000-000000000000",
        "checkpoint": "pre_generation",
        "decision": "approved",
    })
    assert resp.status_code == 404


# ── GET /approvals/{workflow_id} ────────────────────────────────────

async def test_get_approvals_lists_recorded(
    p2_client, override_user, seed_workflow, clean_volatile_tables
):
    override_user("CCO")
    # Record one approval
    await p2_client.post("/approvals/", json={
        "workflow_id": seed_workflow["workflow_id"],
        "checkpoint": "pre_generation",
        "decision": "approved",
    })

    resp = await p2_client.get(f"/approvals/{seed_workflow['workflow_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert "approvals" in body
    assert any(a.get("checkpoint_name") == "pre_generation" for a in body["approvals"])
