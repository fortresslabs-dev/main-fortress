"""
M8 — P2 /validate/* endpoints must per-IP rate-limit so a burst can't
exhaust the validator's Redis pool. Sliding-window deque keyed by
request.client.host; HTTPException 429 + Retry-After on breach.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def validation_module(monkeypatch):
    """Reload validation_api with FORTRESS_P2_VALIDATE_RPM=3 so the test
    can exercise breach without flooding."""
    monkeypatch.setenv("FORTRESS_P2_VALIDATE_RPM", "3")
    from src.api import validation_api
    importlib.reload(validation_api)
    validation_api._validate_calls.clear()
    yield validation_api
    monkeypatch.delenv("FORTRESS_P2_VALIDATE_RPM", raising=False)
    importlib.reload(validation_api)


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


class _FakeRequest:
    def __init__(self, host: str):
        self.client = _FakeClient(host)


def test_m8_rate_limit_blocks_after_window_full(validation_module):
    """At limit=3, the 4th call from one IP returns 429."""
    req = _FakeRequest("10.0.0.1")
    for _ in range(3):
        validation_module._rate_limit(req)  # within limit
    with pytest.raises(Exception) as exc_info:
        validation_module._rate_limit(req)
    # FastAPI HTTPException has status_code attr
    assert getattr(exc_info.value, "status_code", None) == 429


def test_m8_rate_limit_includes_retry_after(validation_module):
    req = _FakeRequest("10.0.0.2")
    for _ in range(3):
        validation_module._rate_limit(req)
    with pytest.raises(Exception) as exc_info:
        validation_module._rate_limit(req)
    headers = getattr(exc_info.value, "headers", None) or {}
    assert "Retry-After" in headers, (
        f"429 must include Retry-After so clients back off (got headers={headers!r})."
    )


def test_m8_rate_limit_per_ip_independent(validation_module):
    """Two IPs with their own quotas — one full does not block the other."""
    a = _FakeRequest("10.0.0.10")
    b = _FakeRequest("10.0.0.11")
    for _ in range(3):
        validation_module._rate_limit(a)
    # B is still fresh
    validation_module._rate_limit(b)
    validation_module._rate_limit(b)


def test_m8_rate_limit_disabled_when_rpm_zero(monkeypatch):
    """FORTRESS_P2_VALIDATE_RPM=0 disables the limiter (load-test escape)."""
    monkeypatch.setenv("FORTRESS_P2_VALIDATE_RPM", "0")
    from src.api import validation_api
    importlib.reload(validation_api)
    try:
        req = _FakeRequest("10.0.0.99")
        # 1000 calls should not raise
        for _ in range(1000):
            validation_api._rate_limit(req)
    finally:
        monkeypatch.delenv("FORTRESS_P2_VALIDATE_RPM", raising=False)
        importlib.reload(validation_api)
