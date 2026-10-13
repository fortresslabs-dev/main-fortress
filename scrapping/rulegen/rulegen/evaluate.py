"""Score an extraction run against the hand-derived answers.

A prompt change without a score is a guess. This turns 'the prompt feels
better' into a number, on five sections where the right answer is known.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

from .golden import SECTIONS


@dataclass
class Score:
    citation: str
    expected_duties: int
    found_duties: int
    recall: float
    over_extraction: int          # more than expected — scaffolding promoted
    evidence_in_vocab: float
    spans_verified: float
    charter_errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def score_section(citation: str, rules: list, vocabulary: set[str]) -> Score:
    spec = SECTIONS[citation]
    expected = spec["expected_duties"]
    found = len(rules)

    def get(r, k, default=None):
        return r.get(k, default) if isinstance(r, dict) else getattr(r, k, default)

    in_vocab = sum(1 for r in rules
                   if (get(r, "evidence_source") or "").lower() in vocabulary)
    verified = sum(1 for r in rules if get(r, "route") != "block")

    charter_errors = []
    if citation.startswith("12 CFR 748"):
        for r in rules:
            if get(r, "applies_bank") == "Y":
                charter_errors.append(
                    f"{get(r, 'rule_id')}: applies_bank=Y on an NCUA part")
    if citation.startswith(("31 CFR 1010", "31 CFR 1020")):
        for r in rules:
            if get(r, "applies_credit_union") == "N":
                charter_errors.append(
                    f"{get(r, 'rule_id')}: applies_credit_union=N under "
                    f"Chapter X, where 1010.100(d) includes credit unions")

    notes = []
    if found < expected:
        notes.append(f"under by {expected - found}: prompt too strict, or "
                     f"the noun-phrase example is missing")
    if found > expected:
        notes.append(f"over by {found - expected}: scaffolding likely being "
                     f"promoted into rules")
    if spec.get("trap"):
        notes.append(f"trap: {spec['trap']}")

    return Score(
        citation=citation, expected_duties=expected, found_duties=found,
        recall=round(min(found, expected) / expected, 2) if expected else 0.0,
        over_extraction=max(0, found - expected),
        evidence_in_vocab=round(in_vocab / found, 2) if found else 0.0,
        spans_verified=round(verified / found, 2) if found else 0.0,
        charter_errors=charter_errors, notes=notes)


def score_run(results: dict[str, list], vocabulary: list[str]) -> dict:
    """results: {citation -> [rules]}. Only golden sections are scored."""
    vocab = {v.lower() for v in vocabulary}
    scores = [score_section(c, rules, vocab)
              for c, rules in results.items() if c in SECTIONS]
    if not scores:
        return {"scored": 0, "note": "no golden sections in this run"}

    exp = sum(s.expected_duties for s in scores)
    got = sum(s.found_duties for s in scores)
    return {
        "scored": len(scores),
        "expected_duties": exp,
        "found_duties": got,
        "recall": round(sum(min(s.found_duties, s.expected_duties)
                            for s in scores) / exp, 3) if exp else 0,
        "over_extraction": sum(s.over_extraction for s in scores),
        "evidence_in_vocab": round(
            sum(s.evidence_in_vocab for s in scores) / len(scores), 3),
        "charter_errors": [e for s in scores for e in s.charter_errors],
        "per_section": [s.to_dict() for s in scores],
    }


def verdict(run: dict) -> tuple[bool, str]:
    """Should this prompt version be promoted?

    Gates chosen so a regression cannot pass quietly. Recall below 0.85
    means duties are being missed; any charter error means the Title 12
    flip is not landing.
    """
    if run.get("scored", 0) == 0:
        return False, "nothing scored — run the golden batch first"
    if run["recall"] < 0.85:
        return False, f"recall {run['recall']} below 0.85 — duties are being missed"
    if run["charter_errors"]:
        return False, (f"{len(run['charter_errors'])} charter errors — the "
                       f"Title 12 / Chapter X defaults are not landing")
    if run["over_extraction"] > run["expected_duties"] * 0.2:
        return False, (f"over-extraction {run['over_extraction']} — scaffolding "
                       f"is being promoted into rules")
    if run["evidence_in_vocab"] < 0.9:
        return False, (f"evidence in vocabulary {run['evidence_in_vocab']} — "
                       f"the model is inventing artifacts")
    return True, "promote"
