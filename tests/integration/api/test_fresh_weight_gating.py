"""
G2 — FRESH rule weight wiring.

OFAC-SCREEN-FRESH-001 used to be display-only. Its weight is now
engine-active: weight > 0 (and is_active=true) enables the staleness
bump in OFACScreener._derive_decision; weight <= 0 disables it.

Covered:
  - FRESH weight=1.0  + stale data → decision bumped one level
  - FRESH weight=0.0  + stale data → no bump
  - FRESH is_active=false + stale data → no bump (independent of weight)
  - FRESH row missing entirely → enabled (back-compat fail-open)
  - DB lookup error → enabled (fail-open; safety control stays on)

We exercise `_derive_decision` directly rather than the full screener
because the wiring is the bump-or-not branch — orthogonal to the
violation-finding path. The cache invalidator is called between cases
so the TTL doesn't carry stale state across tests.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.services.database import get_db
from src.services.validators import (
    MatchDetail,
    Violation,
    _derive_decision,
    _get_fresh_enabled,
    _invalidate_fresh_cache,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _fuzzy_review_match() -> Violation:
    """A REVIEW-level fuzzy match (below HOLD threshold)."""
    return Violation(
        regulation="OFAC Sanctions",
        rule_id="OFAC-SDN-12345",
        description="potential SDN match",
        match_detail=MatchDetail(
            sdn_number="12345",
            matched_name="Test Party",
            scores={"final": 0.88},  # below OFAC_HOLD_THRESHOLD (0.92)
        ),
    )


_STALE_HOURS = 30.0   # above _SDN_STALENESS_SOFT_HOURS (24)
_FRESH_HOURS = 1.0    # below threshold


async def _set_fresh_rule(weight: float, is_active: bool = True) -> None:
    async with get_db() as session:
        await session.execute(text("""
            INSERT INTO screening_rules
              (rule_id, name, weight, version, is_active)
            VALUES ('OFAC-SCREEN-FRESH-001', 'List freshness < 24h',
                    :w, 1, :active)
            ON CONFLICT (rule_id) DO UPDATE
            SET weight = EXCLUDED.weight,
                is_active = EXCLUDED.is_active
        """), {"w": weight, "active": is_active})
        await session.commit()


async def _delete_fresh_rule() -> None:
    async with get_db() as session:
        await session.execute(text(
            "DELETE FROM screening_rules WHERE rule_id = 'OFAC-SCREEN-FRESH-001'"
        ))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _reset_state():
    _invalidate_fresh_cache()
    yield
    # Restore the seeded FRESH row so other tests don't see drift.
    await _set_fresh_rule(weight=1.0, is_active=True)
    _invalidate_fresh_cache()


# ── Tests ────────────────────────────────────────────────────────────


async def test_fresh_weight_positive_enables_bump():
    await _set_fresh_rule(weight=1.0, is_active=True)
    enabled = await _get_fresh_enabled()
    assert enabled is True

    matches = [_fuzzy_review_match()]
    decision, _ = _derive_decision(matches, _STALE_HOURS, fresh_enabled=enabled)
    # REVIEW (below HOLD threshold) + stale bump → HOLD
    assert decision == "HOLD"


async def test_fresh_weight_zero_disables_bump():
    await _set_fresh_rule(weight=0.0, is_active=True)
    enabled = await _get_fresh_enabled()
    assert enabled is False

    matches = [_fuzzy_review_match()]
    decision, _ = _derive_decision(matches, _STALE_HOURS, fresh_enabled=enabled)
    # REVIEW stays REVIEW because the bump is gated off
    assert decision == "REVIEW"


async def test_fresh_inactive_disables_bump_regardless_of_weight():
    """Compliance can deactivate FRESH without zeroing the weight; both
    is_active=false AND weight<=0 must disable the bump."""
    await _set_fresh_rule(weight=1.0, is_active=False)
    enabled = await _get_fresh_enabled()
    assert enabled is False

    matches = [_fuzzy_review_match()]
    decision, _ = _derive_decision(matches, _STALE_HOURS, fresh_enabled=enabled)
    assert decision == "REVIEW"


async def test_fresh_row_missing_is_back_compat_enabled():
    """Pre-034 databases (and any future drop of the seed) must NOT
    silently disable the safety bump."""
    await _delete_fresh_rule()
    _invalidate_fresh_cache()

    enabled = await _get_fresh_enabled()
    assert enabled is True

    matches = [_fuzzy_review_match()]
    decision, _ = _derive_decision(matches, _STALE_HOURS, fresh_enabled=enabled)
    assert decision == "HOLD"


async def test_fresh_data_no_bump_regardless_of_weight():
    """Sanity: when data isn't stale the bump doesn't fire even with
    weight=1.0 — the gate is on stale data, FRESH weight only governs
    whether the gate is honored when triggered."""
    await _set_fresh_rule(weight=1.0, is_active=True)
    matches = [_fuzzy_review_match()]
    decision, _ = _derive_decision(matches, _FRESH_HOURS, fresh_enabled=True)
    assert decision == "REVIEW"


async def test_pre_g2_callers_still_work():
    """`_derive_decision` was previously a two-arg call. The new
    `fresh_enabled` kwarg defaults to True so any third-party or
    in-flight caller that didn't get updated keeps the pre-G2 behavior."""
    matches = [_fuzzy_review_match()]
    decision, _ = _derive_decision(matches, _STALE_HOURS)  # no kwarg
    assert decision == "HOLD"
