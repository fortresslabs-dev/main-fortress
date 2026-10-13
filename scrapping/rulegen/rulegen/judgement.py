"""The parts of my process that were reading comprehension.

Deciding which units impose a duty, grouping them into obligations,
composing the IF/THEN, naming the evidence artifact. There is no regex
for any of these, which is why this module calls a model.

The prompt is written to reproduce what I actually did on the five
sections in golden.py — which is also how it gets measured.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

from .mechanical import Unit

EVIDENCE_VOCAB = [
    "policy document", "board minutes", "audit report", "training record",
    "cdd system", "account opening file", "screening log", "filing record",
    "case file", "archive index", "access log", "customer record",
    "wire message", "aggregation engine", "alerting service",
    "notification record", "poc registration", "match report", "search log",
    "loan file", "collateral record", "lien filing", "rule config",
    "evidence connector", "transaction record",
]

SYSTEM = f"""You are a BSA/AML and banking compliance analyst decomposing one \
unit of a CFR section into a testable rule.

You get the unit, the paragraph markers it sits under, and the section stem. \
Read them together. A unit that looks like a sentence fragment is often a real \
obligation, because the operative "shall" sits in the stem above it.

DECIDE FOUR THINGS

1. Is this a duty on the INSTITUTION? Not on FinCEN, OFAC, the NCUA, a Federal \
functional regulator or an ALJ. Agency procedure is not a bank obligation.

2. What is the duty, in one sentence, in the regulation's own terms?

3. What single assertion can be tested? One rule = one thing that can fail. If \
your IF/THEN needs "and", it is two rules — emit the one this unit is about.

4. What bank record would prove it? Choose from the list below. Never a CFR \
citation. Never "policy" or "documentation" alone — nobody can retrieve those.

RULES OF ENGAGEMENT

- Quote source_span VERBATIM from the unit text. Copy it exactly. If you cannot \
copy a span that supports your rule, set is_duty false.
- Never add a requirement you know from elsewhere. If this text does not say it, \
it is not here. Examination manuals and agency handbooks describe the law; they \
are not the law and they are not in front of you.
- Scaffolding is not a duty. "(2) Includes, at a minimum:" introduces duties; it \
is not one.
- Charter: under 31 CFR 1010.100(d) credit unions are "banks" for BSA purposes, \
so a 31 CFR Chapter X duty covers both unless the section says otherwise. Title \
12 parts are agency-specific — 12 CFR 748, 760, 701, 722, 723 are NCUA \
(credit unions); 339, 323, 326 are FDIC; 32, 21 are OCC (banks).
- rule_class: REGULATORY if the regulation states it. TECHNICAL if it asserts \
something about the testing platform. BUSINESS if it is a bank's own control \
choice that the regulation does not mandate.

EVIDENCE VOCABULARY: {", ".join(EVIDENCE_VOCAB)}

Return strict JSON, no prose, no fences:
{{"is_duty": true|false, "actor": "institution"|"agency",
  "obligation": "", "rule_logic": "IF ... THEN ...", "source_span": "",
  "evidence_source": "", "rule_class": "REGULATORY"|"BUSINESS"|"TECHNICAL",
  "applies_bank": "Y"|"N", "applies_credit_union": "Y"|"N",
  "criticality": "Critical"|"High"|"Medium", "confidence": 0.0}}"""


# Few-shot. The first example is load-bearing: without a noun-phrase case
# the model reproduces the regex failure and finds 1 duty in 1020.210
# where there are 7.
FEW_SHOT = [
    ("STEM: A bank regulated by a Federal functional regulator shall be deemed "
     "to satisfy the requirements of 31 U.S.C. 5318(h)(1) if it implements and "
     "maintains an anti-money laundering program that: (2) Includes, at a "
     "minimum:\nPARENT: (a) (2)\nUNIT: (iv) Training for appropriate personnel; and",
     {"is_duty": True, "actor": "institution",
      "obligation": "Provide training for appropriate personnel.",
      "rule_logic": "IF personnel are in scope for BSA training THEN completed "
                    "training must be evidenced for each.",
      "source_span": "Training for appropriate personnel",
      "evidence_source": "training record", "rule_class": "REGULATORY",
      "applies_bank": "Y", "applies_credit_union": "Y",
      "criticality": "Critical", "confidence": 0.93}),

    ("STEM: (a) Anti-money laundering program requirements for banks...\n"
     "PARENT: (a)\nUNIT: (2) Includes, at a minimum:",
     {"is_duty": False, "actor": "institution", "obligation": "",
      "rule_logic": "", "source_span": "", "evidence_source": "",
      "rule_class": "REGULATORY", "applies_bank": "Y",
      "applies_credit_union": "Y", "criticality": "Medium", "confidence": 0.95}),

    ("STEM: (b) Information requests based on credible evidence...\nPARENT: (b) (2)\n"
     "UNIT: (ii) FinCEN shall provide the information request to the financial "
     "institution.",
     {"is_duty": False, "actor": "agency", "obligation": "", "rule_logic": "",
      "source_span": "", "evidence_source": "", "rule_class": "REGULATORY",
      "applies_bank": "Y", "applies_credit_union": "Y",
      "criticality": "Medium", "confidence": 0.9}),

    ("STEM: (a) Combined general limit. A national bank's total outstanding "
     "loans and extensions of credit to one borrower may not exceed 15 percent "
     "of capital and surplus, plus an additional 10 percent if the amount that "
     "exceeds the 15 percent general limit is fully secured by readily "
     "marketable collateral.\nPARENT: (a)\nUNIT: (a) Combined general limit...",
     {"is_duty": True, "actor": "institution",
      "obligation": "Keep total credit to one borrower within 15 percent of "
                    "capital and surplus.",
      "rule_logic": "IF total outstanding credit to one borrower is measured "
                    "THEN it must not exceed 15 percent of capital and surplus.",
      "source_span": "may not exceed 15 percent of the bank's or savings "
                     "association's capital and surplus",
      "evidence_source": "loan system aggregate", "rule_class": "REGULATORY",
      "applies_bank": "Y", "applies_credit_union": "N",
      "criticality": "Critical", "confidence": 0.9}),
]


def _messages(unit: Unit, stem_text: str) -> list[dict]:
    msgs = []
    for user, answer in FEW_SHOT:
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": json.dumps(answer)})
    msgs.append({"role": "user", "content":
                 f"STEM: {stem_text}\nPARENT: {unit.parent}\n"
                 f"UNIT: {unit.marker} {unit.text}"})
    return msgs


def _parse(text: str) -> dict:
    return json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(),
                             flags=re.M).strip())


# ── transports ────────────────────────────────────────────────────────
def anthropic_api(model: str = "claude-sonnet-4-6",
                  api_key: Optional[str] = None) -> Callable:
    """Direct API. Temperature 0 — this is verification, not writing."""
    import requests
    key = api_key or os.environ["ANTHROPIC_API_KEY"]

    def call(unit: Unit, stem_text: str) -> dict:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": 900, "temperature": 0,
                  "system": SYSTEM, "messages": _messages(unit, stem_text)})
        r.raise_for_status()
        body = "".join(b.get("text", "") for b in r.json()["content"]
                       if b["type"] == "text")
        return _parse(body)
    return call


def bedrock(client, model_id: str = "anthropic.claude-sonnet-4-6-v1:0") -> Callable:
    """Bedrock, matching how the rest of fortress_server calls models."""
    def call(unit: Unit, stem_text: str) -> dict:
        resp = client.invoke_model(modelId=model_id, body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 900, "temperature": 0, "system": SYSTEM,
            "messages": _messages(unit, stem_text)}))
        payload = json.loads(resp["body"].read())
        body = "".join(b.get("text", "") for b in payload["content"]
                       if b["type"] == "text")
        return _parse(body)
    return call
