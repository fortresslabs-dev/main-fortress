"""
H10 — license-verification fail-closed regression lock.
H13 — GitHub token redaction regression lock.

Both gaps were silent failure modes: H10 let an expired bank keep
pulling fresh compliance data through any reachability blip; H13
let a leaked PAT show up in error logs the moment httpx wrapped a
request object into an exception. These tests pin the new contracts.
"""
from __future__ import annotations

from typing import List, Tuple

import httpx
import pytest
import structlog


# ─────────────────────────────────────────────────────────────────────
# H10 — license verifier returns False on timeout / connection error
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def license_verifier(monkeypatch):
    """Build a minimally-configured UpdateService instance for unit-level use."""
    from src.services.update_service import UpdateService
    svc = UpdateService.__new__(UpdateService)
    svc._fortress_url = "https://api.fortresslabs.dev"
    svc._customer_id = "h10-test"
    svc._api_key = "a" * 64
    return svc


async def test_h10_license_verify_fails_closed_on_timeout(monkeypatch, license_verifier):
    """Timeout to license endpoint → False. Was True before H10."""
    class _TimingOutClient:
        def __init__(self, *_, **__): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(httpx, "AsyncClient", _TimingOutClient)
    # Ensure the override env knob is OFF for the default test.
    monkeypatch.delenv("LICENSE_FAIL_OPEN_OVERRIDE", raising=False)

    result = await license_verifier._verify_license()
    assert result is False, (
        "License verifier still fails open on timeout — H10 regression. "
        "An expired bank can keep syncing while the license endpoint is "
        "unreachable, exactly the scenario we set out to close."
    )


async def test_h10_license_verify_fails_closed_on_connection_error(
    monkeypatch, license_verifier
):
    class _BrokenClient:
        def __init__(self, *_, **__): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("simulated connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _BrokenClient)
    monkeypatch.delenv("LICENSE_FAIL_OPEN_OVERRIDE", raising=False)

    assert await license_verifier._verify_license() is False


async def test_h10_license_verify_respects_override(monkeypatch, license_verifier):
    """Ops escape hatch: env var flips to fail-open for incident response.

    The override is intentionally an env var (not SSM) so it can be set
    via `aws ecs update-service` without a code change, and unsetting it
    is one CLI call.
    """
    class _TimingOutClient:
        def __init__(self, *_, **__): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(httpx, "AsyncClient", _TimingOutClient)
    monkeypatch.setenv("LICENSE_FAIL_OPEN_OVERRIDE", "true")

    assert await license_verifier._verify_license() is True


# ─────────────────────────────────────────────────────────────────────
# H13 — GitHub token never appears in logs or wrapped exception text
# ─────────────────────────────────────────────────────────────────────

_LEAK_TOKEN_FORMS = [
    "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "github_pat_bbbbbbbbbbbbbbbbbbbbbbbbbb",
    "token ccccccccccccccccccccccccccccccccccccccccc",
    "Authorization: token ddddddddddddddddddddddddddd",
    "Authorization=Bearer eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "Bearer fffffffffffffffffffffffffffffffff",
]


@pytest.mark.parametrize("payload", _LEAK_TOKEN_FORMS)
def test_h13_redact_text_strips_token_forms(payload):
    """Pure-function test for the redaction helper."""
    from src.utils.logger import redact_text
    out = redact_text(payload)
    assert "[REDACTED]" in out, f"redact_text did not redact: {payload!r}"
    # The literal secret tail must not survive in the output.
    secret_chars = payload.split()[-1] if " " in payload else payload
    assert secret_chars not in out, f"raw secret survived: {out!r}"


@pytest.fixture
def captured_logs(monkeypatch) -> List[Tuple[str, dict]]:
    """Capture every structlog event for the duration of the test, with the
    redaction processor still active so we test the real pipeline."""
    events: List[Tuple[str, dict]] = []

    def _capture(_logger, method, event_dict):
        events.append((method, dict(event_dict)))
        return event_dict

    from src.utils.logger import _redact_processor
    structlog.configure(
        processors=[_redact_processor, _capture, structlog.processors.KeyValueRenderer()],
    )
    yield events
    from src.utils.logger import setup_logging
    setup_logging()


def test_h13_github_pr_failed_log_redacts_token(captured_logs):
    """GitHubService.create_pr catches Exception and logs error=str(exc).
    When the wrapped exception text echoes the PAT, the log line must
    not leak the token in the final structured event dict."""
    from src.services.github_service import GitHubService

    fake_token = "ghp_secretsecretsecretsecretsecret12"
    svc = GitHubService(token=fake_token, org="acme", repo="repo")

    # The structlog processor in the production logger redacts before
    # rendering. Simulate the same call site here.
    import structlog as sl
    logger = sl.get_logger()
    logger.error(
        "github_pr_failed",
        jira_key="ACME-1",
        error=f"HTTPError(401): token {fake_token} rejected",
    )

    leaks = [
        ev for method, ev in captured_logs
        if method == "error"
        and any(fake_token in str(v) for v in ev.values())
    ]
    assert not leaks, (
        f"H13 regression: GitHub PAT leaked into a log event: {leaks}"
    )
