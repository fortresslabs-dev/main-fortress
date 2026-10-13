"""Rule extraction — prompt + few-shot examples for RuleExtraction.

One Bedrock call per ComplianceRule.rule_text produces:
  - rule-level citations (CFR + USC + bare sections)
  - jurisdiction + optional state code
  - dollar thresholds the rule imposes (NOT penalties)
  - per-sentence obligations with verb-class + scoped citation

This subsumes the regex paths in:
  - src/services/obligation_extractor.py (_CFR_RE, _SEC_RE, _OBLIGATION_TRIGGERS, _classify_obligation_type)
  - src/connectors/regulatory/usa/_scraper_utils.py (enrich_document citation regex)
  - scripts/backfill_cfr_citations.py (recall-3.6% regex)

The legacy regex paths stay in place as fallback (Bedrock outage, low
confidence, schema validation failure) — see obligation_extractor.run().
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.extraction.bedrock_extractor import get_extractor
from src.extraction.models import RuleExtraction

_INSTRUCTIONS = """You are a U.S. banking-regulatory compliance analyst.

You are given the raw rule_text of a single ComplianceRule plus the
regulator that scraped it. Extract a structured view per the tool
schema.

What to extract
---------------
1. cfr_citation_primary — the SINGLE most authoritative CFR cite for
   this rule. Prefer cites that appear verbatim in rule_text. If the
   text names a statute by acronym (HMDA, ECOA, BSA, FCRA, GLBA, etc.)
   without a literal cite, you MAY infer the canonical CFR cite — but
   lower confidence to 0.6-0.8 to flag that inference.
   Return null when:
     - The rule is state-level (NYDFS, CA-DFPI, TX-DOB, FL-OFR, IL-IDFPR).
     - The rule is internal Fortress analytics with no external cite.
     - The text is descriptive prose with no statutory anchor (some
       FFIEC handbook material, OFAC procedural notes).
   Do NOT invent a cite to make the field non-null. A null is the
   correct answer for a genuinely cite-less rule.

2. cfr_citations_all — every distinct CFR cite that appears in the
   text. Include cfr_citation_primary. Deduplicate. Preserve the
   formatting the text uses ("12 C.F.R. Part 25" stays as-is — do not
   normalize to "12 CFR 25").

3. usc_citations — every distinct U.S.C. cite verbatim from the text
   (e.g. "31 U.S.C. § 5318"). Do NOT infer USC cites from statute
   names — only include if literally present.

4. section_references — bare section refs ("1010.230(a)", "Part 504")
   that are NOT already covered by cfr_citations_all.

5. jurisdiction_level —
     federal: federal regulator (OCC, FDIC, FRB, CFPB, FFIEC, FINCEN,
              OFAC, CRA) — even if no CFR cite is found.
     state:   state regulator (NYDFS, CA-DFPI, TX-DOB, FL-OFR, IL-IDFPR,
              etc.) OR rule_text references state statute / state code.
     internal: regulator is Fortress / internal analytics.
     unknown: insufficient signal.

6. state_code — set ONLY when jurisdiction_level=state. Two-letter or
   compound ("NY", "NY-DFS", "CA", "CA-DFPI"). Match what the connector
   emits when known.

7. monetary_thresholds — DOLLAR amounts the rule itself imposes as
   thresholds (CTR $10K, SAR $5K, wire $3K, BSA threshold). Convert
   shorthand ("$10,000", "$10K") to plain integer dollars.
   Do NOT include:
     - percentages ("20 percent ownership") — those aren't dollar amounts
     - penalty amounts (those belong in PenaltyExtraction, not here)
     - asset-size / deposit / market-cap distractors

8. obligations — per-sentence list of duty-bearing sentences. Each entry:
     source_text:        verbatim sentence (no paraphrase)
     obligation_type:    shall | must | required | prohibited | permissive | none
                         Use the strongest verb-class in the sentence.
                         "may not" / "shall not" / "prohibited from" → prohibited.
                         "may" / "is permitted" → permissive.
                         "should" / descriptive → none (skip unless duty-bearing).
     cfr_citation:       cite SPECIFIC to this sentence if different
                         from cfr_citation_primary (else null).
     section_reference:  bare section ref if anchored in this sentence.
     has_monetary_threshold: true iff a dollar threshold appears in
                         this sentence.
   Skip sentences with no duty (descriptive, KPI definitions, formulas).

9. confidence — calibration:
     0.90-1.00 : every field anchored to verbatim text
     0.70-0.89 : cfr inferred from statute acronym, or obligation parsing required judgement
     0.50-0.69 : ambiguous regulator or noisy rule_text
     <0.50    : the caller will drop this result and fall back to regex

10. source_span — pick ONE short verbatim span from the text that best
    anchors the primary citation OR primary obligation. Empty string only
    if the text is empty.

11. reasoning — one sentence: which fields were verbatim, which were
    inferred, and why null fields are null.

Output rules
------------
- Call the record_rule_extraction tool exactly once.
- Do not reply in prose.
- Empty lists are valid (cfr_citations_all=[], obligations=[]).
- state_code MUST be null when jurisdiction_level != "state"."""


_EXAMPLES: List[Dict[str, Any]] = [
    # Example 1 — federal OCC, verbatim CFR cite, multi-obligation,
    # percentage (NOT a dollar threshold).
    {
        "input": {
            "regulator": "OCC",
            "rule_text": (
                "Covered financial institutions must establish and maintain "
                "written customer due diligence procedures. Institutions "
                "must identify and verify the identity of beneficial owners "
                "with 20 percent or more ownership stake in legal entity "
                "customers, per FinCEN amendment effective April 11, 2026. "
                "Ongoing monitoring of customer activity is required to "
                "detect and report suspicious transactions. "
                "31 C.F.R. \u00a7 1010.230; OCC BSA/AML Examination Manual."
            ),
        },
        "output": {
            "confidence": 0.96,
            "source_span": "31 C.F.R. \u00a7 1010.230",
            "reasoning": (
                "Cite '31 C.F.R. § 1010.230' is verbatim at the end of the "
                "rule_text. Three duty-bearing sentences extracted. '20 "
                "percent' is a percentage, not a dollar threshold — "
                "monetary_thresholds is empty."
            ),
            "cfr_citation_primary": "31 C.F.R. \u00a7 1010.230",
            "cfr_citations_all": ["31 C.F.R. \u00a7 1010.230"],
            "usc_citations": [],
            "section_references": [],
            "jurisdiction_level": "federal",
            "state_code": None,
            "monetary_thresholds": [],
            "obligations": [
                {
                    "source_text": (
                        "Covered financial institutions must establish and "
                        "maintain written customer due diligence procedures."
                    ),
                    "obligation_type": "must",
                    "cfr_citation": None,
                    "section_reference": None,
                    "has_monetary_threshold": False,
                },
                {
                    "source_text": (
                        "Institutions must identify and verify the identity "
                        "of beneficial owners with 20 percent or more "
                        "ownership stake in legal entity customers, per "
                        "FinCEN amendment effective April 11, 2026."
                    ),
                    "obligation_type": "must",
                    "cfr_citation": None,
                    "section_reference": None,
                    "has_monetary_threshold": False,
                },
                {
                    "source_text": (
                        "Ongoing monitoring of customer activity is required "
                        "to detect and report suspicious transactions."
                    ),
                    "obligation_type": "required",
                    "cfr_citation": None,
                    "section_reference": None,
                    "has_monetary_threshold": False,
                },
            ],
        },
    },
    # Example 2 — FFIEC handbook prose, NO CFR cite anywhere, jurisdiction
    # still federal. Demonstrates that null is the CORRECT answer.
    {
        "input": {
            "regulator": "FFIEC",
            "rule_text": (
                "Institution must complete Inherent Risk Profile (IRP) "
                "assessing five categories: Technologies & Connection "
                "Types, Delivery Channels, Online/Mobile Products & "
                "Technology Services, Organizational Characteristics, "
                "External Threats. Result: Least / Minimal / Moderate / "
                "Significant / Most inherent risk."
            ),
        },
        "output": {
            "confidence": 0.88,
            "source_span": (
                "Institution must complete Inherent Risk Profile (IRP)"
            ),
            "reasoning": (
                "FFIEC handbook material — no CFR or USC cite is present in "
                "the text. Returning null cfr_citation_primary; FFIEC is a "
                "federal interagency body so jurisdiction_level=federal. "
                "One duty-bearing sentence; the categories list is "
                "descriptive."
            ),
            "cfr_citation_primary": None,
            "cfr_citations_all": [],
            "usc_citations": [],
            "section_references": [],
            "jurisdiction_level": "federal",
            "state_code": None,
            "monetary_thresholds": [],
            "obligations": [
                {
                    "source_text": (
                        "Institution must complete Inherent Risk Profile "
                        "(IRP) assessing five categories: Technologies & "
                        "Connection Types, Delivery Channels, Online/Mobile "
                        "Products & Technology Services, Organizational "
                        "Characteristics, External Threats."
                    ),
                    "obligation_type": "must",
                    "cfr_citation": None,
                    "section_reference": None,
                    "has_monetary_threshold": False,
                },
            ],
        },
    },
    # Example 3 — CFPB rule that names statutes by acronym (ECOA, Fair
    # Housing Act). LLM may infer the canonical CFR cite at LOWER
    # confidence — demonstrates the inference path.
    {
        "input": {
            "regulator": "CFPB",
            "rule_text": (
                "Banks must conduct comparative file review and statistical "
                "analysis of HMDA loan data to detect disparate treatment or "
                "disparate impact across protected classes. Peer denial "
                "rates by MSA and applicant demographics must be compared "
                "against peer institutions per ECOA and Fair Housing Act."
            ),
        },
        "output": {
            "confidence": 0.74,
            "source_span": (
                "comparative file review and statistical analysis of HMDA "
                "loan data"
            ),
            "reasoning": (
                "Text names HMDA, ECOA, Fair Housing Act by acronym only — "
                "no verbatim CFR or USC cite. Inferring HMDA → 12 C.F.R. "
                "Part 1003 as the primary cite since the rule's principal "
                "subject is HMDA data analysis. Confidence reduced to 0.74 "
                "to flag the inference. usc_citations stays empty (no "
                "literal USC text present)."
            ),
            "cfr_citation_primary": "12 C.F.R. Part 1003",
            "cfr_citations_all": [
                "12 C.F.R. Part 1003",
                "12 C.F.R. Part 1002",
            ],
            "usc_citations": [],
            "section_references": [],
            "jurisdiction_level": "federal",
            "state_code": None,
            "monetary_thresholds": [],
            "obligations": [
                {
                    "source_text": (
                        "Banks must conduct comparative file review and "
                        "statistical analysis of HMDA loan data to detect "
                        "disparate treatment or disparate impact across "
                        "protected classes."
                    ),
                    "obligation_type": "must",
                    "cfr_citation": "12 C.F.R. Part 1003",
                    "section_reference": None,
                    "has_monetary_threshold": False,
                },
                {
                    "source_text": (
                        "Peer denial rates by MSA and applicant demographics "
                        "must be compared against peer institutions per "
                        "ECOA and Fair Housing Act."
                    ),
                    "obligation_type": "must",
                    "cfr_citation": "12 C.F.R. Part 1002",
                    "section_reference": None,
                    "has_monetary_threshold": False,
                },
            ],
        },
    },
    # Example 4 — NYDFS Part 504 state rule, no CFR. Demonstrates
    # jurisdiction_level=state, state_code populated, section_references
    # for "Part 504".
    {
        "input": {
            "regulator": "NYDFS",
            "rule_text": (
                "Part 504 TM program not validated annually | Transaction "
                "monitoring program last validated 14 months ago | April 15 "
                "certification deadline approached | System blocks "
                "certification: 'TM program validation overdue; annual "
                "validation required before certification'"
            ),
        },
        "output": {
            "confidence": 0.91,
            "source_span": (
                "Part 504 TM program not validated annually"
            ),
            "reasoning": (
                "NYDFS Part 504 is a state regulation (New York Codes, "
                "Rules, and Regulations) — no federal CFR cite applies. "
                "jurisdiction_level=state, state_code=NY. 'Part 504' is the "
                "section reference. One duty-bearing sentence (annual "
                "validation required); the rest describe the trigger/system "
                "behavior."
            ),
            "cfr_citation_primary": None,
            "cfr_citations_all": [],
            "usc_citations": [],
            "section_references": ["Part 504"],
            "jurisdiction_level": "state",
            "state_code": "NY",
            "monetary_thresholds": [],
            "obligations": [
                {
                    "source_text": (
                        "Annual validation required before certification."
                    ),
                    "obligation_type": "required",
                    "cfr_citation": None,
                    "section_reference": "Part 504",
                    "has_monetary_threshold": False,
                },
            ],
        },
    },
    # Example 5 — FinCEN with CTR + SAR thresholds intertwined with
    # obligations. Demonstrates threshold extraction + multi-cite + the
    # has_monetary_threshold flag on obligations.
    {
        "input": {
            "regulator": "FINCEN",
            "rule_text": (
                "Financial institutions shall file a Currency Transaction "
                "Report (CTR) for each transaction in currency of more than "
                "$10,000 in a single business day under 31 C.F.R. \u00a7 "
                "1010.311. A Suspicious Activity Report (SAR) must be filed "
                "within 30 days of initial detection for transactions of "
                "$5,000 or more that the institution knows, suspects, or has "
                "reason to suspect involve funds derived from illegal "
                "activity, per 31 C.F.R. \u00a7 1020.320."
            ),
        },
        "output": {
            "confidence": 0.97,
            "source_span": (
                "file a Currency Transaction Report (CTR) for each "
                "transaction in currency of more than $10,000"
            ),
            "reasoning": (
                "Two verbatim CFR cites (CTR at 1010.311, SAR at 1020.320). "
                "Primary is the CTR cite — it appears first and anchors the "
                "principal duty. Two dollar thresholds extracted ($10K CTR, "
                "$5K SAR). Two duty-bearing sentences, each flagged with "
                "has_monetary_threshold=true."
            ),
            "cfr_citation_primary": "31 C.F.R. \u00a7 1010.311",
            "cfr_citations_all": [
                "31 C.F.R. \u00a7 1010.311",
                "31 C.F.R. \u00a7 1020.320",
            ],
            "usc_citations": [],
            "section_references": [],
            "jurisdiction_level": "federal",
            "state_code": None,
            "monetary_thresholds": [
                {"amount_usd": "10000", "context": "CTR filing"},
                {"amount_usd": "5000", "context": "SAR filing"},
            ],
            "obligations": [
                {
                    "source_text": (
                        "Financial institutions shall file a Currency "
                        "Transaction Report (CTR) for each transaction in "
                        "currency of more than $10,000 in a single business "
                        "day under 31 C.F.R. \u00a7 1010.311."
                    ),
                    "obligation_type": "shall",
                    "cfr_citation": "31 C.F.R. \u00a7 1010.311",
                    "section_reference": None,
                    "has_monetary_threshold": True,
                },
                {
                    "source_text": (
                        "A Suspicious Activity Report (SAR) must be filed "
                        "within 30 days of initial detection for "
                        "transactions of $5,000 or more that the "
                        "institution knows, suspects, or has reason to "
                        "suspect involve funds derived from illegal "
                        "activity, per 31 C.F.R. \u00a7 1020.320."
                    ),
                    "obligation_type": "must",
                    "cfr_citation": "31 C.F.R. \u00a7 1020.320",
                    "section_reference": None,
                    "has_monetary_threshold": True,
                },
            ],
        },
    },
]


async def extract_rule(
    rule_text: str,
    regulator: Optional[str] = None,
) -> RuleExtraction:
    """Extract the structured view of one ComplianceRule.rule_text.

    Args:
        rule_text: The raw rule_text column from compliance_rules.
        regulator: The connector that scraped it (OCC, FFIEC, NYDFS, etc.).
                   Used as a hint for jurisdiction + state_code disambiguation.
                   Optional — extraction works without it but precision drops.

    Returns:
        A validated RuleExtraction. May have confidence < 0.5, which the
        caller should treat as a signal to fall back to regex.

    Raises:
        pydantic.ValidationError: schema violations (caller should fall back).
        RuntimeError / botocore exceptions: Bedrock failure (caller should
            fall back to regex).
    """
    payload = {"regulator": regulator or "UNKNOWN", "rule_text": rule_text}
    # The generic extractor expects `text` as a string — we serialize the
    # regulator hint inline so it shows up in the model's context window
    # alongside the rule_text.
    body = (
        f"Regulator: {payload['regulator']}\n"
        f"---\n"
        f"{payload['rule_text']}"
    )
    return await get_extractor().extract(
        schema_cls=RuleExtraction,
        text=body,
        instructions=_INSTRUCTIONS,
        examples=[
            {
                "input": (
                    f"Regulator: {ex['input']['regulator']}\n"
                    f"---\n"
                    f"{ex['input']['rule_text']}"
                ),
                "output": ex["output"],
            }
            for ex in _EXAMPLES
        ],
        tool_name="record_rule_extraction",
        tool_description=(
            "Record the structured view of one compliance rule — "
            "citations, jurisdiction, thresholds, and per-sentence obligations."
        ),
    )
