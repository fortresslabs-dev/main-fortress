"""
B8 — failure-injection regression locks for P1 sync endpoints.

Before B8, an unhandled exception from a sync handler returned the
default FastAPI 500 body (`Internal Server Error`, text). P2's
circuit breaker keys on a JSON envelope with `code` field, so the
text response was treated as a parse error and the bank would
retry-spin instead of backing off. These tests pin:

  1. Auth check still runs first — DB down on an unauthenticated
     request returns 401 (auth was rejected), not 503/500.
  2. Authenticated handlers that hit a downed DB return a structured
     500 envelope with `code` set (so P2 can branch on it).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def broken_db(monkeypatch):
    """Patch the get_db symbol inside sync_api so a DB call raises immediately."""
    from src.api import sync_api as sync_module

    class _BrokenDB:
        async def __aenter__(self): raise RuntimeError("simulated DB outage (B8)")
        async def __aexit__(self, *_): return False

    monkeypatch.setattr(sync_module, "get_db", lambda: _BrokenDB())


@pytest_asyncio.fixture
async def p1_client_nonraising(p1_app):
    """P1 client that does NOT re-raise app exceptions — needed so we can
    inspect the structured 500 envelope the exception_handler produces.
    The default p1_client fixture uses ASGITransport(raise_app_exceptions=True)
    which re-raises after the handler runs, masking the response body."""
    transport = ASGITransport(app=p1_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─────────────────────────────────────────────────────────────────────
# Auth runs before any DB call, so unauth + DB-down = 401 not 5xx.
# ─────────────────────────────────────────────────────────────────────

async def test_b8_unauthenticated_with_db_down_returns_401(broken_db, p1_client_nonraising):
    """A caller with no headers must get 401 even if the DB is down — the
    require_sync_auth dep rejects before any handler logic runs."""
    resp = await p1_client_nonraising.post("/api/sync/postgres", json={"from_version": 0})
    assert resp.status_code == 401, (
        f"DB-down + no-auth returned {resp.status_code}. The auth dep must "
        "short-circuit before reaching the DB session."
    )


# ─────────────────────────────────────────────────────────────────────
# Authenticated + DB down → structured 500 envelope.
# ─────────────────────────────────────────────────────────────────────

_SYNC_BODY_ENDPOINTS = [
    "/api/sync/postgres",
    "/api/sync/embeddings",
    "/api/sync/obligations",
    "/api/sync/deadlines",
    "/api/sync/risk-scores",
    "/api/sync/benchmarks",
    "/api/sync/counts",
]


@pytest.mark.parametrize("path", _SYNC_BODY_ENDPOINTS)
async def test_b8_authenticated_handler_returns_structured_500_on_db_failure(
    broken_db, p1_client_nonraising, hmac_headers, path
):
    """Each protected handler must surface DB outage as a JSON envelope
    with `code: INTERNAL_ERROR` so P2 circuit breakers can branch on it."""
    resp = await p1_client_nonraising.post(path, json={"from_version": 0}, headers=hmac_headers)
    assert resp.status_code == 500, f"{path} returned {resp.status_code}"
    body = resp.json()
    assert body.get("code") == "INTERNAL_ERROR", (
        f"{path}: response body missing structured `code` field — P2 "
        f"circuit breaker cannot key on it. Body: {body!r}"
    )
    assert body.get("path") == path
