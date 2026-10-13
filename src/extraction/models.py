"""Pydantic schemas for structured extraction results.

The shapes here are the contract between the Bedrock extractor (tool_use
output) and downstream callers. Every extraction carries `confidence`,
`extraction_method`, and `source_span` so:

  - low-confidence results can be dropped or routed to human review,
  - regressions can be audited by re-reading the exact source sentence,
  - mock/regex/LLM extractions are tagged so eval harness can split scores.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExtractionMethod(str, Enum):
    LLM = "llm"
    REGEX = "regex"
    HARDCODED = "hardcoded"
    MOCK = "mock"


# Cap on plausible regulatory penalties. The largest banking penalty on
# record is BNP Paribas ($8.97B, 2014). Anything above $50B is almost
# certainly an asset/revenue/market-cap figure miscategorised as a fine.
MAX_PLAUSIBLE_PENALTY_USD = Decimal("50000000000")


class ExtractionResult(BaseModel):
    """Base for every structured extraction. Subclasses add typed fields."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Extractor's self-rated confidence; LLM returns this, "
                    "regex/hardcoded default to 1.0.",
    )
    extraction_method: ExtractionMethod = Field(
        default=ExtractionMethod.LLM,
        description="Which extractor produced this result.",
    )
    source_span: str = Field(
        default="",
        max_length=1000,
        description="Verbatim sentence/snippet the value was lifted from. "
                    "Empty when no specific span applies (e.g. 'no penalty found').",
    )
    reasoning: str = Field(
        default="",
        max_length=2000,
        description="Short explanation of why this value was chosen — "
                    "especially important for null/ambiguous cases.",
    )


class PenaltyExtraction(ExtractionResult):
    """Monetary penalty (civil money penalty, restitution, fine, judgment).

    `amount_usd` is None when:
      - the document discusses the action but no dollar penalty was imposed
        (consent order with no CMP, prohibition, formal agreement), OR
      - every dollar amount in the text is contextually NOT a penalty
        (assets, revenue, market cap, holding-company size).

    The asset-confusion case is the failure mode that motivated this whole
    redesign — see `tests/golden/extraction/enforcement_penalty.jsonl::capital_one_assets`.
    """

    amount_usd: Optional[Decimal] = Field(
        default=None,
        description="USD penalty amount. None if no penalty is imposed or "
                    "the only dollar figures refer to non-penalty quantities.",
    )
    is_penalty_not_asset: bool = Field(
        default=True,
        description="True iff the extracted amount is genuinely a penalty "
                    "(not assets, revenue, market cap, etc.). When False, "
                    "amount_usd MUST be None.",
    )

    @field_validator("amount_usd")
    @classmethod
    def _cap_and_sign(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is None:
            return None
        if v < 0:
            raise ValueError("penalty amount cannot be negative")
        if v > MAX_PLAUSIBLE_PENALTY_USD:
            raise ValueError(
                f"penalty {v} exceeds plausible cap "
                f"{MAX_PLAUSIBLE_PENALTY_USD} — likely an asset/revenue figure"
            )
        return v


# Rule-level thresholds (CTR $10K, SAR $5K, wire $3K, etc.) live in a
# different range than enforcement penalties. The largest rule threshold
# we expect is the CRA asset-size band ($1B–$2B); anything higher is
# almost certainly an enforcement figure or asset distractor that landed
# in the wrong extractor.
MAX_PLAUSIBLE_THRESHOLD_USD = Decimal("2000000000")


ObligationTypeStr = Literal[
    "shall",
    "must",
    "required",
    "prohibited",
    "permissive",
    "none",
]


JurisdictionLevelStr = Literal["federal", "state", "internal", "unknown"]


class MonetaryThreshold(BaseModel):
    """A dollar threshold the rule imposes (e.g. CTR $10,000, SAR $5,000).

    NOT a penalty — penalties live in PenaltyExtraction. Use this for
    threshold-like figures referenced INSIDE the rule_text itself.
    """

    model_config = ConfigDict(extra="forbid")

    amount_usd: Decimal = Field(
        ...,
        description="Plain USD threshold (no millions/billions shorthand).",
    )
    context: str = Field(
        ...,
        max_length=200,
        description="What the threshold gates — e.g. 'CTR filing', "
                    "'wire dual approval', 'beneficial-ownership reporting'.",
    )

    @field_validator("amount_usd")
    @classmethod
    def _bounded(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("threshold cannot be negative")
        if v > MAX_PLAUSIBLE_THRESHOLD_USD:
            raise ValueError(
                f"threshold {v} exceeds plausible cap "
                f"{MAX_PLAUSIBLE_THRESHOLD_USD} — likely a penalty/asset figure"
            )
        return v


class ObligationItem(BaseModel):
    """One obligation sentence the rule imposes.

    Mirrors the columns the legacy regex pipeline writes into the
    obligations table (source_text + obligation_type + cfr_citation +
    section_reference) so the LLM-first path is a drop-in replacement.
    """

    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Verbatim sentence from rule_text that carries the duty.",
    )
    obligation_type: ObligationTypeStr = Field(
        ...,
        description="Verb-class of the duty. 'permissive' for 'may'/'is "
                    "permitted to'; 'none' for descriptive sentences that "
                    "slipped in but carry no duty.",
    )
    cfr_citation: Optional[str] = Field(
        default=None,
        max_length=200,
        description="CFR cite this sentence anchors to, if explicit. Null "
                    "when the rule-level cfr_citation_primary already covers it.",
    )
    section_reference: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Bare section reference (e.g. '1010.230(a)').",
    )
    has_monetary_threshold: bool = Field(
        default=False,
        description="True iff this sentence contains a dollar threshold "
                    "(cross-reference with monetary_thresholds list).",
    )


class RuleExtraction(ExtractionResult):
    """Structured view of a single ComplianceRule.rule_text.

    Subsumes the obligation_extractor regex pass: one Bedrock call yields
    the rule-level citations + jurisdiction + thresholds AND the
    per-sentence obligations list, all anchored to the same source span.

    Null semantics:
      - `cfr_citation_primary = None` is a VALID, common outcome (state
        rules, internal policies, FFIEC prose without inline cite). The
        eval gate measures recall on rules that DO have a cite.
      - `obligations = []` means the rule_text is descriptive only (no
        duty-bearing sentences). Rare for active regulatory rules.
    """

    cfr_citation_primary: Optional[str] = Field(
        default=None,
        max_length=200,
        description="The single most authoritative CFR cite for the rule. "
                    "Null when no CFR applies (state, internal, OFAC prose).",
    )
    cfr_citations_all: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Every CFR cite found in the rule_text (deduplicated).",
    )
    usc_citations: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="USC cites (e.g. '31 U.S.C. § 5318') found in the rule_text.",
    )
    section_references: List[str] = Field(
        default_factory=list,
        max_length=30,
        description="Bare section refs not already covered by cfr_citations_all.",
    )
    jurisdiction_level: JurisdictionLevelStr = Field(
        default="unknown",
        description="federal=CFR/USC cite present; state=state code or "
                    "state-regulator wording; internal=Fortress-authored; "
                    "unknown=insufficient signal.",
    )
    state_code: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Two-letter state code (or compound like 'NY-DFS') when "
                    "jurisdiction_level=state.",
    )
    monetary_thresholds: List[MonetaryThreshold] = Field(
        default_factory=list,
        max_length=10,
        description="Dollar thresholds the rule itself imposes (NOT penalties).",
    )
    obligations: List[ObligationItem] = Field(
        default_factory=list,
        max_length=50,
        description="Per-sentence duty-bearing extractions. Empty for "
                    "descriptive-only rule_text.",
    )

    @model_validator(mode="after")
    def _state_code_consistency(self) -> "RuleExtraction":
        if self.state_code and self.jurisdiction_level != "state":
            raise ValueError(
                "state_code set but jurisdiction_level != 'state'"
            )
        return self
