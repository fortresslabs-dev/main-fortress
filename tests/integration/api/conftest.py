"""
Shared fixtures for the demo-readiness API contract suite.

These tests run against the **real** docker-compose Postgres
(`pgvector/pgvector:pg15`) so the schema, sync_version row layout, and
sqlalchemy column types are exercised end-to-end. The full module is
skipped if Postgres is not reachable — run `make up` first.

Env is set BEFORE any src.* imports because Settings reads at import time.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import uuid as _uuid
from typing import AsyncIterator, Callable, Dict

import pytest
import pytest_asyncio

# ── 1. Set env before importing src.* ────────────────────────────────
# pydantic-settings reads env at Settings() construction time, which happens
# at import of src.utils.config. Anything not set here gets the prod default.
os.environ.setdefault("PIPELINE_MODE", "pipeline2")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://fortress:fortress_dev@localhost:5432/fortress_dev",
)
os.environ.setdefault("REDIS_URL", "redis://:fortress_dev@localhost:6379/0")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("PIPELINE2_API_TOKEN", "test-dev-token")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("JIRA_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("JIRA_AUTH_MODE", "bearer")
os.environ.setdefault("DEV_MODE", "false")
os.environ.setdefault("CLIENT_NAME", "testbank-demo")
os.environ.setdefault("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-v1:0")


# ── 2. Skip whole module if Postgres not reachable ───────────────────
def _postgres_reachable(host: str = "localhost", port: int = 5432) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


if not _postgres_reachable():
    pytest.skip(
        "Postgres not reachable on localhost:5432 — run `make up` first",
        allow_module_level=True,
    )


# ── 3. Imports that depend on the env above ──────────────────────────
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.services.database import close_db, get_db, init_db  # noqa: E402


# ── 4. Demo credentials (deterministic so tests are reproducible) ────
SYNC_CUSTOMER_ID = "testbank-demo"
# 64-char hex string — matches client_credential_service's secrets.token_hex(32) format
SYNC_API_KEY = "a" * 64
DEV_BEARER_TOKEN = "test-dev-token"


# ── 5. Event loop + session-scoped DB bootstrap ──────────────────────
@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole session — session-scoped async fixtures need it."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _bootstrap_db() -> AsyncIterator[None]:
    """Initialise schema once, seed the canonical client_credentials + SyncVersion row.

    Uses `init_db()` which calls `Base.metadata.create_all` in non-prod. Idempotent
    against existing schema. The test-bank credential is upserted with a known
    hash so HMAC and body-auth paths both work.
    """
    await init_db()

    key_hash = hashlib.sha256(SYNC_API_KEY.encode()).hexdigest()
    async with get_db() as session:
        # Ensure SyncVersion row exists (gap H5 — first call returns NULL without this).
        # Table is `sync_versions` (plural) per SyncVersion.__tablename__.
        await session.execute(text("""
            INSERT INTO sync_versions (id, current_version)
            VALUES (1, 1)
            ON CONFLICT (id) DO UPDATE SET current_version = sync_versions.current_version
        """))
        # Upsert test-bank credential.
        await session.execute(text("""
            DELETE FROM client_credentials WHERE customer_id = :cid
        """), {"cid": SYNC_CUSTOMER_ID})
        await session.execute(text("""
            INSERT INTO client_credentials
              (id, customer_id, bank_name, api_key_hash, is_active, tier, created_at)
            VALUES
              (:id, :cid, :name, :hash, true, 'standard', now())
        """), {
            "id": str(_uuid.uuid4()),
            "cid": SYNC_CUSTOMER_ID,
            "name": "Demo Test Bank",
            "hash": key_hash,
        })
        await session.commit()

    yield
    await close_db()


# ── 6. Per-test rollback of volatile rows ────────────────────────────
@pytest_asyncio.fixture
async def clean_volatile_tables() -> AsyncIterator[None]:
    """Wipe rows that tests create. Leaves seed data (client_credentials, sync_version)."""
    yield
    async with get_db() as session:
        for tbl in (
            "checkpoint_approvals",
            "workflow_executions",
            "screening_events",
            "regulatory_change_records",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass  # table may not exist in test DB yet
        await session.commit()


# ── 7. App + client fixtures ─────────────────────────────────────────
@pytest_asyncio.fixture
async def p1_app():
    from src.main_pipeline1 import app
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def p2_app():
    from src.main_pipeline2 import app
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def p1_client(p1_app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=p1_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def p2_client(p2_app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=p2_app), base_url="http://test") as ac:
        yield ac


# ── 8. Auth helpers ──────────────────────────────────────────────────
@pytest.fixture
def sync_body_auth() -> Dict[str, str]:
    """Body-auth payload for /sync/* endpoints (P1 sync_api takes auth in body)."""
    return {"customer_id": SYNC_CUSTOMER_ID, "api_key": SYNC_API_KEY}


@pytest.fixture
def hmac_headers() -> Dict[str, str]:
    """Header-auth dict for /screening/* and any endpoint using require_sync_auth."""
    return {"X-Customer-Id": SYNC_CUSTOMER_ID, "X-Api-Key": SYNC_API_KEY}


@pytest.fixture
def bearer_headers() -> Dict[str, str]:
    """Bearer token using the dev fallback path (PIPELINE2_API_TOKEN)."""
    return {"Authorization": f"Bearer {DEV_BEARER_TOKEN}"}


@pytest.fixture
def override_user(p2_app) -> Callable:
    """Inject a CurrentUser into all P2 auth deps. Returns a callable taking role."""
    from src.api.auth import (
        CurrentUser,
        get_current_user,
        require_admin,
        require_analyst,
        require_approver,
        require_cco,
        require_vector_intel,
    )

    def _inject(role: str = "CCO", bank_id: str = SYNC_CUSTOMER_ID) -> CurrentUser:
        u = CurrentUser(
            sub="test-user",
            email=f"{role.lower()}@test.local",
            role=role,
            groups=[role],
            bank_id=bank_id,
        )
        for dep in (
            get_current_user,
            require_cco,
            require_approver,
            require_analyst,
            require_admin,
            require_vector_intel,
        ):
            p2_app.dependency_overrides[dep] = lambda u=u: u
        return u

    return _inject


@pytest.fixture
def override_screening_auth(p2_app) -> Callable:
    """Inject the screening_api dual-auth context. Returns a callable."""
    from src.api.screening_api import require_screening_auth

    def _inject(actor_kind: str = "dashboard", customer_id: str | None = None) -> dict:
        ctx = {
            "actor_kind": actor_kind,
            "actor_user_id": "test-user",
            "customer_id": customer_id,
        }
        p2_app.dependency_overrides[require_screening_auth] = lambda: ctx
        return ctx

    return _inject


# ── 9. Seed helpers (per-test fixtures) ──────────────────────────────
@pytest_asyncio.fixture
async def seed_change_record() -> Dict[str, str]:
    """Insert one regulatory_change_record so /dashboard/changes returns a row.

    Asserts:
      - semantic_diff column exists (commit 27c3bae)
      - change_summary stored as sentence string (commit 5b2096b)
    """
    ref = f"chg-test-{_uuid.uuid4().hex[:8]}"
    async with get_db() as session:
        # The column default for `status` is a triple-quoted literal that
        # fails ck_change_record_status — supply the value explicitly.
        await session.execute(text("""
            INSERT INTO regulatory_change_records
              (change_ref, regulator, rule_id, change_type,
               change_summary, semantic_diff, status, detected_at)
            VALUES
              (:ref, 'OFAC', 'OFAC-SDN-TEST', 'modified',
               'OFAC SDN list updated with 3 new individuals.',
               :diff, 'detected', now())
        """), {
            "ref": ref,
            "diff": '{"added": ["X"], "removed": []}',
        })
        await session.commit()
    return {"change_ref": ref}


@pytest_asyncio.fixture
async def seed_workflow() -> Dict[str, str]:
    """Insert a running workflow for approval tests."""
    wf_id = str(_uuid.uuid4())
    async with get_db() as session:
        await session.execute(text("""
            INSERT INTO workflow_executions
              (id, jira_story_id, jira_story_key, status, started_at, context_data, artifacts)
            VALUES
              (:id, 'DEMO-1', 'DEMO-1', 'running', now(), '{}', '{}')
        """), {"id": wf_id})
        await session.commit()
    return {"workflow_id": wf_id}
