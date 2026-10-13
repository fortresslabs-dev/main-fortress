"""
Regression — migrations 046/047 authority + provenance fields must survive
the /api/sync/postgres round-trip.

Guards against silent regressions where a future edit drops one of the
authority fields from RuleDiff or _rule_to_diff. Without those fields on P2,
may_assert_as_law() returns false for every synced rule and the entire
codified-law rail collapses to advisory-only. See docs/correction_smb.md §11.

Test seeds one rule with every authority column non-null, bumps
sync_versions.current_version past it, then asserts every field appears in
the sync response with the exact value.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.services.database import get_db


# Fields we expect to round-trip. Value is what we insert; assertion compares
# the response JSON value to the string form we'd expect after JSON encoding.
_AUTHORITY_FIELDS = {
    "authority_type": ("LAW", "LAW"),
    "authority_source": ("12 CFR 1005.31(b)", "12 CFR 1005.31(b)"),
    "citation_verified": (True, True),
    "citation_verified_at": (
        datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc),
        "2026-07-01T12:00:00+00:00",
    ),
    "citation_verified_source": ("ecfr_section", "ecfr_section"),
    "source_content_hash": ("a" * 64, "a" * 64),
    "scraped_at": (
        datetime(2026, 7, 2, 8, 30, 0, tzinfo=timezone.utc),
        "2026-07-02T08:30:00+00:00",
    ),
    "publication_date": (date(2026, 4, 11), "2026-04-11"),
    "comment_deadline": (date(2026, 6, 11), "2026-06-11"),
    "federal_register_doc": ("2026-07890", "2026-07890"),
    "rin": ("3170-AB12", "3170-AB12"),
    "applicability_asset_min": (Decimal("1000000000"), 1000000000.0),
    "applicability_asset_max": (Decimal("10000000000"), 10000000000.0),
    "applicability_charter": (["national", "state_fed"], ["national", "state_fed"]),
    "applicability_activities": (["wire_transfer", "ach"], ["wire_transfer", "ach"]),
    "applicability_states": (["NY", "CA"], ["NY", "CA"]),
    "is_exemption": (False, False),
    "verify_before_display": (True, True),
    "bank_configurable_threshold": (False, False),
}


@pytest_asyncio.fixture
async def seeded_authority_rule():
    """Insert one compliance_rule with all authority columns populated, bump
    sync_versions past it, and clean up after the test."""
    rule_id = f"auth-roundtrip-{_uuid.uuid4().hex[:8]}"

    async with get_db() as session:
        # Capture the current global version so we can bump past it.
        cur = await session.execute(
            text("SELECT current_version FROM sync_versions WHERE id = 1")
        )
        current_version = cur.scalar() or 0
        new_version = current_version + 1

        params = {
            "rule_id": rule_id,
            "version": new_version,
            **{k: v[0] for k, v in _AUTHORITY_FIELDS.items()},
        }

        # JSONB columns need explicit ::jsonb casts when passed as Python lists
        # via SQLAlchemy text() — otherwise the driver stringifies them.
        await session.execute(text("""
            INSERT INTO compliance_rules
              (rule_id, regulator, category, geography, rule_text,
               is_active, version,
               authority_type, authority_source,
               citation_verified, citation_verified_at, citation_verified_source,
               source_content_hash, scraped_at,
               publication_date, comment_deadline,
               federal_register_doc, rin,
               applicability_asset_min, applicability_asset_max,
               applicability_charter, applicability_activities, applicability_states,
               is_exemption, verify_before_display, bank_configurable_threshold,
               created_at, updated_at)
            VALUES
              (:rule_id, 'FINCEN', 'aml', 'usa', 'Round-trip regression seed.',
               true, :version,
               :authority_type, :authority_source,
               :citation_verified, :citation_verified_at, :citation_verified_source,
               :source_content_hash, :scraped_at,
               :publication_date, :comment_deadline,
               :federal_register_doc, :rin,
               :applicability_asset_min, :applicability_asset_max,
               CAST(:applicability_charter AS jsonb),
               CAST(:applicability_activities AS jsonb),
               CAST(:applicability_states AS jsonb),
               :is_exemption, :verify_before_display, :bank_configurable_threshold,
               now(), now())
        """), {
            **params,
            "applicability_charter": '["national", "state_fed"]',
            "applicability_activities": '["wire_transfer", "ach"]',
            "applicability_states": '["NY", "CA"]',
        })

        # Bump global version so /sync/postgres with from_version=0 returns it.
        await session.execute(text("""
            UPDATE sync_versions SET current_version = :v WHERE id = 1
        """), {"v": new_version})
        await session.commit()

    yield {"rule_id": rule_id, "version": new_version}

    async with get_db() as session:
        await session.execute(
            text("DELETE FROM compliance_rules WHERE rule_id = :rid"),
            {"rid": rule_id},
        )
        await session.commit()


async def test_sync_postgres_round_trips_authority_fields(
    p1_client, hmac_headers, seeded_authority_rule
):
    """Every migration-046/047 field on the seeded rule must appear in the
    /api/sync/postgres response with the value we inserted."""
    resp = await p1_client.post(
        "/api/sync/postgres",
        json={"from_version": 0},
        headers=hmac_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    all_rows = body.get("added", []) + body.get("updated", [])
    matches = [r for r in all_rows if r.get("rule_id") == seeded_authority_rule["rule_id"]]
    assert matches, (
        f"seeded rule {seeded_authority_rule['rule_id']!r} not in response — "
        f"got {len(all_rows)} rows"
    )
    row = matches[0]

    missing = [k for k in _AUTHORITY_FIELDS if k not in row]
    assert not missing, (
        f"RuleDiff dropped fields: {missing}. Check src/api/sync_api.py "
        f"RuleDiff model and _rule_to_diff() — did a refactor delete them?"
    )

    mismatches = []
    for field, (_, expected_json) in _AUTHORITY_FIELDS.items():
        actual = row.get(field)
        if actual != expected_json:
            mismatches.append(f"{field}: expected {expected_json!r}, got {actual!r}")
    assert not mismatches, "Value drift:\n  " + "\n  ".join(mismatches)


@pytest.mark.parametrize("field", sorted(_AUTHORITY_FIELDS.keys()))
async def test_rule_diff_model_declares_authority_field(field):
    """Fast unit check — the Pydantic model itself must declare each field.
    Runs even without a live DB so CI catches drops immediately."""
    from src.api.sync_api import RuleDiff
    assert field in RuleDiff.model_fields, (
        f"RuleDiff is missing {field!r}. Anything not on the model is silently "
        f"dropped from the sync response."
    )
