"""
Unit tests — dashboard export token + renderer.

The Reports tab's create/download flow lives in `src.api.dashboard_api`. The
helpers exercised here used to be backed by an in-process `_EXPORT_CACHE`
dict; download requests that landed on a different ECS task than create
returned 404. The cache is gone — the export_id is now a base64-encoded
JSON token that any replica can decode and re-render.

These tests pin the behavior that was broken:

  1. Token round-trips faithfully (type, format, as_of, generated_by, ts).
  2. Tampered / prefix-less / non-base64 / unknown-type / bad-format tokens
     raise HTTPException(404) — they MUST NOT raise 500.
  3. _render_pdf_like_html walks list payloads (the previous renderer only
     handled scalar key/values, leaving examiner_package + change_log PDFs
     visually empty).
  4. Empty list payloads (e.g. mra_tracker → findings: []) render an
     explicit "No rows as of …" block instead of nothing.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# Settings load at import time — provide harmless test defaults so importing
# src.api.dashboard_api doesn't crash on a dev box without secrets wired up.
os.environ.setdefault("PIPELINE_MODE", "pipeline2")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("PIPELINE2_API_TOKEN", "test-dev-token")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("JIRA_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("JIRA_AUTH_MODE", "bearer")
os.environ.setdefault("CLIENT_NAME", "testbank-demo")
os.environ.setdefault("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-v1:0")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://fortress:fortress_dev@localhost:5432/fortress_dev",
)
os.environ.setdefault("REDIS_URL", "redis://:fortress_dev@localhost:6379/0")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from src.api.dashboard_api import (  # noqa: E402
    _EXPORT_TOKEN_PREFIX,
    _canonical_export_bytes,
    _decode_export_token,
    _encode_export_token,
    _render_pdf_like_html,
    _short_hash,
)


# ── token round-trip ─────────────────────────────────────────────────

def _fixed_ts() -> datetime:
    return datetime(2026, 6, 18, 14, 30, 0, tzinfo=timezone.utc)


def test_token_roundtrip_preserves_all_fields():
    as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
    generated_at = _fixed_ts()
    token = _encode_export_token(
        "examiner_package", as_of, "pdf", "cco@bank.test", generated_at,
    )
    assert token.startswith(_EXPORT_TOKEN_PREFIX)

    decoded = _decode_export_token(token)
    assert decoded["type"] == "examiner_package"
    assert decoded["format"] == "pdf"
    assert decoded["as_of"] == as_of
    assert decoded["generated_by"] == "cco@bank.test"
    assert decoded["generated_at"] == generated_at


def test_token_is_deterministic_for_same_inputs():
    """Same inputs → identical token; safe to compare or log."""
    as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ts = _fixed_ts()
    t1 = _encode_export_token("change_log", as_of, "csv", "u@x", ts)
    t2 = _encode_export_token("change_log", as_of, "csv", "u@x", ts)
    assert t1 == t2


# ── 404 paths (must NOT 500) ─────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "",                                            # empty
    "not-a-fortress-token",                        # no prefix
    "exp_!!!not-base64!!!",                        # b64 garbage after prefix
    "exp_e30",                                     # decodes to {} — missing all keys
])
def test_decode_rejects_malformed_token(bad):
    with pytest.raises(HTTPException) as ei:
        _decode_export_token(bad)
    assert ei.value.status_code == 404


def test_decode_rejects_unknown_export_type():
    bogus = _encode_export_token(
        "examiner_package",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        "pdf",
        "u@x",
        _fixed_ts(),
    )
    # Flip the type to one not in _EXPORT_TYPES by re-encoding manually.
    import base64
    import json
    payload = {
        "t": "not_a_real_export", "a": "2026-06-01T00:00:00+00:00",
        "f": "pdf", "by": "u@x", "ts": _fixed_ts().isoformat(),
    }
    tampered = _EXPORT_TOKEN_PREFIX + base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode().rstrip("=")
    assert tampered != bogus
    with pytest.raises(HTTPException) as ei:
        _decode_export_token(tampered)
    assert ei.value.status_code == 404


def test_decode_rejects_bad_format():
    import base64
    import json
    payload = {
        "t": "examiner_package", "a": "2026-06-01T00:00:00+00:00",
        "f": "xlsx", "by": "u@x", "ts": _fixed_ts().isoformat(),
    }
    tampered = _EXPORT_TOKEN_PREFIX + base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode().rstrip("=")
    with pytest.raises(HTTPException) as ei:
        _decode_export_token(tampered)
    assert ei.value.status_code == 404


# ── canonical bytes + hash ───────────────────────────────────────────

def test_canonical_bytes_stable_across_dict_key_order():
    """version_hash must match between create and download — payload key order
    can differ between two _gather_export_payload() calls, so canonicalisation
    must sort."""
    as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
    p1 = {"rules": [{"a": 1, "b": 2}], "extra": 9}
    p2 = {"extra": 9, "rules": [{"b": 2, "a": 1}]}
    assert (_short_hash(_canonical_export_bytes("examiner_package", as_of, p1))
            == _short_hash(_canonical_export_bytes("examiner_package", as_of, p2)))


# ── PDF/HTML renderer ────────────────────────────────────────────────

def _meta(payload, export_type="examiner_package", fmt="pdf"):
    return {
        "type":         export_type,
        "as_of":        datetime(2026, 6, 1, tzinfo=timezone.utc),
        "format":       fmt,
        "payload":      payload,
        "version_hash": "ABC1234",
        "generated_by": "cco@bank.test",
        "generated_at": _fixed_ts(),
    }


def test_render_pdf_html_renders_list_payload_as_table():
    """Regression: old renderer iterated scalars only → list payloads (the
    bulk of examiner_package) printed nothing → PDFs looked blank."""
    payload = {
        "rules": [
            {"rule_id": "R-1", "regulator": "FinCEN", "cfr_citation": "31 CFR 1020.220"},
            {"rule_id": "R-2", "regulator": "OCC",    "cfr_citation": "12 CFR 21.21"},
        ],
    }
    html_bytes = _render_pdf_like_html(_meta(payload))
    html = html_bytes.decode()

    assert "<table" in html, "list payload was not rendered as a table"
    assert "<th>rule_id</th>" in html
    assert "R-1" in html and "R-2" in html
    assert "FinCEN" in html and "12 CFR 21.21" in html
    assert "2 rows" in html, "row count not surfaced in heading"
    # Header/footer chrome still present:
    assert "FORTRESS LABS" in html
    assert "v.ABC1234" in html
    assert "cco@bank.test" in html


def test_render_pdf_html_empty_list_emits_no_rows_block():
    """mra_tracker has no backing table → findings: []. Render must show an
    explicit 'No rows as of <date>' line so the PDF can't look silently
    broken."""
    html = _render_pdf_like_html(_meta({"findings": []}, "mra_tracker")).decode()
    assert "Findings" in html, "empty list still needs its label"
    assert "No rows as of 2026-06-01" in html
    assert "<table" not in html, "empty list should not emit a table"


def test_render_pdf_html_escapes_payload_values():
    """HTML chars in payload values must not break out of the document."""
    payload = {
        "rules": [{"rule_id": "<script>alert('x')</script>", "regulator": "OCC"}],
    }
    html = _render_pdf_like_html(_meta(payload)).decode()
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_render_pdf_html_scalar_payload_still_renders():
    """ofac_attestation returns flat key/values — must still produce a body."""
    payload = {"entries": 4321, "variants": 9876, "embedded": 9876,
               "last_refresh": "2026-06-17T03:00:00+00:00"}
    html = _render_pdf_like_html(_meta(payload, "ofac_attestation")).decode()
    assert "4321" in html and "9876" in html
    # scalar rows render as div.row, not table:
    assert "class='row'" in html
