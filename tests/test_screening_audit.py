"""
Unit tests for the screening decision derivation + criteria hashing.

These cover the *deterministic* parts of OFACScreener that don't need a
database. The full audit-write flow (`_write_audit`, case upsert) is
exercised by tests/test_screening_api.py via TestClient with the engine
mocked, and end-to-end by tests/integration/test_validators_e2e.py.
"""
from __future__ import annotations

import pytest

from src.services.validators import (
    MatchDetail,
    OFAC_HOLD_THRESHOLD,
    Violation,
    _criteria_hash,
    _derive_decision,
    _normalise_criteria,
    _normalise_name,
)


# ── _normalise_name + _criteria_hash determinism ────────────────────

def test_normalise_name_strips_accents_and_punct():
    assert _normalise_name("José O'Brien") == "JOSE O BRIEN"
    assert _normalise_name("  Putin,   Vladimir  ") == "PUTIN VLADIMIR"


def test_normalise_criteria_canonicalises_country_and_address():
    c = _normalise_criteria({
        "name": "John Smith",
        "country": "us",
        "address": "  100 Main St   Apt 4 ",
    })
    assert c == {
        "name": "JOHN SMITH",
        "country": "US",
        "address": "100 MAIN ST APT 4",
    }


def test_criteria_hash_stable_across_typographic_noise():
    a = _criteria_hash({"name": "Mohammed Ali", "country": "AE"})
    b = _criteria_hash({"name": "  mohammed  ali  ", "country": "ae"})
    assert a == b, "criteria_hash must be stable across whitespace + case"


def test_criteria_hash_changes_when_dob_added():
    no_dob = _criteria_hash({"name": "John Smith"})
    with_dob = _criteria_hash({"name": "John Smith", "dob": "1980-01-01"})
    assert no_dob != with_dob


# ── _derive_decision ─────────────────────────────────────────────────

def _match(final: float, rule_id: str = "OFAC-SDN-1") -> Violation:
    return Violation(
        regulation="OFAC Sanctions",
        rule_id=rule_id,
        description="",
        match_detail=MatchDetail(scores={"final": final}),
    )


def test_no_matches_passes():
    decision, score = _derive_decision([], stale_hours=1.0)
    assert decision == "PASS"
    assert score == 0.0


def test_match_below_hold_reviews():
    decision, score = _derive_decision([_match(0.86)], stale_hours=1.0)
    assert decision == "REVIEW"
    assert score == pytest.approx(0.86)


def test_match_at_or_above_hold_holds():
    decision, _ = _derive_decision([_match(OFAC_HOLD_THRESHOLD)], stale_hours=0.0)
    assert decision == "HOLD"


def test_block_violation_forces_hold_even_with_low_score():
    decision, _ = _derive_decision(
        [_match(0.50, rule_id="OFAC_BLOCK_SANCTIONS")], stale_hours=0.0
    )
    assert decision == "HOLD"


def test_soft_stale_data_bumps_decision_one_level():
    pass_bumped, _ = _derive_decision([], stale_hours=30)
    review_bumped, _ = _derive_decision([_match(0.86)], stale_hours=30)
    hold_kept, _    = _derive_decision([_match(0.95)], stale_hours=30)
    assert pass_bumped   == "REVIEW"
    assert review_bumped == "HOLD"
    assert hold_kept     == "HOLD"  # ceiling


def test_fresh_data_does_not_bump():
    decision, _ = _derive_decision([_match(0.86)], stale_hours=10)
    assert decision == "REVIEW"


def test_top_match_score_is_max_not_first():
    decision, score = _derive_decision(
        [_match(0.50), _match(0.95), _match(0.88)], stale_hours=0.0
    )
    assert decision == "HOLD"
    assert score == pytest.approx(0.95)
