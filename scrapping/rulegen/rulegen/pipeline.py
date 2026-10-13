"""First encounter extracts. Every night after that only verifies.

The design point: the span a rule was drawn from IS the anchor. Tonight,
re-fetch and ask whether that exact string is still in the section. Yes
means the rule is still grounded — no model call, no lookup table. No
means something moved, and a human sees a diff.

That works on a section nobody has curated, which is what makes it
scale past the sections someone had time to hand-register.

Obligations are separate records from rules. Two rules covering the
same duty share one obligation via obligation_id. Collapse them to a
string field on Rule (as this used to) and N rules always produce N
obligations — the 1:1 defect this project exists to prevent.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .mechanical import Unit, split, stem, find_numbers, span_present

# Values that have been wrong in production HERE. Not a threshold table —
# this never grows with the corpus, only when something goes wrong.
NEVER_AGAIN = {
    "travel_rule_10k": (r"(?:transmittal|travel\s+rule)[^.]{0,80}10,?000",
                        "1010.410(f) transmittal threshold is $3,000"),
    "cdd_20_percent": (r"\b20\s?(?:percent|%)[^.]{0,60}(?:owner|beneficial)",
                       "beneficial ownership is 25 percent"),
    "sar_90_day": (r"\b90\s+(?:calendar\s+)?days?[^.]{0,40}(?:SAR|suspicious)",
                   "SAR deadline is 30 days, 60 maximum"),
    "wire_dual_approval": (r"dual[-\s](?:approval|authorization)",
                           "no federal dual-approval requirement exists"),
}

_CRITICALITY_RANK = {"Critical": 3, "High": 2, "Medium": 1, "": 0, None: 0}


def _norm(text: str) -> str:
    """Whitespace-and-case-normalised text. The only relaxation permitted."""
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def obligation_key(citation: str, obligation_text: str) -> str:
    """Group key for obligations. Same citation + normalised text = same duty.

    Kept simple on purpose. A fuzzier key would merge duties the model
    intended to keep apart; a stricter one would fragment a duty across
    minor wording variance.
    """
    return f"{citation}|{_norm(obligation_text)}"


@dataclass
class Obligation:
    """One duty. Rules attach to obligations via obligation_id."""
    obligation_id: str
    citation: str
    subsection: str            # (a) (2) from the first member rule's path
    obligation_text: str
    criticality: str
    applies_bank: str
    applies_credit_union: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Rule:
    rule_id: str
    citation: str
    path: str
    obligation_id: str         # anchor into the obligations table
    obligation_text: str       # denormalised for legibility in the queue
    rule_logic: str
    source_span: str
    evidence_source: str
    rule_class: str
    applies_bank: str
    applies_credit_union: str
    criticality: str
    threshold_value: object = None
    threshold_unit: Optional[str] = None
    authority_type: str = "LAW"
    confidence: float = 0.0
    route: str = "review"
    reasons: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class SectionRecord:
    citation: str
    content_hash: str
    section_text: str
    source_url: str
    retrieved_at: str
    obligations: list = field(default_factory=list)
    rules: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["obligations"] = [o.to_dict() if isinstance(o, Obligation) else o
                            for o in self.obligations]
        d["rules"] = [r.to_dict() if isinstance(r, Rule) else r
                      for r in self.rules]
        return d


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _check(rule: Rule, section_text: str) -> Rule:
    """Deterministic gates. These overrule the model; it cannot overrule them."""
    if not span_present(rule.source_span, section_text):
        rule.route = "block"
        rule.reasons.append("BLOCK: source_span is not in the fetched text")

    ev = (rule.evidence_source or "").strip().lower()
    if not ev:
        rule.route = "block"
        rule.reasons.append("BLOCK: no evidence named — nothing can test this")
    elif re.search(r"\d{1,4}\.\d+|\bCFR\b", rule.evidence_source, re.I):
        rule.route = "block"
        rule.reasons.append("BLOCK: evidence is a citation, not a bank record")

    for key, (pattern, correct) in NEVER_AGAIN.items():
        if re.search(pattern, rule.rule_logic, re.I):
            rule.route = "block"
            rule.reasons.append(f"BLOCK: matched {key} — {correct}")

    if rule.citation.startswith(("31 CFR 1010", "31 CFR 1020")) \
            and rule.applies_credit_union == "N":
        rule.reasons.append("REVIEW: credit unions are 'banks' under "
                            "1010.100(d) — confirm the carve-out")

    if len(rule.rule_logic) > 250:
        rule.reasons.append(f"REVIEW: {len(rule.rule_logic)} chars — "
                            "check this is one assertion")

    if rule.route != "block":
        blocking = any(r.startswith("BLOCK") for r in rule.reasons)
        reviewing = any(r.startswith("REVIEW") for r in rule.reasons)
        rule.route = "block" if blocking else ("review" if reviewing or
                                               rule.confidence < 0.6
                                               else "auto_promote")
    return rule


def _upgrade_criticality(current: str, candidate: str) -> str:
    """An obligation is as critical as its most critical member rule."""
    if _CRITICALITY_RANK.get(candidate, 0) > _CRITICALITY_RANK.get(current, 0):
        return candidate
    return current or "Medium"


def _widen(current: str, candidate: str) -> str:
    """If any rule applies to a charter, the obligation covers that charter."""
    return "Y" if "Y" in (current, candidate) else "N"


# ── first encounter ───────────────────────────────────────────────────
def extract(citation: str, section_text: str, source_url: str,
            classify: Callable[[Unit, str], dict]) -> SectionRecord:
    """Run once per section, ever. One model call per unit.

    Emits obligations and rules as separate records. A rule points at its
    obligation by obligation_id; two rules with the same normalised
    obligation_text (per citation) share the same obligation.
    """
    record = SectionRecord(
        citation=citation, content_hash=_hash(section_text),
        section_text=section_text, source_url=source_url,
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    stem_text = stem(section_text)
    obligations_by_key: dict[str, Obligation] = {}
    seq = 0
    for unit in split(section_text):
        answer = classify(unit, stem_text)
        if not answer.get("is_duty") or answer.get("actor") != "institution":
            continue

        numbers = find_numbers(unit.text)
        seq += 1
        obligation_text = answer.get("obligation", "")
        applies_bank = answer.get("applies_bank", "Y")
        applies_cu = answer.get("applies_credit_union", "Y")
        criticality = answer.get("criticality", "Medium")

        key = obligation_key(citation, obligation_text)
        ob = obligations_by_key.get(key)
        if ob is None:
            ob_id = f"OB-{citation.replace(' ', '-')}-{len(obligations_by_key) + 1:03d}"
            ob = Obligation(
                obligation_id=ob_id, citation=citation,
                subsection=unit.parent or unit.marker,
                obligation_text=obligation_text,
                criticality=criticality,
                applies_bank=applies_bank,
                applies_credit_union=applies_cu,
            )
            obligations_by_key[key] = ob
        else:
            ob.criticality = _upgrade_criticality(ob.criticality, criticality)
            ob.applies_bank = _widen(ob.applies_bank, applies_bank)
            ob.applies_credit_union = _widen(ob.applies_credit_union, applies_cu)

        rule = Rule(
            rule_id=f"{citation.replace(' ', '-')}-R{seq:02d}",
            citation=citation, path=unit.path,
            obligation_id=ob.obligation_id,
            obligation_text=obligation_text,
            rule_logic=answer.get("rule_logic", ""),
            source_span=answer.get("source_span", ""),
            evidence_source=answer.get("evidence_source", ""),
            rule_class=answer.get("rule_class", "REGULATORY"),
            applies_bank=applies_bank,
            applies_credit_union=applies_cu,
            criticality=criticality,
            threshold_value=numbers[0]["value"] if numbers else None,
            threshold_unit=numbers[0]["unit"] if numbers else None,
            confidence=float(answer.get("confidence", 0)),
        )
        record.rules.append(_check(rule, section_text))

    record.obligations = list(obligations_by_key.values())
    return record


# ── every night after ─────────────────────────────────────────────────
@dataclass
class Recheck:
    citation: str
    changed: bool
    surviving: list = field(default_factory=list)
    broken: list = field(default_factory=list)
    model_calls: int = 0
    note: str = ""


def recheck(stored: SectionRecord, fresh_text: str,
            classify: Optional[Callable] = None) -> Recheck:
    """Tonight's pass. Costs one hash comparison when nothing moved.

    On drift, each rule is re-anchored by its own span. A rule whose span
    survives is still grounded even though the section changed elsewhere.
    Only rules whose span vanished need the model again.
    """
    fresh_hash = _hash(fresh_text)
    if fresh_hash == stored.content_hash:
        return Recheck(stored.citation, changed=False,
                       surviving=[r.rule_id if isinstance(r, Rule) else r["rule_id"]
                                  for r in stored.rules],
                       note="hash unchanged — nothing to do")

    out = Recheck(stored.citation, changed=True,
                  note="section text changed — rules re-anchored by span")
    for r in stored.rules:
        rule = r if isinstance(r, Rule) else Rule(**r)
        if span_present(rule.source_span, fresh_text):
            out.surviving.append(rule.rule_id)
        else:
            out.broken.append(rule.rule_id)

    # A changed section may also have GAINED a duty, which no span check
    # can see. Re-classify the whole section and compare the count.
    if classify is not None:
        fresh = extract(stored.citation, fresh_text, stored.source_url, classify)
        out.model_calls = len(split(fresh_text))
        gained = len(fresh.rules) - len(stored.rules)
        if gained > 0:
            out.note += f"; {gained} new duty candidate(s) found"
    return out


# ── storage ───────────────────────────────────────────────────────────
def save(record: SectionRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2))


def load(path: Path) -> SectionRecord:
    d = json.loads(path.read_text())
    obligations = [Obligation(**o) for o in d.pop("obligations", [])]
    rules = [Rule(**r) for r in d.pop("rules")]
    return SectionRecord(**d, obligations=obligations, rules=rules)
