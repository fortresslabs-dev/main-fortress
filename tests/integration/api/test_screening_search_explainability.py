"""
D5 + G4 — analyst-facing explainability on `POST /api/v1/dashboard/screening/search`.

D5: surface SDN-list freshness so the analyst can tell a REVIEW caused by
    low fuzzy confidence from a REVIEW caused by stale data. The /search
    response carries `sdn_data_age_hours` (float) and `staleness_bumped`
    (bool — True iff the FRESH rule actually fired the bump).

G4: when a screening attaches to an already-open case (because
    `(external_customer_id, sdn_number)` matches), `_write_audit` returns
    the original `case_opened_at` and a `case_was_pre_existing` flag.
    The FE renders a "you're working an existing case" banner so the
    analyst doesn't think they opened a fresh case.

These fields default-safe: PASS with no matches still produces them
(staleness_bumped=False, case_was_pre_existing=False, case_opened_at=None,
sdn_data_age_hours=0.0), so the FE can render unconditionally.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.services.database import get_db


@pytest_asyncio.fixture(autouse=True)
async def _wipe_screening_state():
    """The screener writes screening_events / screening_cases rows; wipe
    them between tests so pre-existing-case behaviour is reproducible."""
    async with get_db() as session:
        await session.execute(text("DELETE FROM screening_case_events"))
        await session.execute(text("DELETE FROM screening_cases"))
        await session.execute(text("DELETE FROM screening_events"))
        await session.commit()
    yield


async def test_search_response_carries_freshness_fields(
    p2_client, override_user,
):
    """Empty-DB PASS still surfaces D5 fields with safe defaults."""
    override_user(role="Analyst")
    resp = await p2_client.post(
        "/api/v1/dashboard/screening/search",
        json={"name": "Random Party Name"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "sdn_data_age_hours" in body
    assert "staleness_bumped" in body
    assert isinstance(body["sdn_data_age_hours"], (int, float))
    assert isinstance(body["staleness_bumped"], bool)
    # PASS with no SDN data → no bump.
    assert body["staleness_bumped"] is False


async def test_search_response_carries_attached_case_fields(
    p2_client, override_user,
):
    """G4 fields appear on every response; defaults are False / null when
    no case was opened (PASS path)."""
    override_user(role="Analyst")
    resp = await p2_client.post(
        "/api/v1/dashboard/screening/search",
        json={"name": "Random Party Name"},
    )
    body = resp.json()
    assert "case_was_pre_existing" in body
    assert "case_opened_at" in body
    assert body["case_was_pre_existing"] is False
    assert body["case_opened_at"] is None
    # PASS → no case opened
    assert body["case_id"] is None
