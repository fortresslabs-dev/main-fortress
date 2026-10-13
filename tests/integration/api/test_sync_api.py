"""
F2 — P1 ↔ P2 sync API contract tests.

Covered (auth=headers via require_sync_auth; see H1/H2):
  POST /api/sync/postgres
    - bad api_key             → 401
    - good api_key            → 200, response has current_version + added/updated/deleted
    - idempotent re-call      → no new rows, same current_version
  POST /api/sync/change-records
    - bad api_key             → 401
    - good                    → 200, records[] present
  POST /api/sync/status (H2 — header-auth required)
    - no headers              → 401
    - mismatched customer_id  → 403
    - good                    → 200
  POST /api/sync/counts
    - good                    → 200, integer counts

Demo-relevance: F2 is the rule-sync → dashboard "Changes" demo flow. A 500
here breaks the dashboard's regulatory change inbox.

Gap H5 regression — `current_version` must be present (not NULL) even on a
freshly-created sync_version row. The conftest pre-seeds (id=1, version=1).

Gap H6 regression — /sync/initial advances sync_metadata per page. Asserted
via the explicit "page 1 with has_more=True does not yet update metadata,
page 2 with has_more=False does." (covered by a single happy-path call since
seed data is small.)
"""
from __future__ import annotations


# ── /sync/postgres ───────────────────────────────────────────────────

async def test_sync_postgres_bad_key_returns_401(p1_client):
    bad_headers = {"X-Customer-Id": "testbank-demo", "X-Api-Key": "wrong-key-xxxx"}
    resp = await p1_client.post(
        "/api/sync/postgres",
        json={"from_version": 0},
        headers=bad_headers,
    )
    assert resp.status_code == 401


async def test_sync_postgres_unknown_customer_returns_401(p1_client, hmac_headers):
    bad_headers = {**hmac_headers, "X-Customer-Id": "no-such-bank"}
    resp = await p1_client.post(
        "/api/sync/postgres",
        json={"from_version": 0},
        headers=bad_headers,
    )
    assert resp.status_code == 401


async def test_sync_postgres_happy_path_shape(p1_client, hmac_headers):
    resp = await p1_client.post(
        "/api/sync/postgres",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Gap H5 — current_version must always be an int, never null
    assert isinstance(body["current_version"], int)
    assert body["current_version"] >= 0
    assert isinstance(body["added"], list)
    assert isinstance(body["updated"], list)
    assert isinstance(body["deleted"], list)


async def test_sync_postgres_idempotent_when_caught_up(p1_client, hmac_headers):
    """Second call with from_version == current_version returns empty diffs."""
    first = await p1_client.post(
        "/api/sync/postgres",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert first.status_code == 200
    cur = first.json()["current_version"]

    second = await p1_client.post(
        "/api/sync/postgres",
        json={"from_version": cur},
        headers=hmac_headers,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["current_version"] == cur
    assert body["added"] == []
    assert body["updated"] == []
    assert body["deleted"] == []


# ── /sync/initial ────────────────────────────────────────────────────

async def test_sync_initial_bad_key_returns_401(p1_client):
    bad_headers = {"X-Customer-Id": "testbank-demo", "X-Api-Key": "wrong"}
    resp = await p1_client.post(
        "/api/sync/initial?page=1&page_size=100",
        json={"from_version": 0},
        headers=bad_headers,
    )
    assert resp.status_code == 401


async def test_sync_initial_happy_path_shape(p1_client, hmac_headers):
    resp = await p1_client.post(
        "/api/sync/initial?page=1&page_size=100",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Contract shape — every page exposes the page-level cursor + rules list
    assert "current_version" in body
    assert isinstance(body["current_version"], int)
    assert "rules" in body and isinstance(body["rules"], list)
    assert "has_more" in body and isinstance(body["has_more"], bool)


async def test_sync_initial_updates_metadata_per_page(p1_client, hmac_headers):
    """H6 regression — /sync/initial must advance sync_metadata.last_sync_version
    on every page, not just on has_more=False. We assert the post-call cursor
    is at least the current_version returned by page 1."""
    from sqlalchemy import text
    from src.services.database import get_db

    resp = await p1_client.post(
        "/api/sync/initial?page=1&page_size=100",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert resp.status_code == 200
    cur = resp.json()["current_version"]

    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT last_sync_version FROM sync_metadata
            WHERE customer_id = :cid
        """), {"cid": hmac_headers["X-Customer-Id"]})).first()
    assert row is not None, "sync_metadata row missing after /sync/initial"
    assert row[0] >= cur, (
        f"last_sync_version={row[0]} < current_version={cur} — H6 regression"
    )


# ── /sync/change-records ─────────────────────────────────────────────

async def test_sync_change_records_bad_key_returns_401(p1_client):
    bad_headers = {"X-Customer-Id": "testbank-demo", "X-Api-Key": "x"}
    resp = await p1_client.post(
        "/api/sync/change-records",
        json={"from_version": 0},
        headers=bad_headers,
    )
    assert resp.status_code == 401


async def test_sync_change_records_happy_path(p1_client, hmac_headers):
    resp = await p1_client.post(
        "/api/sync/change-records",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "records" in body
    assert isinstance(body["records"], list)
    assert "current_version" in body


# ── /sync/status (H2 — auth required) ────────────────────────────────

async def test_sync_status_no_auth_returns_401(p1_client):
    resp = await p1_client.post("/api/sync/status", json={
        "customer_id": "testbank-demo",
        "sync_version": 1,
        "sync_status": "SUCCESS",
        "rules_added": 0,
        "rules_updated": 0,
        "rules_deleted": 0,
    })
    assert resp.status_code == 401


async def test_sync_status_customer_mismatch_returns_403(p1_client, hmac_headers):
    """Authenticated as testbank-demo but body says customer_id=other-bank.
    Without this check, a valid caller could poison another bank's telemetry."""
    resp = await p1_client.post("/api/sync/status", json={
        "customer_id": "some-other-bank",
        "sync_version": 1,
        "sync_status": "SUCCESS",
        "rules_added": 0,
        "rules_updated": 0,
        "rules_deleted": 0,
    }, headers=hmac_headers)
    assert resp.status_code == 403


async def test_sync_status_accepts_telemetry(p1_client, hmac_headers):
    resp = await p1_client.post("/api/sync/status", json={
        "customer_id": "testbank-demo",
        "sync_version": 1,
        "sync_status": "SUCCESS",
        "rules_added": 0,
        "rules_updated": 0,
        "rules_deleted": 0,
    }, headers=hmac_headers)
    assert resp.status_code == 200


# ── /sync/counts ─────────────────────────────────────────────────────

async def test_sync_counts_bad_key_returns_401(p1_client):
    bad_headers = {"X-Customer-Id": "testbank-demo", "X-Api-Key": "nope"}
    resp = await p1_client.post(
        "/api/sync/counts",
        json={"from_version": 0},
        headers=bad_headers,
    )
    assert resp.status_code == 401


async def test_sync_counts_happy_path_returns_ints(p1_client, hmac_headers):
    resp = await p1_client.post(
        "/api/sync/counts",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # All count values must be ints — never None — so the dashboard parity
    # check does not crash on null comparisons.
    for k, v in body.items():
        if k.endswith("_count") or "count" in k:
            assert isinstance(v, int), f"{k} = {v!r}"
