"""
Obligation Extractor — REQ-010: Obligation Universe Coverage Score.

Parses rule_text from ComplianceRule rows into discrete obligation sentences,
stores them in the obligations table, and computes daily coverage snapshots.

Extraction strategy — REGEX-FIRST with LLM fallback for the residual gap.

Per-rule decision tree (see `extract_for_rule`):
  1. Run regex pass. Yields (obligations, primary_cfr_or_None).
  2. If regex output is sufficient — primary_cfr is not None AND
     len(obligations) > 0 — we're done. No Bedrock call.
  3. Else, if `LLM_EXTRACTION_MODE` permits, invoke `extract_rule` to
     fill the empty slots. The LLM never overwrites a regex hit:
       - LLM fills cfr_citation_primary ONLY when regex returned None.
       - LLM fills obligations ONLY when regex returned an empty list.
     Connector-emitted values + regex hits are preserved verbatim.

Modes (env `LLM_EXTRACTION_MODE`):

  off (default): regex only. No Bedrock cost. Same recall as the legacy
                 pipeline (3.6% CFR recall on testbank — see
                 scripts/backfill_cfr_citations.py).
  fallback:     regex + LLM-as-fallback per the decision tree above.
                LLM fires on the ~96% of testbank rules where regex
                returned no cite. Cost ~$0.0014 per LLM call on Haiku 4.5.
  shadow:       run BOTH paths on regex-insufficient rules. Persist the
                regex output (no behavior change), log the LLM result +
                a diff for drift monitoring. Use this as a 1-2 week gate
                before flipping to `fallback` in production.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Sentence splitter — splits on ". " or ".\n" boundaries
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Obligation trigger phrases (matches must include sentence-level context)
_OBLIGATION_TRIGGERS = re.compile(
    r"\b(shall|must|required to|is required|are required|prohibited from|may not|will not|"
    r"needs to|have to|has to|failure to|must not)\b",
    re.IGNORECASE,
)

# CFR citation pattern
_CFR_RE = re.compile(r"(\d+\s+C\.F\.R\.?\s*§\s*[\d.]+(?:\([a-z0-9]+\))*)", re.I)

# Section reference
_SEC_RE = re.compile(r"§\s*([\d.]+(?:\([a-z0-9]+\))*)", re.I)


# DB-allowed values for obligations.obligation_type (see
# alembic 008 CHECK ck_obligation_type). Sentences that cannot be
# classified into this set are dropped upstream — persisting anything
# else trips the check constraint on insert.
_DB_OBLIGATION_TYPES = frozenset({"shall", "must", "prohibited", "required", "may_not"})


def _classify_obligation_type(sentence: str) -> Optional[str]:
    """Return the primary obligation type for a sentence, or None if
    the sentence carries no recognisable obligation verb.

    None means "drop this sentence" — callers must not persist it. The
    return value is constrained to `_DB_OBLIGATION_TYPES`.
    """
    s = sentence.lower()
    if re.search(r"\bprohibited\b|\bmay not\b|\bmust not\b|\bwill not\b", s):
        return "prohibited"
    if re.search(r"\bshall\b", s):
        return "shall"
    if re.search(r"\bmust\b", s):
        return "must"
    if re.search(r"\brequired\b|\bneeds to\b|\bhave to\b|\bhas to\b", s):
        return "required"
    return None


def extract_obligation_sentences(rule_text: str) -> List[str]:
    """Split rule_text into sentences that contain obligation language."""
    if not rule_text:
        return []
    sentences = _SENTENCE_RE.split(rule_text.strip())
    return [s.strip() for s in sentences if s.strip() and _OBLIGATION_TRIGGERS.search(s)]


# ── Extraction primitives ─────────────────────────────────────────────
# Result tuple shape: (sentence, obligation_type, cfr_citation, section_ref).
# Both the regex and LLM-fill paths produce this shape so the persist
# loop in `ObligationExtractor.run()` is extractor-agnostic.

ExtractedObligation = Tuple[str, str, Optional[str], Optional[str]]


def _extraction_mode() -> str:
    """One of {off, fallback, shadow}. Default off — opt-in rollout."""
    raw = (os.environ.get("LLM_EXTRACTION_MODE") or "off").strip().lower()
    if raw in {"off", "fallback", "shadow"}:
        return raw
    logger.warning("llm_extraction_mode_invalid", got=raw, using="off")
    return "off"


def _min_confidence() -> float:
    try:
        return float(os.environ.get("LLM_EXTRACTION_MIN_CONFIDENCE", "0.5"))
    except ValueError:
        return 0.5


def _regex_extract(
    rule_text: str,
    rule_cfr_citation: Optional[str],
) -> Tuple[List[ExtractedObligation], Optional[str]]:
    """Regex pass. Returns (obligations, primary_cfr_for_rule).

    `primary_cfr_for_rule` is the first CFR cite found anywhere in the
    rule_text via `_CFR_RE`. This is what we want to write back to
    `compliance_rules.cfr_citation` when the column is NULL.

    Connector-emitted `rule_cfr_citation` is used as the per-obligation
    default when a sentence has no inline cite, but is NOT returned as
    `primary_cfr_for_rule` — the regex path only claims authority over
    cites it found in the text itself.
    """
    sentences = extract_obligation_sentences(rule_text or "")
    obligations: List[ExtractedObligation] = []
    rule_primary: Optional[str] = None
    for sentence in sentences:
        cfr_match = _CFR_RE.search(sentence)
        sec_match = _SEC_RE.search(sentence)
        cite = cfr_match.group(1).strip() if cfr_match else rule_cfr_citation
        if rule_primary is None and cfr_match:
            rule_primary = cfr_match.group(1).strip()
        obl_type = _classify_obligation_type(sentence)
        if obl_type is None:
            # Sentence tripped the outer trigger but no verb class matched
            # (rare — happens on paraphrased triggers). Drop rather than
            # emit a value the DB CHECK constraint will reject.
            continue
        obligations.append((
            sentence,
            obl_type,
            cite,
            sec_match.group(0) if sec_match else None,
        ))
    return obligations, rule_primary


async def _llm_extract_for_gaps(
    rule_text: str,
    regulator: Optional[str],
    rule_cfr_citation: Optional[str],
    min_confidence: float,
    reg_dim: Dict[str, str],
) -> Optional[Tuple[List[ExtractedObligation], Optional[str], float]]:
    """Invoke the LLM extractor as fallback for a regex-insufficient rule.

    Returns (obligations, primary_cfr, confidence) on success, or None
    when the LLM either failed or returned low confidence — in which
    case the caller keeps whatever the regex produced.

    Every terminal outcome (success / invoke failure / validation
    failure / low confidence) emits an EMF metric so CW alarms can
    track the fallback's own health distinct from the regex layer.
    """
    from src.extraction.bedrock_extractor import _emit_extraction_metric
    from src.extraction.rule import extract_rule

    try:
        extraction = await extract_rule(rule_text, regulator)
    except Exception as exc:
        # bedrock_extractor already logged ERROR + emitted
        # BedrockInvokeFailures / ValidationFailures with exc_info.
        # Count the consequence at the obligation_extractor layer.
        logger.warning(
            "llm_fallback_failed",
            regulator=regulator,
            reason=type(exc).__name__,
            error=str(exc)[:300],
        )
        _emit_extraction_metric(
            "LlmFallbackFailed", "RuleExtraction",
            dimensions={**reg_dim, "Reason": type(exc).__name__[:64]},
        )
        return None

    if extraction.confidence < min_confidence:
        logger.warning(
            "llm_fallback_low_confidence",
            regulator=regulator,
            confidence=extraction.confidence,
            reasoning=extraction.reasoning,
        )
        _emit_extraction_metric(
            "LlmFallbackLowConfidence", "RuleExtraction", dimensions=reg_dim,
        )
        _emit_extraction_metric(
            "LlmFallbackFailed", "RuleExtraction",
            dimensions={**reg_dim, "Reason": "LowConfidence"},
        )
        return None

    obligations: List[ExtractedObligation] = []
    for obl in extraction.obligations:
        # RuleExtraction's ObligationTypeStr admits 'permissive' and
        # 'none' which describe non-duty sentences. The obligations
        # table's CHECK constraint rejects both — skip them here rather
        # than fail the insert.
        if obl.obligation_type not in _DB_OBLIGATION_TYPES:
            continue
        cite = (
            obl.cfr_citation
            or extraction.cfr_citation_primary
            or rule_cfr_citation
        )
        obligations.append((
            obl.source_text,
            obl.obligation_type,
            cite,
            obl.section_reference,
        ))
    return obligations, extraction.cfr_citation_primary, extraction.confidence


async def extract_for_rule(
    rule_text: str,
    regulator: Optional[str],
    rule_cfr_citation: Optional[str] = None,
    mode: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> Tuple[List[ExtractedObligation], Optional[str], bool]:
    """Regex-first extraction with optional LLM fallback for missing slots.

    SINGLE SOURCE OF TRUTH — both `ObligationExtractor.run()` and
    `scripts/backfill_rule_extractions.py` call this. The merge policy is:

      - If regex returned a primary_cfr → keep regex's cite (never
        overwritten by the LLM).
      - If regex returned obligations → keep regex's obligations (never
        overwritten by the LLM).
      - LLM output is *additive* — fills only what regex left empty.

    Args:
        rule_text:           ComplianceRule.rule_text
        regulator:           ComplianceRule.regulator (hint for LLM jurisdiction)
        rule_cfr_citation:   ComplianceRule.cfr_citation (the connector's
                             already-emitted authority, if any)
        mode:                Override `LLM_EXTRACTION_MODE`
        min_confidence:      Override `LLM_EXTRACTION_MIN_CONFIDENCE`

    Returns:
        (obligations, primary_cfr_or_None, used_llm)

        used_llm is True only when the LLM actually produced output that
        was merged into the result; False if regex was sufficient or the
        LLM was disabled / failed.
    """
    from src.extraction.bedrock_extractor import _emit_extraction_metric

    mode = mode if mode is not None else _extraction_mode()
    min_conf = min_confidence if min_confidence is not None else _min_confidence()
    reg_dim = {"Regulator": (regulator or "UNKNOWN")[:64]}

    # ── Step 1: regex pass (always runs) ──────────────────────────────
    regex_obls, regex_primary = _regex_extract(rule_text or "", rule_cfr_citation)
    regex_has_cite = regex_primary is not None
    regex_has_obls = len(regex_obls) > 0

    if regex_has_cite and regex_has_obls:
        # Regex output is sufficient — no LLM call, no gap to fill.
        _emit_extraction_metric(
            "RegexSufficient", "RuleExtraction", dimensions=reg_dim,
        )
        return regex_obls, regex_primary, False

    # ── Step 2: bail out if LLM disabled or input empty ───────────────
    if mode == "off" or not (rule_text and rule_text.strip()):
        # Insufficient regex output but mode forbids LLM, or there's
        # nothing for the LLM to read. Surface the gap so ops can see
        # the rate of regex-only insufficient rules.
        _emit_extraction_metric(
            "RegexInsufficient", "RuleExtraction",
            dimensions={
                **reg_dim,
                "Gap": _gap_label(regex_has_cite, regex_has_obls),
            },
        )
        return regex_obls, regex_primary, False

    # ── Step 3: LLM fallback ──────────────────────────────────────────
    llm_result = await _llm_extract_for_gaps(
        rule_text, regulator, rule_cfr_citation, min_conf, reg_dim,
    )

    # Shadow mode — log divergence but persist regex output, never merge.
    if mode == "shadow":
        if llm_result is not None:
            llm_obls, llm_primary, llm_conf = llm_result
            logger.info(
                "llm_shadow_diff",
                regulator=regulator,
                regex_obligations=len(regex_obls),
                llm_obligations=len(llm_obls),
                regex_primary_cfr=regex_primary,
                llm_primary_cfr=llm_primary,
                cfr_match=(regex_primary == llm_primary),
                llm_confidence=llm_conf,
            )
            _emit_extraction_metric(
                "LlmShadowRun", "RuleExtraction", dimensions=reg_dim,
            )
        return regex_obls, regex_primary, False

    # Fallback mode — merge per slot, regex wins on conflict.
    if llm_result is None:
        # LLM unavailable; regex output is what we have.
        return regex_obls, regex_primary, False

    llm_obls, llm_primary, _llm_conf = llm_result
    final_primary = regex_primary if regex_has_cite else llm_primary
    final_obls = regex_obls if regex_has_obls else llm_obls

    used_llm = (final_primary == llm_primary and not regex_has_cite) or \
               (final_obls is llm_obls and not regex_has_obls)
    if used_llm:
        _emit_extraction_metric(
            "LlmFallbackInvoked", "RuleExtraction", dimensions=reg_dim,
        )
    return final_obls, final_primary, used_llm


def _gap_label(has_cite: bool, has_obls: bool) -> str:
    if not has_cite and not has_obls:
        return "BothMissing"
    if not has_cite:
        return "CiteMissing"
    return "ObligationsMissing"


class ObligationExtractor:
    """Extracts discrete obligation sentences from ComplianceRule rows.

    Usage (called from scraper_scheduler after a scrape cycle):

        extractor = ObligationExtractor()
        await extractor.run(db_session)
    """

    async def run(self, session) -> Tuple[int, int]:
        """Process all active ComplianceRule rows and upsert Obligation rows.

        Delegates per-rule extraction to `extract_for_rule()` — the same
        entry point used by `scripts/backfill_rule_extractions.py`. The
        per-rule decision tree (regex-first, LLM-fallback on gaps) lives
        there.

        Writes back `compliance_rules.cfr_citation` ONLY when:
          - the column is currently NULL, AND
          - the LLM fallback supplied a primary cite.

        Regex hits never trigger a cfr_citation write — those were
        already in `rule_text` and the connector's row of authority
        is preserved.

        Returns:
            (obligations_created, obligations_updated) counts.
        """
        from sqlalchemy import select, update
        from src.models.database import ComplianceRule, Obligation

        mode = _extraction_mode()
        min_conf = _min_confidence()

        result = await session.execute(
            select(
                ComplianceRule.rule_id,
                ComplianceRule.rule_text,
                ComplianceRule.regulator,
                ComplianceRule.category,
                ComplianceRule.cfr_citation,
            ).where(ComplianceRule.is_active == True)
        )
        rules = result.all()

        created = 0
        updated = 0
        obligation_counts: Dict[str, int] = {}
        cfr_writes = 0
        llm_used = 0

        for rule_id, rule_text, regulator, category, cfr_citation in rules:
            obls, primary_cfr, used_llm = await extract_for_rule(
                rule_text or "",
                regulator,
                cfr_citation,
                mode=mode,
                min_confidence=min_conf,
            )
            obligation_counts[rule_id] = len(obls)
            if used_llm:
                llm_used += 1

            # ── UPSERT compliance_rules.cfr_citation if currently NULL ──
            # Only writes when the cite came from the LLM (regex hits in
            # rule_text are already discoverable; connector cites are
            # already in the column). The is_(None) guard makes this
            # idempotent across re-runs.
            if used_llm and primary_cfr and cfr_citation is None:
                await session.execute(
                    update(ComplianceRule)
                    .where(ComplianceRule.rule_id == rule_id)
                    .where(ComplianceRule.cfr_citation.is_(None))
                    .values(cfr_citation=primary_cfr)
                )
                cfr_writes += 1

            # ── UPSERT obligations rows ───────────────────────────────
            for idx, (sentence, obl_type, cite, sec_ref) in enumerate(obls):
                obligation_id = f"{rule_id}::OBL::{idx + 1:03d}"

                with session.no_autoflush:
                    existing = await session.execute(
                        select(Obligation).where(Obligation.obligation_id == obligation_id)
                    )
                    row = existing.scalar_one_or_none()

                if row is None:
                    session.add(
                        Obligation(
                            obligation_id=obligation_id,
                            regulation_id=rule_id,
                            regulator=regulator or "UNKNOWN",
                            category=category or "general",
                            source_text=sentence,
                            obligation_type=obl_type,
                            section_reference=sec_ref,
                            cfr_citation=cite,
                            has_test_coverage=False,
                            is_active=True,
                        )
                    )
                    created += 1
                elif row.source_text != sentence:
                    row.source_text = sentence
                    row.obligation_type = obl_type
                    row.cfr_citation = cite
                    row.section_reference = sec_ref
                    updated += 1

        # Update obligation_count on ComplianceRule rows
        for rule_id, count in obligation_counts.items():
            await session.execute(
                update(ComplianceRule)
                .where(ComplianceRule.rule_id == rule_id)
                .values(obligation_count=count)
            )

        logger.info(
            "obligation_extraction_complete",
            mode=mode,
            rules_processed=len(rules),
            obligations_created=created,
            obligations_updated=updated,
            cfr_writes_from_llm=cfr_writes,
            llm_used=llm_used,
        )

        return created, updated

    async def compute_coverage_snapshots(self, session) -> None:
        """Aggregate obligation coverage by regulator and write daily snapshots."""
        from sqlalchemy import Integer, func, select
        from src.models.database import CoverageSnapshot, Obligation

        today = date.today()

        result = await session.execute(
            select(
                Obligation.regulator,
                Obligation.category,
                func.count(Obligation.id).label("total"),
                func.sum(
                    func.cast(Obligation.has_test_coverage, Integer)
                ).label("covered"),
            )
            .where(Obligation.is_active == True)
            .group_by(Obligation.regulator, Obligation.category)
        )
        rows = result.all()

        for regulator, category, total, covered in rows:
            covered = covered or 0
            coverage_pct = (covered / total * 100.0) if total > 0 else 0.0
            key = (today, regulator, category or "")

            with session.no_autoflush:
                existing = await session.execute(
                    select(CoverageSnapshot).where(
                        CoverageSnapshot.snapshot_date == today,
                        CoverageSnapshot.regulator == regulator,
                        CoverageSnapshot.category == category,
                    )
                )
                snap = existing.scalar_one_or_none()
            if snap is None:
                session.add(
                    CoverageSnapshot(
                        snapshot_date=today,
                        regulator=regulator,
                        category=category,
                        total_obligations=total,
                        covered_obligations=covered,
                        coverage_pct=coverage_pct,
                    )
                )
            else:
                snap.total_obligations = total
                snap.covered_obligations = covered
                snap.coverage_pct = coverage_pct

        logger.info("coverage_snapshots_computed", regulators=len(rows), date=str(today))
