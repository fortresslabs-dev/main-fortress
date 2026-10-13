"""Penalty extraction — prompt + few-shot examples for PenaltyExtraction.

Kept separate from bedrock_extractor.py so other entity extractors
(ObligationExtraction, ActionTypeExtraction) can be added without
churning the generic extractor.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.extraction.bedrock_extractor import get_extractor
from src.extraction.models import PenaltyExtraction

_INSTRUCTIONS = """You are a U.S. banking-regulatory compliance analyst.

Extract the CIVIL MONETARY PENALTY (CMP) imposed by the regulator in the
document below. Penalty types that count:
  - civil money penalty, fine, monetary judgment
  - restitution, disgorgement, consumer redress
  - forfeiture ordered by the regulator

Dollar amounts that DO NOT count as penalties (return null + set
is_penalty_not_asset=false if these are the only dollar figures present):
  - total assets, total deposits, total revenue, market capitalization
  - holding-company size or affiliated-entity size
  - transaction volume, loan-portfolio size, suspicious-activity totals
  - amounts the consumer was overcharged (those are damages context, not a
    penalty — unless explicitly ordered as restitution by the regulator)

Rules:
  1. If multiple penalty figures are mentioned (e.g. "$1.5M CMP plus
     $300K restitution"), return the TOTAL of the punitive amounts.
  2. If the document describes the action but no penalty was imposed
     (consent order with no CMP, prohibition order, formal agreement),
     return amount_usd=null with is_penalty_not_asset=true and explain.
  3. amount_usd must be in plain USD (not millions / billions). Convert
     "$1.5 million" → 1500000.
  4. source_span MUST be a verbatim sentence (or short fragment) from the
     document — do not paraphrase. Empty string if amount_usd is null
     and no specific span anchors that decision.
  5. confidence should reflect ambiguity:
       0.95+ : explicit "ordered to pay $N civil money penalty"
       0.7-0.9 : penalty implied by context but worded loosely
       <0.5  : multiple plausible interpretations, no clear anchor

You MUST call the record_extraction tool exactly once. Do not reply in prose."""


_EXAMPLES: List[Dict[str, Any]] = [
    {
        "input": (
            "On January 14, 2025, the Bureau filed a lawsuit against Capital "
            "One, N.A., a national bank with over $480 billion in assets, "
            "and its parent holding company, Capital One Financial Corp., "
            "alleging the bank misled consumers about high-interest savings "
            "accounts."
        ),
        "output": {
            "amount_usd": None,
            "is_penalty_not_asset": False,
            "confidence": 0.92,
            "source_span": "a national bank with over $480 billion in assets",
            "reasoning": (
                "The $480 billion figure refers to the bank's total assets, "
                "not a penalty. The document describes the filing of a "
                "lawsuit but does not state any civil money penalty amount."
            ),
        },
    },
    {
        "input": (
            "The Office of the Comptroller of the Currency today assessed a "
            "$1,500,000 civil money penalty against XYZ Bank, N.A., for "
            "violations of the Bank Secrecy Act. The bank, which holds "
            "approximately $12 billion in assets, has agreed to pay the "
            "penalty and implement remedial measures."
        ),
        "output": {
            "amount_usd": 1500000,
            "is_penalty_not_asset": True,
            "confidence": 0.98,
            "source_span": (
                "assessed a $1,500,000 civil money penalty against XYZ Bank, "
                "N.A., for violations of the Bank Secrecy Act"
            ),
            "reasoning": (
                "Explicit civil money penalty of $1.5M. The $12B figure is "
                "the bank's assets and is correctly excluded."
            ),
        },
    },
    {
        "input": (
            "The Federal Reserve entered into a formal written agreement "
            "with ABC Bancorp requiring the holding company to strengthen "
            "board oversight and credit-risk management. The agreement does "
            "not impose any monetary penalty."
        ),
        "output": {
            "amount_usd": None,
            "is_penalty_not_asset": True,
            "confidence": 0.97,
            "source_span": "The agreement does not impose any monetary penalty.",
            "reasoning": (
                "Formal written agreement with no civil money penalty. "
                "Document explicitly states no monetary penalty was imposed."
            ),
        },
    },
]


async def extract_penalty(text: str) -> PenaltyExtraction:
    """Extract penalty amount from enforcement-action prose."""
    return await get_extractor().extract(
        schema_cls=PenaltyExtraction,
        text=text,
        instructions=_INSTRUCTIONS,
        examples=_EXAMPLES,
        tool_name="record_extraction",
        tool_description=(
            "Record the extracted civil money penalty amount and provenance."
        ),
    )
