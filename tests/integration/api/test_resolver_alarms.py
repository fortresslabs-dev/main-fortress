"""
H7 — alarm path for `_embed_query` BM25 fallback.

Today the resolver silently degrades to ts_rank-only when Bedrock
embed fails. Without an alarm, an AWS-cred outage burns hours of
degraded queries (wrong wire thresholds, wrong OFAC fuzzies) before
ops notices. This test pins:

  1. Forced embed failure → resolver still returns a rule but tagged
     `pgvector_degraded` (caller can show the badge today).
  2. The CW EMF metric line `metric_bm25_fallback` is emitted on
     fallback, so the CW alarm on `BM25FallbackHits >= 1 in 5min`
     fires automatically — no separate metric pipeline needed.

Runs against the real Postgres so the ts_rank path actually executes.
"""
from __future__ import annotations

from typing import List, Tuple

import pytest
import structlog


@pytest.fixture
def captured_logs(monkeypatch) -> List[Tuple[str, dict]]:
    """Capture every structlog event for the duration of the test."""
    events: List[Tuple[str, dict]] = []

    def _capture(_logger, method, event_dict):
        events.append((method, dict(event_dict)))
        return event_dict

    structlog.configure(
        processors=[_capture, structlog.processors.KeyValueRenderer()],
    )
    yield events
    # Restore default processors so other tests aren't polluted.
    from src.utils.logger import setup_logging
    setup_logging()


async def test_h7_embed_failure_emits_bm25_fallback_metric(monkeypatch, captured_logs):
    """When _embed_query returns None, the EMF metric must be emitted
    on the same domain that triggered the fallback."""
    from src.services import compliance_rule_resolver as crr

    resolver = crr.ComplianceRuleResolver()

    async def _broken_embed(_self, _q):
        return None

    monkeypatch.setattr(
        crr.ComplianceRuleResolver, "_embed_query", _broken_embed
    )

    await resolver._from_pgvector("wire_transfer", "dual_approval_threshold")

    metric_lines = [
        ev for _, ev in captured_logs
        if ev.get("event") == "metric_bm25_fallback"
    ]
    assert metric_lines, (
        "metric_bm25_fallback was not emitted on embed failure — "
        "CW alarm on BM25FallbackHits will never fire. See H7."
    )
    metric = metric_lines[0]
    assert metric.get("Domain") == "wire_transfer"
    assert metric.get("BM25FallbackHits") == 1
    # EMF envelope must be present so CW Logs picks it up as a metric.
    aws = metric.get("_aws", {})
    assert "CloudWatchMetrics" in aws
    cwm = aws["CloudWatchMetrics"][0]
    assert cwm["Namespace"] == "Fortress/P2/Resolver"
    assert {"Name": "BM25FallbackHits", "Unit": "Count"} in cwm["Metrics"]


async def test_h7_degraded_warning_logged_on_embed_failure(
    monkeypatch, captured_logs
):
    """The human-readable warning is the secondary alarm hook (CW Insights
    query). Make sure it stays in lockstep with the EMF metric."""
    from src.services import compliance_rule_resolver as crr

    resolver = crr.ComplianceRuleResolver()

    async def _broken_embed(_self, _q):
        return None

    monkeypatch.setattr(
        crr.ComplianceRuleResolver, "_embed_query", _broken_embed
    )

    await resolver._from_pgvector("ofac", "fuzzy_threshold")

    warnings = [
        ev for method, ev in captured_logs
        if method == "warning"
        and ev.get("event") == "pgvector_query_degraded_no_embedding"
    ]
    assert warnings, (
        "Resolver fell back to BM25 without logging the degraded "
        "warning — ops cannot diagnose the outage. See H7."
    )
    assert warnings[0]["domain"] == "ofac"
