"""Row-level CSV writers per design doc §4.

Each writer takes the same snapshot dict the PDF render gets and emits
a CSV with the exact column names called out in §4. Reusing the
snapshot guarantees the PDF and CSV exports are derived from the same
canonical data.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable, Mapping


def _write(rows: Iterable[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: (r.get(c) if r.get(c) is not None else "") for c in columns})
    return buf.getvalue().encode("utf-8")


def csv_for(report_type: str, snapshot: Mapping) -> bytes:
    fn = _WRITERS.get(report_type)
    if fn is None:
        raise ValueError(f"no CSV writer for report_type={report_type!r}")
    return fn(snapshot)


def _examiner_package(s: Mapping) -> bytes:
    cols = ["control_id", "name", "owner_role", "owner_name",
            "test_id", "threshold_value", "threshold_unit",
            "threshold_rationale", "jira_story_key", "github_pr_url",
            "commit_hash"]
    return _write(s.get("controls", []), cols)


def _ofac_attestation(s: Mapping) -> bytes:
    cols = ["list_name", "version_id", "published_at", "ingested_at",
            "sha256_of_xml"]
    rows = [s["list_version"]] if s.get("list_version") else []
    return _write(rows, cols)


def _board_report(s: Mapping) -> bytes:
    cols = ["regulator", "action_date", "action_type",
            "summary_pretty", "penalty_amount_usd", "peer_signal_score"]
    return _write(s.get("enforcement", []), cols)


def _mra_tracker(s: Mapping) -> bytes:
    cols = ["finding_id", "type", "severity", "regulatory_source",
            "concern", "cause", "consequence", "corrective_action",
            "commitment", "status", "identified_date", "due_date",
            "repeat_flag"]
    return _write(s.get("findings", []), cols)


def _change_log(s: Mapping) -> bytes:
    cols = ["change_ref", "regulator", "change_summary", "disposition",
            "owner_name", "impl_link", "status", "dispositioned_at",
            "vocab_version"]
    return _write(s.get("change_log", []), cols)


def _coverage(s: Mapping) -> bytes:
    return _write([{
        "total_mapped_rules": s.get("total_mapped_rules", 0),
        "tested_rules": s.get("tested_rules", 0),
        "coverage_pct": s.get("coverage_pct", 0.0),
    }], ["total_mapped_rules", "tested_rules", "coverage_pct"])


_WRITERS = {
    "examiner_package": _examiner_package,
    "ofac_attestation": _ofac_attestation,
    "board_report": _board_report,
    "mra_tracker": _mra_tracker,
    "change_log": _change_log,
    "coverage": _coverage,
}
