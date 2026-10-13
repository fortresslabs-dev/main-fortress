"""
G3 — Notes-required gate on HOLD→Pass overrides
(`POST /api/v1/dashboard/screening/cases/{case_id}/decision`).

Risk: high for examination readiness. A CCO clearing a HOLD-decision case
with no rationale is exactly what bank examiners flag during audit. This
gate forces a written justification ≥ PASS_HOLD_MIN_NOTES_CHARS chars
before the resolution is accepted.

Covered:
  - HOLD case, Pass with empty notes    → 400
  - HOLD case, Pass with short notes    → 400
  - HOLD case, Pass with ≥50-char notes → 200, case becomes cleared
  - REVIEW case, Pass with no notes     → 200 (gate fires on HOLD only)
  - Non-CCO Pass on HOLD                → 403 (auth wins, before notes check)
  - Unknown case_id                     → 404
"""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import text

from src.api.dashboard_api import PASS_HOLD_MIN_NOTES_CHARS
from src.services.database import get_db


# ── Helpers ──────────────────────────────────────────────────────────


async def _seed_case(decision_at_open: str) -> str:
    """Insert a screening_event + screening_case with the given decision.

    Returns the case_id (string, e.g. 'CASE-XXXX').
    """
    event_id = _uuid.uuid4()
    case_uuid = _uuid.uuid4()
    case_id = f"CASE-G3-{_uuid.uuid4().hex[:8].upper()}"

    async with get_db() as session:
        await session.execute(text("""
            INSERT INTO screening_events
              (id, screening_id, request_kind, party_name,
               criteria, criteria_hash, decision, final_score,
               match_count, engine_mode, engine_response,
               latency_ms, actor_kind, actor_user_id, created_at)
            VALUES
              (:id, :sid, 'sdn', 'Test Party',
               CAST('{"name":"Test Party"}' AS JSONB), :chash, :dec, 0.95,
               1, 'legacy', CAST('{}' AS JSONB),
               1.0, 'dashboard', 'test-user', now())
        """), {
            "id": event_id,
            "sid": f"scr-{_uuid.uuid4().hex[:12]}",
            "chash": _uuid.uuid4().hex,
            "dec": decision_at_open,
        })
        await session.execute(text("""
            INSERT INTO screening_cases
              (id, case_id, external_customer_id, sdn_number, matched_name,
               status, priority, decision_at_open,
               first_event_id, last_event_id, event_count,
               created_at, updated_at)
            VALUES
              (:uuid, :cid, 'cust-1', 'SDN-9999', 'Test Party',
               'open', 'high', :dec,
               :ev, :ev, 1, now(), now())
        """), {
            "uuid": case_uuid,
            "cid": case_id,
            "dec": decision_at_open,
            "ev": event_id,
        })
        await session.commit()
    return case_id


@pytest.fixture
async def hold_case_id():
    return await _seed_case("HOLD")


@pytest.fixture
async def review_case_id():
    return await _seed_case("REVIEW")


def _decision_url(case_id: str) -> str:
    return f"/api/v1/dashboard/screening/cases/{case_id}/decision"


# ── Tests ────────────────────────────────────────────────────────────


async def test_hold_pass_with_empty_notes_is_rejected(
    p2_client, override_user, hold_case_id,
):
    override_user(role="CCO")
    resp = await p2_client.post(
        _decision_url(hold_case_id),
        json={"action": "pass"},
    )
    assert resp.status_code == 400, resp.text
    assert "requires reviewer notes" in resp.text
    assert str(PASS_HOLD_MIN_NOTES_CHARS) in resp.text


async def test_hold_pass_with_short_notes_is_rejected(
    p2_client, override_user, hold_case_id,
):
    override_user(role="CCO")
    short_notes = "ok"
    assert len(short_notes) < PASS_HOLD_MIN_NOTES_CHARS  # guard the guard
    resp = await p2_client.post(
        _decision_url(hold_case_id),
        json={"action": "pass", "notes": short_notes},
    )
    assert resp.status_code == 400, resp.text


async def test_hold_pass_with_sufficient_notes_succeeds(
    p2_client, override_user, hold_case_id,
):
    override_user(role="CCO")
    notes = (
        "Verified secondary KYC docs, customer is a US-domiciled "
        "homonym — false-positive against SDN entry."
    )
    assert len(notes) >= PASS_HOLD_MIN_NOTES_CHARS  # guard the guard
    resp = await p2_client.post(
        _decision_url(hold_case_id),
        json={"action": "pass", "notes": notes},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "cleared"
    assert body["action"] == "pass"


async def test_review_pass_with_no_notes_succeeds(
    p2_client, override_user, review_case_id,
):
    """The gate only fires on HOLD, not REVIEW (per G3 scope)."""
    override_user(role="CCO")
    resp = await p2_client.post(
        _decision_url(review_case_id),
        json={"action": "pass"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cleared"


async def test_non_cco_pass_on_hold_is_403_before_notes_check(
    p2_client, override_user, hold_case_id,
):
    """Auth check must run before the notes gate so a non-CCO sees the
    real reason (403, not a misleading 400)."""
    override_user(role="Analyst")
    resp = await p2_client.post(
        _decision_url(hold_case_id),
        json={"action": "pass"},
    )
    assert resp.status_code == 403, resp.text
    assert "CCO" in resp.text


async def test_unknown_case_returns_404(p2_client, override_user):
    override_user(role="CCO")
    resp = await p2_client.post(
        _decision_url("CASE-DOES-NOT-EXIST"),
        json={"action": "pass", "notes": "x" * 100},
    )
    assert resp.status_code == 404
