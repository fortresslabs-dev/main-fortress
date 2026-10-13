"""
Regression locks — H1 / H2 / H14 (all CLOSED).

These started life as `xfail(strict=True)` open-gap stubs asserting
the post-fix contract. All three gaps are now closed in the code —
the xfail markers have been removed and the tests are permanent
regression locks against the same behavior coming back:

  H1  Every protected /api/sync/* POST declares require_sync_auth as a
      Depends, so unauthenticated callers get a uniform 401 before any
      DB session is opened. 14 endpoints × 2 assertions each.
  H2  /api/sync/status rejects unauthenticated callers; valid HMAC
      still works.
  H14 /health probes DB / Redis / Bedrock / S3 and returns 503 if a
      critical dep is down.

If any of these fails on a future commit, the corresponding gap has
been reintroduced — see `production-readiness-gaps.md` for context.
Run via `make test-demo`.
"""
from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────
# H1 — every /sync/* POST must declare require_sync_auth as a Depends
# ─────────────────────────────────────────────────────────────────────
#
# Today most handlers call _verify_api_key() inline from the request
# body. That has two real problems:
#   (a) the 401 path varies per endpoint — inconsistent error shape;
#   (b) a DB session is opened to look up the credential **before** the
#       caller is rejected, so a DB outage produces 500 instead of 401.
#
# Acceptance (production-readiness-gaps.md H1): every protected route
# uses `auth=Depends(require_sync_auth)` and reads customer_id from the
# returned context; no inline `_verify_api_key` calls remain.

# sync_api.py mounts under prefix="/api", so every route is /api/sync/*.
H1_PROTECTED_ENDPOINTS = [
    "/api/sync/postgres",
    "/api/sync/embeddings",
    "/api/sync/initial",
    "/api/sync/obligations",
    "/api/sync/deadlines",
    "/api/sync/change-records",
    "/api/sync/risk-scores",
    "/api/sync/benchmarks",
    "/api/sync/ofac-sdn",
    "/api/sync/sdn-vectors",
    "/api/sync/sdn-changes",
    "/api/sync/sdn-health",
    "/api/sync/enforcement-actions",
    "/api/sync/counts",
]


def _route_declares_dependency(app, path: str, target) -> bool:
    """True if the POST route at `path` has `target` anywhere in its
    FastAPI dependency graph (directly or via nested Depends)."""
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if "POST" not in getattr(route, "methods", set()):
            continue
        stack = [route.dependant]
        while stack:
            d = stack.pop()
            if d is None:
                continue
            if d.call is target:
                return True
            stack.extend(d.dependencies)
    return False


@pytest.mark.parametrize("path", H1_PROTECTED_ENDPOINTS)
async def test_h1_sync_post_declares_require_sync_auth(p1_app, path):
    """Every protected /sync/* POST must declare require_sync_auth Depends."""
    from src.api.sync_auth import require_sync_auth
    assert _route_declares_dependency(p1_app, path, require_sync_auth), (
        f"{path}: route dependency graph does not contain require_sync_auth. "
        "See production-readiness-gaps.md H1."
    )


@pytest.mark.parametrize("path", H1_PROTECTED_ENDPOINTS)
async def test_h1_unauthenticated_returns_401(p1_client, path):
    """Unauthenticated POST (no headers, empty body) must return 401."""
    resp = await p1_client.post(path, json={})
    assert resp.status_code == 401, (
        f"{path} returned {resp.status_code}; expected 401."
    )


# ─────────────────────────────────────────────────────────────────────
# H2 — /sync/status must require auth
# ─────────────────────────────────────────────────────────────────────
#
# Source: production-readiness-gaps.md H2. /sync/status takes a
# SyncTelemetry body, writes to SyncMetadata, and returns
# {"status": "received"} — without any auth check. Leaks (and lets a
# caller poison) sync telemetry for any customer_id they know.

async def test_h2_sync_status_rejects_unauthenticated(p1_client):
    """POST /sync/status without auth headers must return 401."""
    payload = {
        "customer_id": "testbank-demo",
        "sync_version": 1,
        "sync_status": "SUCCESS",
        "rules_added": 0,
        "rules_updated": 0,
        "rules_deleted": 0,
    }
    resp = await p1_client.post("/api/sync/status", json=payload)
    assert resp.status_code == 401


async def test_h2_sync_status_accepts_valid_hmac(p1_client, hmac_headers):
    """Valid HMAC headers must keep working after H2 closure."""
    payload = {
        "customer_id": "testbank-demo",
        "sync_version": 1,
        "sync_status": "SUCCESS",
        "rules_added": 0,
        "rules_updated": 0,
        "rules_deleted": 0,
    }
    resp = await p1_client.post("/api/sync/status", json=payload, headers=hmac_headers)
    assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────
# H14 — /health must probe DB / Redis / Bedrock / S3
# ─────────────────────────────────────────────────────────────────────
#
# Source: production-readiness-gaps.md H14. Today /health returns a flat
# {"status": "healthy"} without touching any dependency. ECS liveness
# probes pass while the DB or Bedrock is down, so a degraded stack stays
# in service — no auto-recovery, no alarm.
#
# Acceptance:
#   1. /health exposes a `checks` dict with at minimum db/redis/bedrock/s3
#   2. each dep status is one of {"ok", "degraded", "down"}
#   3. /health returns HTTP 503 when any critical dep is down

EXPECTED_HEALTH_DEPS = {"db", "redis", "bedrock", "s3"}


async def test_h14_health_exposes_per_dep_checks(p1_client):
    """/health must return a `checks` dict with the four critical deps."""
    resp = await p1_client.get("/health/")
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body, (
        f"/health response keys: {list(body)}. /health must expose "
        "per-dep statuses under `checks` (H14)."
    )
    missing = EXPECTED_HEALTH_DEPS - set(body["checks"])
    assert not missing, f"/health is missing dep keys: {missing}"
    for dep, dep_status in body["checks"].items():
        assert dep_status in {"ok", "degraded", "down"}, (
            f"{dep} reports unknown status {dep_status!r}"
        )


async def test_h14_health_returns_503_when_db_down(p1_client, monkeypatch):
    """When the DB is unreachable, /health must return 503, not 200.

    Patch target is the binding inside `src.api.health` (where get_db
    is consumed) rather than `src.services.database` (the source
    module) — patching the source module won't rebind the
    already-imported name in the consumer.
    """
    from src.api import health as health_module

    class _BrokenDB:
        async def __aenter__(self): raise RuntimeError("simulated DB outage")
        async def __aexit__(self, *_): return False

    monkeypatch.setattr(health_module, "get_db", lambda: _BrokenDB())

    resp = await p1_client.get("/health/")
    assert resp.status_code == 503, (
        f"/health returned {resp.status_code} with DB down; expected 503."
    )
    body = resp.json()
    assert body["checks"]["db"] == "down"
    assert body["status"] == "unhealthy"
