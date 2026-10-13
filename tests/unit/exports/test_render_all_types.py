"""Per-type render smoke tests for the 6 export report types.

Covers two regressions:
  * an export tile producing an empty PDF (HTML body was empty because
    a snapshot key was renamed and the template silently skipped it)
  * a CSV writer returning the header row but no body when the snapshot
    list was nested under the wrong key

For each report type:
  * csv_writer.csv_for(...) returns non-empty bytes with the documented
    header row AND at least one body row.
  * renderer.render_html(...) returns HTML containing the bank legal
    name, the document_id, and at least one value pulled from the body
    section (so the template isn't dropping the snapshot).

PDF byte rendering needs Playwright/Chromium (only present in the
exports-worker image) — the worker-side check belongs in the
integration suite. Here we exercise the HTML path that the PDF render
wraps, which is what would have caught the empty-PDF regression.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io

import pytest

from src.exports import csv_writer
from src.exports.renderer import render_html
from src.exports.signoff import required_signers


_AS_OF = _dt.date(2026, 6, 22)


def _cover() -> dict:
    return {
        "bank_id": "testbank",
        "legal_name": "Testbank, N.A.",
        "charter_number": "12345",
        "primary_regulator": "OCC",
        "secondary_regulators": ["FDIC"],
        "total_assets_usd": 1_250_000_000,
        "total_assets_as_of": _AS_OF.isoformat(),
        "fiscal_year_end": _dt.date(2026, 12, 31),
        "address": "1 Main St",
        "as_of": _AS_OF.isoformat(),
    }


def _ctx(report_type: str, snapshot: dict) -> dict:
    return {
        **snapshot,
        "report_type": report_type,
        "version_hash": "abc1234",
        "vocab_version": "v1.2026.06",
        "template_version": "v1.2026.06",
        "document_id": f"FTX-XXX-2026-0622-abc123",
        "required_signers": list(required_signers(report_type)),
        "is_draft": True,
    }


# ── Fixture snapshots — one per report type ──────────────────────────


def _examiner_package_snap() -> dict:
    # §11.3 hard gate — one blocking row (LAW + verified) and one
    # advisory row (INTERNAL_CONTROL) so the render smoke covers both
    # rails. The dashboard/PDF regression this test guards is exactly
    # a template dropping one of these lists.
    blocking = {
        "control_id": "C-001", "name": "Wire dual approval",
        "owner_role": "bsa_officer", "owner_name": "J. Doe",
        "test_id": "T-001", "threshold_value": 10000,
        "threshold_unit": "USD",
        "threshold_rationale": "31 C.F.R. § 1010.410(f)",
        "jira_story_key": "MDP-42",
        "github_pr_url": "https://github.com/example/p2/pull/7",
        "commit_hash": "deadbeef1234",
        "authority_type": "LAW", "citation_verified": True,
        "cfr_citation": "31 C.F.R. § 1010.410(f)",
        "authority_source": None,
        "citation": "31 C.F.R. § 1010.410(f)",
        "source": None,
    }
    advisory = {
        "control_id": "C-002", "name": "Wire two-signer policy",
        "owner_role": "bsa_officer", "owner_name": "J. Doe",
        "test_id": "T-002", "threshold_value": 10000,
        "threshold_unit": "USD",
        "threshold_rationale": "OCC Handbook — Wire Transfer",
        "jira_story_key": "MDP-43",
        "github_pr_url": None, "commit_hash": None,
        "authority_type": "INTERNAL_CONTROL", "citation_verified": False,
        "cfr_citation": None,
        "authority_source": "OCC Comptroller's Handbook — Wire Transfer",
        "citation": None,
        "source": "OCC Comptroller's Handbook — Wire Transfer",
    }
    return {
        "cover": _cover(),
        "controls": [blocking, advisory],
        "controls_blocking": [blocking],
        "controls_advisory": [advisory],
        "execution": [{
            "test_id": "T-001",
            "executed_at": _dt.datetime(2026, 6, 21, 13, 0,
                                        tzinfo=_dt.timezone.utc),
            "result": "PASS",
            "evidence_s3_uri": "s3://fortress-p2/evidence/T-001.json",
        }],
    }


def _ofac_attestation_snap() -> dict:
    return {
        "cover": _cover(),
        "list_version": {
            "list_name": "sdn", "version_id": "2026-06-22-001",
            "published_at": _dt.datetime(2026, 6, 22, 6, 0,
                                         tzinfo=_dt.timezone.utc),
            "ingested_at": _dt.datetime(2026, 6, 22, 7, 0,
                                        tzinfo=_dt.timezone.utc),
            "sha256_of_xml": "cafef00d" * 8,
        },
        "validation": None,
    }


def _board_report_snap() -> dict:
    return {
        "cover": _cover(),
        "quarter": "FQ2-FY2027",
        "enforcement": [{
            "regulator": "OCC",
            "action_date": _dt.date(2026, 6, 1),
            "action_type": "Consent Order",
            "summary_pretty": "AML program deficiencies at Peer Bank.",
            "penalty_amount_usd": 5_000_000,
            "peer_signal_score": 0.82,
        }],
    }


def _mra_tracker_snap() -> dict:
    return {
        "cover": _cover(),
        "findings": [{
            "finding_id": "F-001", "type": "MRA", "severity": "high",
            "regulatory_source": "OCC", "concern": "BSA program gaps",
            "cause": "Insufficient staffing", "consequence": "Late filings",
            "corrective_action": "Hire 2 analysts",
            "commitment": "Complete by Q4",
            "status": "open",
            "identified_date": _dt.date(2026, 3, 1),
            "due_date": _dt.date(2026, 12, 31),
            "repeat_flag": False,
            "repeat_of_finding_id": None,
        }],
    }


def _change_log_snap() -> dict:
    return {
        "cover": _cover(),
        "change_log": [{
            "change_ref": "CHG-2026-0412",
            "regulator": "FinCEN",
            "change_summary": "Beneficial ownership threshold 25% → 20%.",
            "disposition": "acknowledged",
            "owner_name": "C. Smith",
            "impl_link": "https://github.com/example/p2/pull/99",
            "status": "completed",
            "dispositioned_at": _dt.datetime(2026, 4, 12, 0, 0,
                                             tzinfo=_dt.timezone.utc),
            "vocab_version": "v1.2026.06",
        }],
    }


def _coverage_snap() -> dict:
    return {
        "cover": _cover(),
        "total_mapped_rules": 128,
        "tested_rules": 119,
        "coverage_pct": 93.0,
    }


# (report_type, template_filename, snapshot_factory, body_value_in_html)
_CASES = [
    ("examiner_package", "examiner_package.html",
     _examiner_package_snap, "Wire dual approval"),
    ("ofac_attestation", "ofac_attestation.html",
     _ofac_attestation_snap, "2026-06-22-001"),
    ("board_report", "board_report.html",
     _board_report_snap, "Consent Order"),
    ("mra_tracker", "mra_tracker.html",
     _mra_tracker_snap, "BSA program gaps"),
    ("change_log", "change_log.html",
     _change_log_snap, "CHG-2026-0412"),
    ("coverage", "coverage.html",
     _coverage_snap, "93.0"),
]


@pytest.mark.parametrize("report_type,template,snap_fn,body_value", _CASES)
def test_html_render_includes_cover_and_body(report_type, template,
                                             snap_fn, body_value):
    html = render_html(template, _ctx(report_type, snap_fn()))
    assert "Testbank, N.A." in html, "cover header missing"
    assert "FTX-XXX-2026-0622-abc123" in html, "document_id missing"
    assert body_value in html, (
        f"{report_type} body row missing — empty PDF regression. "
        f"Expected '{body_value}' in rendered HTML."
    )


@pytest.mark.parametrize("report_type,_t,snap_fn,_b", _CASES)
def test_csv_writer_emits_header_and_at_least_one_row(report_type, _t,
                                                     snap_fn, _b):
    body = csv_writer.csv_for(report_type, snap_fn())
    assert isinstance(body, bytes) and len(body) > 0
    rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
    assert len(rows) >= 2, (
        f"{report_type} CSV has no data rows — only a header. "
        f"Snapshot key wiring is wrong."
    )
    header = rows[0]
    assert len(header) > 1, f"{report_type} CSV header is suspiciously thin"


def test_every_report_type_has_a_csv_writer():
    expected = {"examiner_package", "ofac_attestation", "board_report",
                "mra_tracker", "change_log", "coverage"}
    assert set(csv_writer._WRITERS) == expected
