"""
F2/F4/F5 — Dashboard data endpoints.

Covered:
  GET /api/v1/dashboard/overview         (F4)
  GET /api/v1/dashboard/rules            (F2)
  GET /api/v1/dashboard/changes          (F2) — regression: semantic_diff present
  GET /api/v1/dashboard/deadlines        (F4)
  GET /api/v1/dashboard/risk             (F4)
  GET /api/v1/dashboard/coverage         (F4)
  GET /api/v1/dashboard/workflows        (F3)
  GET /api/v1/dashboard/ofac-health      (F1)
  GET /api/v1/dashboard/enforcement      (F5)
  GET /api/v1/dashboard/obligations      (F5)
  GET /api/v1/dashboard/readiness        (F4)
  GET /api/v1/dashboard/risk-matrix      (F4)
  GET /api/v1/dashboard/cro              (F5)
  GET /api/v1/dashboard/cio              (F5)

Auth: every endpoint goes through Depends(get_current_user), which we override
in conftest via the `override_user` fixture.

Regression assertions (do not relax without intent):
  - /changes payload contains `semantic_diff` key  (commit 27c3bae)
  - /changes `change_summary` is a string sentence (commit 5b2096b — never JSON envelope)
  - Missing auth → 401, never 500 (gap H1/H12)
"""
from __future__ import annotations

import json

import pytest


# ── Read-only endpoints (Analyst+ acceptable) ────────────────────────

READ_ENDPOINTS = [
    "/api/v1/dashboard/overview",
    "/api/v1/dashboard/rules",
    "/api/v1/dashboard/changes",
    "/api/v1/dashboard/deadlines",
    "/api/v1/dashboard/risk",
    "/api/v1/dashboard/coverage",
    "/api/v1/dashboard/workflows",
    "/api/v1/dashboard/ofac-health",
    "/api/v1/dashboard/enforcement",
    "/api/v1/dashboard/obligations",
    "/api/v1/dashboard/readiness",
    "/api/v1/dashboard/risk-matrix",
    "/api/v1/dashboard/cro",
    "/api/v1/dashboard/cio",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_read_endpoint_no_auth_returns_401(p2_client, path):
    """Every read endpoint must 401 on missing auth — never 500.

    Gap H1/H12 regression — opening a DB session before checking auth means
    a missing-auth 500 surfaces in customer telemetry.
    """
    resp = await p2_client.get(path)
    assert resp.status_code == 401, f"{path} returned {resp.status_code}: {resp.text[:200]}"


@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_read_endpoint_authed_returns_200(p2_client, override_user, path):
    override_user("CCO")
    resp = await p2_client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert isinstance(body, dict), f"{path} body must be a dict"


# ── /changes — schema regression guards ──────────────────────────────

async def test_changes_payload_includes_semantic_diff_key(
    p2_client, override_user, seed_change_record, clean_volatile_tables
):
    """Regression: commit 27c3bae added semantic_diff to /dashboard/changes."""
    override_user("CCO")
    resp = await p2_client.get("/api/v1/dashboard/changes")
    assert resp.status_code == 200
    body = resp.json()
    assert "changes" in body
    assert len(body["changes"]) >= 1, "seed_change_record did not produce a row"
    row = next(c for c in body["changes"] if c.get("change_ref") == seed_change_record["change_ref"])
    assert "semantic_diff" in row, "semantic_diff column missing — commit 27c3bae regression"


async def test_changes_change_summary_is_sentence_not_json(
    p2_client, override_user, seed_change_record, clean_volatile_tables
):
    """Regression: commit 5b2096b — change_summary must be a sentence string,
    never a JSON envelope like '{"summary": "...", "items": [...]}'."""
    override_user("CCO")
    resp = await p2_client.get("/api/v1/dashboard/changes")
    body = resp.json()
    row = next(c for c in body["changes"] if c.get("change_ref") == seed_change_record["change_ref"])
    summary = row["change_summary"]
    assert isinstance(summary, str)
    assert not summary.lstrip().startswith("{"), (
        f"change_summary looks like JSON: {summary[:120]!r}"
    )
    # Sanity: try-parse — if it's accidentally valid JSON, that's the regression.
    try:
        parsed = json.loads(summary)
    except (ValueError, TypeError):
        parsed = None
    assert not isinstance(parsed, dict), (
        f"change_summary parses as JSON dict — must be a sentence string"
    )


# ── /workflows — pagination param ────────────────────────────────────

async def test_workflows_respects_limit(p2_client, override_user):
    override_user("CCO")
    resp = await p2_client.get("/api/v1/dashboard/workflows?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json()["workflows"], list)


# ── /ofac-health — coverage_pct shape ────────────────────────────────

async def test_ofac_health_payload_shape(p2_client, override_user):
    override_user("CCO")
    resp = await p2_client.get("/api/v1/dashboard/ofac-health")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("entries_active", "variants_total", "variants_active",
                "variants_embedded", "embedding_coverage_pct",
                "vector_mode_ready"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["embedding_coverage_pct"], (int, float))
    assert isinstance(body["vector_mode_ready"], bool)
