"""
M9 — duplicate JIRA webhook deliveries for the same story key must NOT
spawn parallel workflows. Pre-fix, the handler did SELECT-then-INSERT
which raced; two concurrent deliveries both INSERTed and the agent
pipeline ran twice. Now there is a partial unique index on
(jira_story_id) WHERE status IN ('pending','running'); the handler
reuses the in-flight row.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import text, select

from src.services.database import get_db
from src.models.database import WorkflowExecution, WorkflowStatus


# Apply M9 partial unique index inline so the test passes without a
# full alembic upgrade. Idempotent — DROP/CREATE so reruns are safe.
async def _ensure_inflight_index() -> None:
    async with get_db() as session:
        await session.execute(text(
            "DROP INDEX IF EXISTS uq_workflow_jira_story_inflight"
        ))
        await session.execute(text(
            "CREATE UNIQUE INDEX uq_workflow_jira_story_inflight "
            "ON workflow_executions (jira_story_id) "
            "WHERE status IN ('pending', 'running')"
        ))
        await session.commit()


@pytest.fixture(autouse=True)
async def _setup_index_and_clean(monkeypatch):
    """Index + cleanup + stub out the background agent pipeline so the
    webhook handler exits sub-second. The dedup logic lives entirely
    in the foreground request path; the background task is irrelevant
    to this test."""
    from src.api import webhooks as webhooks_mod

    async def _noop(*args, **kwargs):
        return None
    monkeypatch.setattr(webhooks_mod, "process_workflow", _noop)

    await _ensure_inflight_index()
    async with get_db() as session:
        await session.execute(text(
            "DELETE FROM workflow_executions WHERE jira_story_id LIKE 'DEDUP-%'"
        ))
        await session.commit()
    yield
    async with get_db() as session:
        await session.execute(text(
            "DELETE FROM workflow_executions WHERE jira_story_id LIKE 'DEDUP-%'"
        ))
        await session.commit()


async def _post_webhook(p1_client, jira_key: str):
    """Hit /webhooks/jira with the DEV_MODE bypass enabled (set in
    conftest via JIRA_WEBHOOK_SECRET + ENVIRONMENT=test). The webhook
    router is mounted on the P2 app, not P1 — but conftest only ships
    p1_client / p2_client. Use p2_client here."""
    os.environ["DEV_MODE"] = "true"
    payload = {
        "issue": {
            "key": jira_key,
            "fields": {
                "summary": "dedup test",
                "description": "Test wire transfer >10k validation",
            },
        },
    }
    return await p1_client.post("/webhooks/jira", json=payload)


async def test_m9_duplicate_webhook_returns_same_workflow_id(p2_client, monkeypatch):
    """Two POSTs with the same jira_key — same workflow_id back, one row in DB."""
    monkeypatch.setenv("DEV_MODE", "true")
    jira_key = f"DEDUP-{uuid.uuid4().hex[:8]}"
    payload = {
        "issue": {
            "key": jira_key,
            "fields": {
                "summary": "dedup",
                "description": "Test wire transfer 10000 OFAC compliance",
            },
        },
    }
    r1 = await p2_client.post("/webhooks/jira", json=payload)
    assert r1.status_code in (200, 202), r1.text
    wf1 = r1.json()["workflow_id"]

    r2 = await p2_client.post("/webhooks/jira", json=payload)
    assert r2.status_code in (200, 202), r2.text
    wf2 = r2.json()["workflow_id"]

    assert wf1 == wf2, (
        f"Duplicate webhook produced wf1={wf1!r} vs wf2={wf2!r}. "
        f"The handler must reuse the in-flight row."
    )

    async with get_db() as session:
        rows = (await session.execute(
            select(WorkflowExecution.id)
            .where(WorkflowExecution.jira_story_id == jira_key)
        )).all()
        assert len(rows) == 1, (
            f"Duplicate webhook produced {len(rows)} rows for {jira_key!r}; expected 1."
        )


async def test_m9_partial_index_only_blocks_inflight(p2_client, monkeypatch):
    """A new webhook for a key whose prior workflow COMPLETED must spawn a fresh row."""
    monkeypatch.setenv("DEV_MODE", "true")
    jira_key = f"DEDUP-{uuid.uuid4().hex[:8]}"

    # Seed a completed row out of band.
    async with get_db() as session:
        await session.execute(text("""
            INSERT INTO workflow_executions
              (id, jira_story_id, jira_story_key, status, started_at,
               context_data, artifacts)
            VALUES
              (:id, :k, :k, 'completed', now(), '{}', '{}')
        """), {"id": str(uuid.uuid4()), "k": jira_key})
        await session.commit()

    payload = {
        "issue": {
            "key": jira_key,
            "fields": {
                "summary": "rerun",
                "description": "Test wire transfer 10000 OFAC compliance",
            },
        },
    }
    resp = await p2_client.post("/webhooks/jira", json=payload)
    assert resp.status_code in (200, 202), resp.text

    async with get_db() as session:
        rows = (await session.execute(
            select(WorkflowExecution.status)
            .where(WorkflowExecution.jira_story_id == jira_key)
        )).all()
        statuses = sorted(r[0].value if hasattr(r[0], "value") else str(r[0]) for r in rows)
        assert statuses == ["completed", "running"], (
            f"Expected [completed, running] after rerun; got {statuses!r}."
        )
