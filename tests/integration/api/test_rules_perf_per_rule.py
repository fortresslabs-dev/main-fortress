"""
D3 — per-rule precision on `GET /api/v1/dashboard/screening/rules-perf`.

Each open screening_case is attributed to one screening_rule via
`matched_rule_id` (migration 035). The endpoint returns a `per_rule`
array so examiners can answer "what is the precision of your fuzzy-
match rule vs your ownership rule?" — the existing `totals` block is
GLOBAL across all rules and remains for backwards compatibility.

Attribution map (set in OFACScreener._write_audit):
  any Violation.rule_id startswith 'OFAC_BLOCK' → OFAC-SCREEN-OWN50-001
  else (fuzzy / trgm / vector name match)       → OFAC-SCREEN-FUZZY-001
  CLEAN / FRESH never open a case in isolation.

Covered:
  - per_rule key exists and is a list
  - per-rule alerts / tp / fp / precision are correct
  - rules with zero attributed cases still appear (CLEAN, FRESH)
  - legacy cases (matched_rule_id IS NULL) count in totals, not per_rule
  - rule with no fp+tp returns precision=null (no div-by-zero)
"""
from __future__ import annotations

import uuid as _uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.services.database import get_db


# ── Isolation ────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _wipe_screening_data():
    """Per-rule precision asserts exact counts; wipe leftover cases from
    other tests in this file or other files. screening_rules is preserved
    (it's seeded by `seed_screening_rules` and stable across tests)."""
    async with get_db() as session:
        await session.execute(text("DELETE FROM screening_case_events"))
        await session.execute(text("DELETE FROM screening_cases"))
        await session.execute(text("DELETE FROM screening_events"))
        await session.commit()
    yield


# ── Seeds ────────────────────────────────────────────────────────────


_RULES = [
    ("OFAC-SCREEN-FUZZY-001", "Fuzzy match triggers review"),
    ("OFAC-SCREEN-CLEAN-001", "Clean name clears"),
    ("OFAC-SCREEN-OWN50-001", ">=50%-owned entity blocked"),
    ("OFAC-SCREEN-FRESH-001", "List freshness < 24h"),
]


@pytest.fixture
async def seed_screening_rules():
    """Upsert the four canonical rules (migration 034 normally seeds these,
    but the test DB uses Base.metadata.create_all so it doesn't run seeds)."""
    async with get_db() as session:
        for rid, name in _RULES:
            await session.execute(text("""
                INSERT INTO screening_rules
                  (rule_id, name, weight, version, is_active)
                VALUES (:rid, :name, 1.0, 1, TRUE)
                ON CONFLICT (rule_id) DO NOTHING
            """), {"rid": rid, "name": name})
        await session.commit()


async def _seed_case(
    *,
    matched_rule_id: str | None,
    reviewer_action: str | None,
    decision_at_open: str = "HOLD",
    engine_mode: str = "legacy",
) -> str:
    """Insert one screening_event + one screening_case attributed to
    matched_rule_id with the given reviewer_action. `engine_mode` lands
    on the opening event so /rules-perf can filter on it (G6)."""
    event_id = _uuid.uuid4()
    case_uuid = _uuid.uuid4()
    case_id = f"CASE-D3-{_uuid.uuid4().hex[:8].upper()}"
    status = "cleared" if reviewer_action else "open"
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
               1, :engine_mode, CAST('{}' AS JSONB),
               1.0, 'dashboard', 'test-user', now())
        """), {
            "id": event_id,
            "sid": f"scr-{_uuid.uuid4().hex[:12]}",
            "chash": _uuid.uuid4().hex,
            "dec": decision_at_open,
            "engine_mode": engine_mode,
        })
        await session.execute(text("""
            INSERT INTO screening_cases
              (id, case_id, external_customer_id, sdn_number, matched_name,
               status, priority, decision_at_open,
               first_event_id, last_event_id, event_count,
               matched_rule_id, reviewer_action,
               created_at, updated_at)
            VALUES
              (:uuid, :cid, 'cust-1', 'SDN-9999', 'Test Party',
               :status, 'high', :dec,
               :ev, :ev, 1,
               :mrule, :raction,
               now(), now())
        """), {
            "uuid": case_uuid,
            "cid": case_id,
            "status": status,
            "dec": decision_at_open,
            "ev": event_id,
            "mrule": matched_rule_id,
            "raction": reviewer_action,
        })
        await session.commit()
    return case_id


@pytest.fixture
async def seed_attributed_cases(seed_screening_rules):
    """Mixed case set:
      FUZZY: 3 TP, 1 FP        → precision = 0.75
      OWN50: 1 TP, 1 FP        → precision = 0.5
      CLEAN: 0 cases           → precision = null
      FRESH: 0 cases           → precision = null
      legacy (NULL): 1 TP, 1 FP → counted in totals, not per_rule
    """
    fuzzy = "OFAC-SCREEN-FUZZY-001"
    own50 = "OFAC-SCREEN-OWN50-001"
    for _ in range(3):
        await _seed_case(matched_rule_id=fuzzy, reviewer_action="confirmed_true_positive")
    await _seed_case(matched_rule_id=fuzzy, reviewer_action="cleared_false_positive")
    await _seed_case(matched_rule_id=own50, reviewer_action="confirmed_true_positive")
    await _seed_case(matched_rule_id=own50, reviewer_action="cleared_false_positive")
    # legacy / unattributed
    await _seed_case(matched_rule_id=None, reviewer_action="confirmed_true_positive")
    await _seed_case(matched_rule_id=None, reviewer_action="cleared_false_positive")


# ── Tests ────────────────────────────────────────────────────────────


async def test_per_rule_shape(p2_client, override_user, seed_screening_rules):
    override_user(role="CCO")
    resp = await p2_client.get("/api/v1/dashboard/screening/rules-perf")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "per_rule" in body
    assert isinstance(body["per_rule"], list)
    rule_ids = {r["rule_id"] for r in body["per_rule"]}
    # All four canonical rules appear (CLEAN/FRESH zero-count rows included).
    assert {rid for rid, _ in _RULES}.issubset(rule_ids), (
        f"Missing rules: {rule_ids}"
    )


async def test_per_rule_precision_math(
    p2_client, override_user, seed_attributed_cases,
):
    override_user(role="CCO")
    resp = await p2_client.get("/api/v1/dashboard/screening/rules-perf")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {r["rule_id"]: r for r in body["per_rule"]}

    fuzzy = by_id["OFAC-SCREEN-FUZZY-001"]
    assert fuzzy["alerts"] == 4
    assert fuzzy["true_positives"] == 3
    assert fuzzy["false_positives"] == 1
    assert fuzzy["precision"] == 0.75

    own50 = by_id["OFAC-SCREEN-OWN50-001"]
    assert own50["alerts"] == 2
    assert own50["true_positives"] == 1
    assert own50["false_positives"] == 1
    assert own50["precision"] == 0.5


async def test_rules_with_no_cases_show_null_precision(
    p2_client, override_user, seed_attributed_cases,
):
    """CLEAN and FRESH never open a case in isolation — they appear in
    per_rule with zero counts and precision=null (not 0)."""
    override_user(role="CCO")
    resp = await p2_client.get("/api/v1/dashboard/screening/rules-perf")
    body = resp.json()
    by_id = {r["rule_id"]: r for r in body["per_rule"]}

    for rid in ("OFAC-SCREEN-CLEAN-001", "OFAC-SCREEN-FRESH-001"):
        row = by_id[rid]
        assert row["alerts"] == 0
        assert row["true_positives"] == 0
        assert row["false_positives"] == 0
        assert row["precision"] is None  # not 0, distinguishing N/A from perfect-miss


async def test_legacy_null_cases_count_in_totals_not_per_rule(
    p2_client, override_user, seed_attributed_cases,
):
    """Cases pre-035 (matched_rule_id IS NULL) must still count in the
    global totals but cannot appear in per_rule (no rule to attribute to)."""
    override_user(role="CCO")
    resp = await p2_client.get("/api/v1/dashboard/screening/rules-perf")
    body = resp.json()

    # Totals: 4 FUZZY + 2 OWN50 + 2 NULL = 8
    assert body["totals"]["alerts"] == 8
    # Per-rule sums: 4 FUZZY + 2 OWN50 + 0 CLEAN + 0 FRESH = 6 (NULL excluded)
    per_rule_alerts = sum(r["alerts"] for r in body["per_rule"])
    assert per_rule_alerts == 6


async def test_rules_perf_requires_approver(p2_client):
    """Smoke: endpoint stays approver-gated (CCO/CRO/Admin)."""
    resp = await p2_client.get("/api/v1/dashboard/screening/rules-perf")
    # 401 (no auth) or 403 (auth without approver) both acceptable; 200 is not.
    assert resp.status_code in (401, 403), resp.text


# ── G6 — engine_mode filter ──────────────────────────────────────────


@pytest.fixture
async def seed_engine_split_cases(seed_screening_rules):
    """Two FUZZY cases under legacy, one FUZZY case under vector. All TP.
    Lets us assert the filter actually changes per-rule counts."""
    fuzzy = "OFAC-SCREEN-FUZZY-001"
    for _ in range(2):
        await _seed_case(
            matched_rule_id=fuzzy,
            reviewer_action="confirmed_true_positive",
            engine_mode="legacy",
        )
    await _seed_case(
        matched_rule_id=fuzzy,
        reviewer_action="confirmed_true_positive",
        engine_mode="vector",
    )


async def test_engine_mode_filter_legacy(
    p2_client, override_user, seed_engine_split_cases,
):
    override_user(role="CCO")
    resp = await p2_client.get(
        "/api/v1/dashboard/screening/rules-perf?engine_mode=legacy"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["engine_mode_filter"] == "legacy"
    assert body["totals"]["alerts"] == 2
    by_id = {r["rule_id"]: r for r in body["per_rule"]}
    assert by_id["OFAC-SCREEN-FUZZY-001"]["alerts"] == 2


async def test_engine_mode_filter_vector(
    p2_client, override_user, seed_engine_split_cases,
):
    override_user(role="CCO")
    resp = await p2_client.get(
        "/api/v1/dashboard/screening/rules-perf?engine_mode=vector"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["engine_mode_filter"] == "vector"
    assert body["totals"]["alerts"] == 1
    by_id = {r["rule_id"]: r for r in body["per_rule"]}
    assert by_id["OFAC-SCREEN-FUZZY-001"]["alerts"] == 1


async def test_engine_mode_filter_omitted_returns_all(
    p2_client, override_user, seed_engine_split_cases,
):
    """No filter → counts both modes; engine_mode_filter is null in payload."""
    override_user(role="CCO")
    resp = await p2_client.get("/api/v1/dashboard/screening/rules-perf")
    body = resp.json()
    assert body["engine_mode_filter"] is None
    assert body["totals"]["alerts"] == 3
    by_id = {r["rule_id"]: r for r in body["per_rule"]}
    assert by_id["OFAC-SCREEN-FUZZY-001"]["alerts"] == 3


async def test_engine_mode_filter_rejects_unknown_value(
    p2_client, override_user, seed_screening_rules,
):
    """Pattern guard on Query — `?engine_mode=bogus` must 422."""
    override_user(role="CCO")
    resp = await p2_client.get(
        "/api/v1/dashboard/screening/rules-perf?engine_mode=bogus"
    )
    assert resp.status_code == 422, resp.text


async def test_engine_mode_filter_preserves_zero_count_rules(
    p2_client, override_user, seed_engine_split_cases,
):
    """LEFT JOIN must keep CLEAN/FRESH/OWN50 with zero counts under a filter."""
    override_user(role="CCO")
    resp = await p2_client.get(
        "/api/v1/dashboard/screening/rules-perf?engine_mode=vector"
    )
    body = resp.json()
    rule_ids = {r["rule_id"] for r in body["per_rule"]}
    # All four canonical rules still appear even though only FUZZY has a case
    # under vector mode.
    assert {rid for rid, _ in _RULES}.issubset(rule_ids)
