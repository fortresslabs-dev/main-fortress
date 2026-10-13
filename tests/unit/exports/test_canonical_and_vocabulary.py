"""Determinism + vocabulary tests for src/exports.

Per design doc §5 — these tests prove the deterministic guarantee that
makes the WORM artifact contract meaningful:

  * canonical.hash_envelope is stable across runs with permuted keys
  * canonical handles Decimal, date, datetime in a stable form
  * version_hash7 is the 7-char prefix of the full sha256
  * vocabulary.validate rejects bad enum values at every locked path
  * vocabulary.validate accepts a fully-good snapshot
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest

from src.exports import canonical, vocabulary


# ── canonical.py ──────────────────────────────────────────────────────


def test_hash_stable_across_key_order():
    a = {"foo": 1, "bar": [3, 2, 1], "nested": {"y": "y", "x": "x"}}
    b = {"nested": {"x": "x", "y": "y"}, "bar": [3, 2, 1], "foo": 1}
    sha_a, v_a = canonical.hash_envelope(
        a, template_version="v1", vocab_version="v1", app_commit="c1",
    )
    sha_b, v_b = canonical.hash_envelope(
        b, template_version="v1", vocab_version="v1", app_commit="c1",
    )
    assert sha_a == sha_b
    assert v_a == v_b
    assert v_a == sha_a[:7]


def test_hash_changes_when_template_changes():
    snap = {"x": 1}
    sha1, _ = canonical.hash_envelope(snap, template_version="v1",
                                      vocab_version="v1", app_commit="c1")
    sha2, _ = canonical.hash_envelope(snap, template_version="v2",
                                      vocab_version="v1", app_commit="c1")
    assert sha1 != sha2


def test_hash_changes_when_vocab_changes():
    snap = {"x": 1}
    sha1, _ = canonical.hash_envelope(snap, template_version="v1",
                                      vocab_version="v1", app_commit="c1")
    sha2, _ = canonical.hash_envelope(snap, template_version="v1",
                                      vocab_version="v2", app_commit="c1")
    assert sha1 != sha2


def test_decimal_canonical_form_2dp():
    snap = {"penalty": Decimal("12345.6")}
    b = canonical.canonical_bytes(snap)
    assert b'"penalty":"12345.60"' in b


def test_datetime_canonical_form_z():
    snap = {"at": _dt.datetime(2026, 6, 18, 13, 14, 15, tzinfo=_dt.timezone.utc)}
    b = canonical.canonical_bytes(snap)
    assert b'"at":"2026-06-18T13:14:15Z"' in b


def test_date_canonical_form_iso():
    snap = {"d": _dt.date(2026, 6, 18)}
    assert b'"d":"2026-06-18"' in canonical.canonical_bytes(snap)


def test_canonical_rejects_unknown_type():
    class Weird:
        pass
    with pytest.raises(TypeError):
        canonical.canonical_bytes({"w": Weird()})


# ── vocabulary.py ─────────────────────────────────────────────────────


def _good_snapshot() -> dict:
    return {
        "execution": [{"test_id": "T1", "result": "PASS"}],
        "findings": [{"finding_id": "F1", "type": "MRA", "status": "open"}],
        "change_log": [{"change_ref": "X", "disposition": "acknowledged"}],
        "signoffs": [{"role": "cco"}, {"role": "internal_audit"}],
        "verdict": "PASS",
    }


def test_validate_accepts_good_snapshot():
    vocabulary.validate(_good_snapshot())


def test_validate_rejects_bad_test_result():
    s = _good_snapshot()
    s["execution"][0]["result"] = "MAYBE"
    with pytest.raises(vocabulary.VocabularyError):
        vocabulary.validate(s)


def test_validate_rejects_bad_finding_status():
    s = _good_snapshot()
    s["findings"][0]["status"] = "BOGUS"
    with pytest.raises(vocabulary.VocabularyError):
        vocabulary.validate(s)


def test_validate_rejects_bad_disposition():
    s = _good_snapshot()
    s["change_log"][0]["disposition"] = "spam"
    with pytest.raises(vocabulary.VocabularyError):
        vocabulary.validate(s)


def test_validate_rejects_bad_role():
    s = _good_snapshot()
    s["signoffs"][0]["role"] = "intern"
    with pytest.raises(vocabulary.VocabularyError):
        vocabulary.validate(s)


def test_validate_tolerates_missing_optional_paths():
    # Only verdict supplied — no findings, no execution. Should still pass.
    vocabulary.validate({"verdict": "REVIEW"})


def test_validate_rejects_bad_verdict():
    with pytest.raises(vocabulary.VocabularyError):
        vocabulary.validate({"verdict": "MAYBE"})
