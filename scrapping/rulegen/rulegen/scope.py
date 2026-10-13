"""Decide which sections deserve a model call.

Most of 31 CFR Chapter X is not a community bank's problem: definitions,
agency procedure, penalty schedules, and duties belonging to casinos,
MSBs, broker-dealers and insurers. Filtering those out before the model
is the difference between ~2,500 calls and ~900.

The gate is deliberately conservative. A wrong reject loses a section
silently, which is worse than a wrong rule — a wrong rule gets caught in
review, a missing section is invisible. When in doubt it keeps the
section and lets the model decide.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

# ── the parts that belong to somebody else ────────────────────────────
# 31 CFR Chapter X assigns a part per institution type. A community bank
# is 1010 (general) + 1020 (banks). Everything else is another industry.
OTHER_INSTITUTION_PARTS = {
    "1021": "casinos and card clubs",
    "1022": "money services businesses",
    "1023": "brokers or dealers in securities",
    "1024": "mutual funds",
    "1025": "insurance companies",
    "1026": "futures commission merchants",
    "1027": "dealers in precious metals and stones",
    "1028": "operators of credit card systems",
    "1029": "loan or finance companies",
    "1030": "housing government sponsored enterprises",
}

# ── operative language ────────────────────────────────────────────────
MODAL = re.compile(
    r"\b(shall|must|is required to|are required to|may not|shall not|"
    r"will notify|is prohibited)\b", re.I)

# The stem pattern that makes noun-phrase lists into duties. Without this
# the gate drops 1020.210 - whose five pillars contain no modal verb at
# all - and takes the AML program requirements with it.
NOUN_DUTY_STEM = re.compile(
    r"\b(includes?,?\s+at\s+a\s+minimum|shall\s+be\s+deemed\s+to\s+satisfy|"
    r"at\s+a\s+minimum,?\s+includes?|must\s+include|shall\s+include|"
    r"consists?\s+of\s+the\s+following|the\s+following\s+requirements)\b", re.I)

# ── actor signals ─────────────────────────────────────────────────────
AGENCY_ACTOR = re.compile(
    r"\b(FinCEN|OFAC|the Secretary|the Director|Treasury|the Administrator|"
    r"Federal functional regulator|administrative law judge|the Board|"
    r"the Comptroller|the Corporation|the NCUA Board)\s+(may|shall|will|must)\b")

INSTITUTION_ACTOR = re.compile(
    r"\b(a |each |every |the )?(bank|credit union|financial institution|"
    r"institution|savings association)s?\b\s*[^.]{0,80}?"
    r"\b(shall|must|is required|are required|may not|will notify)\b", re.I)

# ── section kinds that never carry a testable duty ────────────────────
DEFINITIONAL_HEADING = re.compile(
    r"^\s*(definitions?|scope|purpose|authority|general|"
    r"applicability|effective date|delegation|\[reserved\])\b", re.I)

PROCEDURAL_HEADING = re.compile(
    r"\b(administrative|procedure|hearing|penalt(y|ies)|assessment|"
    r"forfeiture|licens(e|ing)|ruling|exemption request|appeal|"
    r"civil money|enforcement)\b", re.I)


@dataclass
class ScopeVerdict:
    in_scope: bool
    reason: str
    signal: str          # which rule fired
    confidence: str      # "certain" | "likely" | "borderline"

    def to_dict(self) -> dict:
        return asdict(self)


def assess(citation: str, heading: str, text: str,
           part: Optional[str] = None) -> ScopeVerdict:
    """Should this section get model calls?

    Order matters: the cheapest and most certain rejects run first, and
    the noun-phrase rescue runs before the no-operative-language reject.
    """
    part = part or (re.search(r"(\d{3,4})\.", citation).group(1)
                    if re.search(r"(\d{3,4})\.", citation) else "")

    # 1. Reserved - no text to read.
    if re.search(r"\[reserved\]", heading, re.I) or not text.strip():
        return ScopeVerdict(False, "reserved or empty", "reserved", "certain")

    # 2. Another industry's part. Structural, not a judgement call.
    if part in OTHER_INSTITUTION_PARTS:
        return ScopeVerdict(False,
                            f"part {part} belongs to {OTHER_INSTITUTION_PARTS[part]}",
                            "other_institution_part", "certain")

    # 3. Noun-phrase duty stem - RESCUE, before any language-based reject.
    #    1020.210's five pillars have no modal verb. Reject on "no modal"
    #    and the most examined obligations in US banking disappear.
    if NOUN_DUTY_STEM.search(text):
        return ScopeVerdict(True, "enumerated requirements introduced by a stem",
                            "noun_duty_stem", "certain")

    # 4. Definitional or scope heading with no institution duty in the body.
    if DEFINITIONAL_HEADING.match(heading) and not INSTITUTION_ACTOR.search(text):
        return ScopeVerdict(False, "definitions or scope, no institution duty",
                            "definitional", "likely")

    # 5. Agency procedure: the agency acts, the institution does not.
    if AGENCY_ACTOR.search(text) and not INSTITUTION_ACTOR.search(text):
        return ScopeVerdict(False, "agency acts, institution has no duty",
                            "agency_procedure", "likely")

    # 6. Procedural heading AND no institution duty in the body.
    if PROCEDURAL_HEADING.search(heading) and not INSTITUTION_ACTOR.search(text):
        return ScopeVerdict(False, "administrative or penalty procedure",
                            "procedural_heading", "likely")

    # 7. No operative language anywhere.
    if not MODAL.search(text):
        return ScopeVerdict(False, "no operative language found",
                            "no_modal", "borderline")

    # 8. Default: keep it. A borderline section costs a few model calls;
    #    a wrongly dropped section costs a coverage gap nobody sees.
    return ScopeVerdict(True, "institution duty candidate", "modal_present",
                        "certain" if INSTITUTION_ACTOR.search(text) else "likely")


def summarise(verdicts: list[tuple[str, ScopeVerdict]]) -> dict:
    kept = [c for c, v in verdicts if v.in_scope]
    dropped = [(c, v) for c, v in verdicts if not v.in_scope]
    by_signal: dict[str, int] = {}
    for _, v in dropped:
        by_signal[v.signal] = by_signal.get(v.signal, 0) + 1
    borderline = [c for c, v in verdicts if v.confidence == "borderline"]
    return {
        "total": len(verdicts),
        "kept": len(kept),
        "dropped": len(dropped),
        "dropped_by_signal": by_signal,
        "borderline_review_these": borderline,
    }
