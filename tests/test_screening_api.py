"""
HTTP tests for /api/v1/screen/sdn — auth gating, response shape,
idempotency surface contract. The OFAC screener and DB layer are
mocked; this is a fast unit-style test, not an integration test.

For end-to-end coverage with a real DB see
tests/integration/test_validators_e2e.py.
"""
from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Set env BEFORE importing the API module (Settings + auth.py read at import).
os.environ.setdefault("PIPELINE2_API_TOKEN", "test-dev-token")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from src.api.screening_api import router as screening_router  # noqa: E402
from src.services.validators import (  # noqa: E402
    MatchDetail,
    ValidationResult,
    Violation,
)


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(screening_router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _result(decision: str, *, with_match: bool = False, score: float = 0.93) -> ValidationResult:
    violations = []
    if with_match:
        violations.append(Violation(
            regulation="OFAC Sanctions",
            rule_id="OFAC-SDN-26410",
            description="Potential SDN match: 'PUTIN, Vladimir Vladimirovich' (score: 0.93)",
            match_detail=MatchDetail(
                sdn_number="26410",
                matched_name="PUTIN, Vladimir Vladimirovich",
                is_primary=True,
                entity_type="individual",
                programs=["RUSSIA-EO14024"],
                country="RUSSIA",
                scores={"trgm": 0.91, "fuzz": 0.94, "vector": 0.88, "final": score},
            ),
        ))
    r = ValidationResult(action="block" if decision == "HOLD" else "clear",
                         violations=violations, latency_ms=12.5)
    r.decision = decision
    r.screening_id = "abc123def456"
    r.final_score = score if with_match else 0.0
    return r


# ── Auth gating ──────────────────────────────────────────────────────

def test_missing_auth_returns_401(client: TestClient):
    r = client.post("/api/v1/screen/sdn", json={"name": "Foo"})
    assert r.status_code == 401


def test_dev_token_bypass_is_allowed(client: TestClient):
    with patch("src.api.screening_api._try_idempotent", new=AsyncMock(return_value=None)), \
         patch("src.api.screening_api.OFACScreener") as Screener:
        Screener.return_value.screen = AsyncMock(return_value=_result("PASS"))
        r = client.post(
            "/api/v1/screen/sdn",
            json={"name": "Jane Doe"},
            headers={"Authorization": "Bearer test-dev-token"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "PASS"
    assert body["matches"] == []


def test_hmac_path_uses_sync_auth(client: TestClient):
    fake_sync = AsyncMock(return_value={"customer_id": "firstnational"})
    with patch("src.api.screening_api.require_sync_auth", new=fake_sync), \
         patch("src.api.screening_api._try_idempotent", new=AsyncMock(return_value=None)), \
         patch("src.api.screening_api.OFACScreener") as Screener:
        Screener.return_value.screen = AsyncMock(return_value=_result("PASS"))
        r = client.post(
            "/api/v1/screen/sdn",
            json={"name": "Jane Doe"},
            headers={
                "X-Api-Key": "deadbeef" * 8,
                "X-Customer-Id": "firstnational",
            },
        )
    assert r.status_code == 200
    fake_sync.assert_awaited_once()


# ── Decision shape ───────────────────────────────────────────────────

@pytest.mark.parametrize("decision,score,with_match", [
    ("PASS",   0.00, False),
    ("REVIEW", 0.87, True),
    ("HOLD",   0.95, True),
])
def test_decision_response_shape(client: TestClient, decision: str,
                                  score: float, with_match: bool):
    with patch("src.api.screening_api._try_idempotent", new=AsyncMock(return_value=None)), \
         patch("src.api.screening_api.OFACScreener") as Screener:
        Screener.return_value.screen = AsyncMock(
            return_value=_result(decision, with_match=with_match, score=score)
        )
        r = client.post(
            "/api/v1/screen/sdn",
            json={"name": "Vladimir Putin", "country": "RU"},
            headers={"Authorization": "Bearer test-dev-token"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == decision
    assert body["screening_id"] == "abc123def456"
    if with_match:
        assert len(body["matches"]) == 1
        m = body["matches"][0]
        assert m["sdn_number"] == "26410"
        assert m["scores"]["final"] == score
        assert "X-Idempotent" not in r.headers
    else:
        assert body["matches"] == []


# ── Idempotency cache replay ────────────────────────────────────────

def test_idempotent_cache_sets_header_and_skips_engine(client: TestClient):
    cached: Dict[str, Any] = {
        "screening_id": "old1",
        "decision":     "REVIEW",
        "final_score":  0.87,
        "matches":      [],
        "screened_at":  "2026-06-06T00:00:00",
        "latency_ms":   8.1,
    }
    screen_mock = AsyncMock()
    with patch("src.api.screening_api._try_idempotent",
               new=AsyncMock(return_value=cached)), \
         patch("src.api.screening_api.OFACScreener") as Screener:
        Screener.return_value.screen = screen_mock
        r = client.post(
            "/api/v1/screen/sdn",
            json={"name": "Vladimir Putin"},
            headers={"Authorization": "Bearer test-dev-token"},
        )
    assert r.status_code == 200
    assert r.headers.get("X-Idempotent") == "true"
    assert r.json()["screening_id"] == "old1"
    screen_mock.assert_not_called()
