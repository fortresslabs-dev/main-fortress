"""Per-type point-in-time snapshot gatherers (design doc §7).

Today every gatherer runs on the live tables — reproducibility for
as_of < today is added in PR #6.1 (Q13 snapshot history). For now an
as_of in the past is best-effort.

Each gatherer returns a plain ``dict`` suitable for both:
  * canonical.hash_envelope() — the deterministic content hash
  * Jinja2 template render at src/exports/templates/<type>.html

Gatherers are intentionally thin and stateless so unit tests don't need
the DB to assert structural shape.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Callable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.authority_gate import may_assert_as_law


# Registry — populated by the @register decorator below.
_GATHERERS: dict[str, Callable] = {}


def register(report_type: str):
    def deco(fn):
        _GATHERERS[report_type] = fn
        return fn
    return deco


async def gather(session: AsyncSession, *, report_type: str,
                 bank_id: str, as_of: _dt.date) -> dict:
    fn = _GATHERERS.get(report_type)
    if fn is None:
        raise ValueError(f"unknown report_type: {report_type}")
    return await fn(session, bank_id=bank_id, as_of=as_of)


# ── Helpers ──────────────────────────────────────────────────────────

async def _bank_cover(session, bank_id: str, as_of: _dt.date) -> dict:
    row = (await session.execute(text("""
        SELECT bank_id, legal_name, charter_number, primary_regulator,
               secondary_regulators, total_assets_usd, total_assets_as_of,
               fiscal_year_end, address
        FROM bank_profile
        WHERE bank_id = :b
    """), {"b": bank_id})).mappings().first()
    if row is None:
        raise LookupError(f"bank_profile missing for {bank_id!r}")
    return {
        **dict(row),
        "as_of": as_of.isoformat(),
    }


def _fiscal_quarter(as_of: _dt.date, fy_end: _dt.date) -> str:
    """Per design doc §11.1 Q12: fiscal quarter driven by fy_end.

    Mid-quarter as_of returns 'FQn-FYxxxx partial'."""
    # Map calendar month to fiscal-quarter offset relative to fy_end.
    fy_end_month = fy_end.month
    months_since_fy_start = ((as_of.month - (fy_end_month % 12) - 1) % 12)
    fq = months_since_fy_start // 3 + 1
    fy_year = as_of.year if as_of.month > fy_end_month else as_of.year - 1
    label = f"FQ{fq}-FY{fy_year + 1}"
    # If as_of is not the last day of the fiscal quarter, mark partial.
    return label  # caller decides "partial" suffix


# ── Type 1 — examiner_package ────────────────────────────────────────

@register("examiner_package")
async def _examiner_package(session, *, bank_id, as_of):
    cover = await _bank_cover(session, bank_id, as_of)

    # §11.3 hard gate: the exam package is the surface prior corpus rows
    # used to launder INTERNAL_CONTROL as codified law (C-1). We join
    # control_rule_mappings → compliance_rules so every rendered row
    # carries the authority classification, and split into blocking vs
    # advisory rails via may_assert_as_law() below.
    controls = (await session.execute(text("""
        SELECT c.control_id, c.name, c.owner_role, o.full_name AS owner_name,
               t.test_id, t.threshold_value, t.threshold_unit,
               t.threshold_rationale, t.jira_story_key, t.github_pr_url,
               t.commit_hash,
               r.rule_id AS rule_id,
               r.authority_type AS authority_type,
               r.citation_verified AS citation_verified,
               r.cfr_citation AS cfr_citation,
               r.authority_source AS authority_source
        FROM controls c
        LEFT JOIN bank_officers o ON o.person_id = c.owner_person_id
        LEFT JOIN control_tests t ON t.control_id = c.control_id
        LEFT JOIN control_rule_mappings m
            ON m.control_id = c.control_id
            AND m.bank_id = c.bank_id
            AND m.superseded_at IS NULL
        LEFT JOIN compliance_rules r ON r.rule_id = m.rule_id
        WHERE c.bank_id = :b
        ORDER BY c.control_id, t.test_id
    """), {"b": bank_id})).mappings().all()

    blocking: list[dict] = []
    advisory: list[dict] = []
    for row in controls:
        d = dict(row)
        if may_assert_as_law(d):
            # LAW rail — populate the citation column from cfr_citation
            # so the examiner sees the specific section that requires
            # this control test.
            d["citation"] = d.get("cfr_citation") or d.get("threshold_rationale") or "—"
            d["source"] = None
            blocking.append(d)
        else:
            # Advisory rail — citation column is intentionally blank; the
            # source column carries the manual / bulletin reference. Prior
            # corpus fabricated CFR cites here (see docs/correction_smb.md
            # C-1 for the wire dual-approval example), so we refuse to
            # emit a CFR string on this rail even if one is present.
            d["citation"] = None
            d["source"] = d.get("authority_source") or d.get("threshold_rationale") or "—"
            advisory.append(d)

    executions = (await session.execute(text("""
        SELECT test_id, executed_at, result, evidence_s3_uri
        FROM test_executions
        WHERE bank_id = :b AND executed_at <= :asof
        ORDER BY test_id, executed_at DESC
    """), {"b": bank_id, "asof": _dt.datetime.combine(as_of, _dt.time.max)})).mappings().all()

    return {
        "cover": cover,
        # `controls` kept for template back-compat; new templates should
        # render blocking + advisory sections separately.
        "controls": blocking + advisory,
        "controls_blocking": blocking,
        "controls_advisory": advisory,
        "execution": [dict(r) for r in executions],
    }


# ── Type 2 — ofac_attestation ────────────────────────────────────────

@register("ofac_attestation")
async def _ofac_attestation(session, *, bank_id, as_of):
    cover = await _bank_cover(session, bank_id, as_of)

    # Latest OFAC list version (synced from P1 via sync_api).
    lv_row = (await session.execute(text("""
        SELECT list_name, version_id, published_at, ingested_at, sha256_of_xml
        FROM ofac_list_versions
        WHERE list_name = 'sdn'
        ORDER BY ingested_at DESC LIMIT 1
    """))).mappings().first()

    # Latest independent validation.
    val = (await session.execute(text("""
        SELECT v.validation_id, v.performed_on, o.full_name AS validator,
               v.scope, v.methodology, v.findings_summary, v.next_due
        FROM ofac_validations v
        LEFT JOIN bank_officers o ON o.person_id = v.performed_by_person_id
        WHERE v.bank_id = :b
        ORDER BY v.performed_on DESC LIMIT 1
    """), {"b": bank_id})).mappings().first()

    return {
        "cover": cover,
        "list_version": dict(lv_row) if lv_row else None,
        "validation": dict(val) if val else None,
    }


# ── Type 3 — board_report ────────────────────────────────────────────

@register("board_report")
async def _board_report(session, *, bank_id, as_of):
    cover = await _bank_cover(session, bank_id, as_of)
    fy_end = cover["fiscal_year_end"]
    quarter_label = _fiscal_quarter(as_of, fy_end)

    enforcement = (await session.execute(text("""
        SELECT regulator, action_date, action_type, summary_pretty,
               penalty_amount_usd, peer_signal_score
        FROM enforcement_actions
        WHERE is_active = TRUE AND action_date <= :asof
        ORDER BY action_date DESC LIMIT 25
    """), {"asof": as_of})).mappings().all()

    return {
        "cover": cover,
        "quarter": quarter_label,
        "enforcement": [dict(r) for r in enforcement],
    }


# ── Type 4 — mra_tracker ─────────────────────────────────────────────

@register("mra_tracker")
async def _mra_tracker(session, *, bank_id, as_of):
    cover = await _bank_cover(session, bank_id, as_of)
    findings = (await session.execute(text("""
        SELECT finding_id, type, severity, regulatory_source,
               concern, cause, consequence, corrective_action, commitment,
               status, identified_date, due_date, repeat_flag,
               repeat_of_finding_id
        FROM findings
        WHERE bank_id = :b AND identified_date <= :asof
        ORDER BY identified_date DESC
    """), {"b": bank_id, "asof": as_of})).mappings().all()
    return {"cover": cover, "findings": [dict(r) for r in findings]}


# ── Type 5 — change_log ──────────────────────────────────────────────

@register("change_log")
async def _change_log(session, *, bank_id, as_of):
    cover = await _bank_cover(session, bank_id, as_of)
    rows = (await session.execute(text("""
        SELECT d.change_ref, d.disposition, o.full_name AS owner_name,
               d.impl_link, d.status, d.dispositioned_at, d.vocab_version,
               r.regulator, r.change_summary
        FROM change_record_dispositions d
        LEFT JOIN bank_officers o ON o.person_id = d.owner_person_id
        LEFT JOIN regulatory_change_records r ON r.change_ref = d.change_ref
        WHERE d.bank_id = :b
        ORDER BY d.created_at DESC
    """), {"b": bank_id})).mappings().all()
    return {"cover": cover, "change_log": [dict(r) for r in rows]}


# ── Type 6 — coverage ────────────────────────────────────────────────

@register("coverage")
async def _coverage(session, *, bank_id, as_of):
    cover = await _bank_cover(session, bank_id, as_of)
    total = (await session.execute(text(
        "SELECT count(*) FROM control_rule_mappings WHERE bank_id = :b AND superseded_at IS NULL"
    ), {"b": bank_id})).scalar() or 0
    tested = (await session.execute(text("""
        SELECT count(DISTINCT m.rule_id)
        FROM control_rule_mappings m
        JOIN control_tests t ON t.control_id = m.control_id
        WHERE m.bank_id = :b AND m.superseded_at IS NULL
    """), {"b": bank_id})).scalar() or 0
    return {
        "cover": cover,
        "total_mapped_rules": int(total),
        "tested_rules": int(tested),
        "coverage_pct": round(100.0 * tested / total, 1) if total else 0.0,
    }
