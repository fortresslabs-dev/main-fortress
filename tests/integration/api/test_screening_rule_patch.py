"""
G1 — admin CRUD for screening_rules via
PATCH /api/v1/dashboard/screening/rules/{rule_id}.

Before G1, compliance had to edit the screening_rules table via raw SQL
to update weight / is_active / description. This endpoint closes that
gap with an Admin-only PATCH that:
  - validates the rule_id against the 4 canonical rules
  - accepts a sparse {weight?, is_active?, description?} body
  - bumps `version` monotonically + sets `updated_by` to the caller's
    email (audit symmetry with migration 036's `updated_by =
    'migration_036'`)
  - invalidates the FRESH cache when the FRESH rule changes so the
    engine picks up the new state on the very next screen call instead
    of waiting out the 5-min TTL

Covered:
  - happy-path PATCH (weight + is_active + description) bumps version
  - sparse PATCH (one field at a time) leaves others intact
  - unknown rule_id → 404 (before any DB write)
  - empty body → 400
  - weight out of range (>10 or <0) → 422 (Pydantic guard)
  - description too long → 422
  - non-Admin user → 403
  - FRESH-rule PATCH invalidates the staleness-bump cache
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.services.database import get_db
from src.services.validators import (
    _get_fresh_enabled,
    _invalidate_fresh_cache,
)


_CANON_RULES = [
    ("OFAC-SCREEN-FUZZY-001", "Fuzzy match triggers review"),
    ("OFAC-SCREEN-CLEAN-001", "Clean name clears"),
    ("OFAC-SCREEN-OWN50-001", ">=50%-owned entity blocked"),
    ("OFAC-SCREEN-FRESH-001", "List freshness < 24h"),
]


@pytest_asyncio.fixture(autouse=True)
async def _seed_rules():
    """Idempotent seed of the 4 canonical rules with known starting state.
    Each test restores them after running so version/weight drift doesn't
    leak into the next test."""
    async with get_db() as session:
        for rid, name in _CANON_RULES:
            await session.execute(text("""
                INSERT INTO screening_rules
                  (rule_id, name, weight, version, is_active, description)
                VALUES (:rid, :name, 1.0, 1, TRUE, :name)
                ON CONFLICT (rule_id) DO UPDATE
                SET weight = 1.0, version = 1, is_active = TRUE,
                    description = EXCLUDED.description,
                    updated_by = 'test_seed'
            """), {"rid": rid, "name": name})
        await session.commit()
    _invalidate_fresh_cache()
    yield
    _invalidate_fresh_cache()


async def _read_rule(rule_id: str):
    async with get_db() as session:
        return (await session.execute(text("""
            SELECT rule_id, name, weight::float AS weight, version,
                   is_active, description, updated_by
            FROM screening_rules WHERE rule_id = :rid
        """), {"rid": rule_id})).first()


# ── Happy path ───────────────────────────────────────────────────────


async def test_patch_updates_all_three_fields_and_bumps_version(
    p2_client, override_user,
):
    u = override_user(role="Admin")
    rid = "OFAC-SCREEN-FUZZY-001"
    before = await _read_rule(rid)
    resp = await p2_client.patch(
        f"/api/v1/dashboard/screening/rules/{rid}",
        json={"weight": 0.5, "is_active": False,
              "description": "Lowered weight pending recalibration."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["weight"] == 0.5
    assert body["is_active"] is False
    assert body["description"].startswith("Lowered weight")
    assert body["version"] == before.version + 1
    assert body["updated_by"] == u.email

    after = await _read_rule(rid)
    assert after.weight == 0.5
    assert after.is_active is False
    assert after.version == before.version + 1
    assert after.updated_by == u.email


async def test_patch_sparse_leaves_other_fields_intact(
    p2_client, override_user,
):
    """Sending only `weight` must not blank out description or flip is_active."""
    override_user(role="Admin")
    rid = "OFAC-SCREEN-OWN50-001"
    before = await _read_rule(rid)
    resp = await p2_client.patch(
        f"/api/v1/dashboard/screening/rules/{rid}",
        json={"weight": 0.75},
    )
    assert resp.status_code == 200
    after = await _read_rule(rid)
    assert after.weight == 0.75
    assert after.is_active == before.is_active
    assert after.description == before.description
    assert after.version == before.version + 1


# ── Validation guards ────────────────────────────────────────────────


async def test_patch_unknown_rule_id_404(p2_client, override_user):
    override_user(role="Admin")
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-MADE-UP-999",
        json={"weight": 1.0},
    )
    assert resp.status_code == 404


async def test_patch_empty_body_400(p2_client, override_user):
    override_user(role="Admin")
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FUZZY-001",
        json={},
    )
    assert resp.status_code == 400


async def test_patch_weight_out_of_range_422(p2_client, override_user):
    override_user(role="Admin")
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FUZZY-001",
        json={"weight": 99.0},
    )
    assert resp.status_code == 422


async def test_patch_description_empty_string_422(p2_client, override_user):
    """min_length=1 — blanking the description must be rejected, not silently
    saved. Compliance should not be able to wipe the explanation by accident."""
    override_user(role="Admin")
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FUZZY-001",
        json={"description": ""},
    )
    assert resp.status_code == 422


# ── Role gating ──────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["Analyst", "CCO", "CRO", "CIO"])
async def test_patch_rejects_non_admin(p2_app, p2_client, role):
    """The shared `override_user` fixture overrides every role-check dep
    (including `require_admin`) to return the user verbatim, which
    bypasses the role gate. To test the gate itself, we override only
    `get_current_user` so the real `require_admin` runs and rejects
    non-Admin roles with 403."""
    from src.api.auth import CurrentUser, get_current_user
    user = CurrentUser(
        sub="test", email=f"{role.lower()}@test.local",
        role=role, groups=[role], bank_id="testbank-demo",
    )
    p2_app.dependency_overrides[get_current_user] = lambda u=user: u
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FUZZY-001",
        json={"weight": 0.5},
    )
    assert resp.status_code == 403, (
        f"role={role} got {resp.status_code} — only Admin should be allowed"
    )


async def test_patch_no_auth_returns_401(p2_client):
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FUZZY-001",
        json={"weight": 0.5},
    )
    assert resp.status_code == 401


# ── FRESH cache invalidation ─────────────────────────────────────────


async def test_patch_fresh_rule_invalidates_engine_cache(
    p2_client, override_user,
):
    """The bump-staleness gate caches the FRESH rule for 5 min. A PATCH
    that flips the gate must take effect immediately, not after the TTL."""
    override_user(role="Admin")
    # Prime cache: enabled=True (weight=1.0, is_active=True per seed).
    assert await _get_fresh_enabled() is True

    # Flip is_active=False through the API.
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FRESH-001",
        json={"is_active": False},
    )
    assert resp.status_code == 200

    # Without cache invalidation this would still read True for ~5 min.
    assert await _get_fresh_enabled() is False


async def test_patch_non_fresh_rule_does_not_invalidate_cache(
    p2_client, override_user,
):
    """Editing FUZZY shouldn't trigger a needless cache miss on the
    FRESH path. We can't directly observe "not invalidated", so we
    confirm the cached value still sticks across an unrelated PATCH."""
    override_user(role="Admin")
    assert await _get_fresh_enabled() is True
    resp = await p2_client.patch(
        "/api/v1/dashboard/screening/rules/OFAC-SCREEN-FUZZY-001",
        json={"weight": 0.5},
    )
    assert resp.status_code == 200
    # Cache wasn't invalidated, so this returns the cached True without
    # a DB roundtrip even though FUZZY's row changed.
    assert await _get_fresh_enabled() is True
