"""LLM-backed structured extraction with Pydantic schema validation.

Replaces ad-hoc regex extractors in scrapers / connectors. Each extractor
returns an ExtractionResult subclass with `extraction_method` + `confidence`
+ `source_span` so downstream code can gate writes (e.g. drop low-confidence
penalty amounts) and audits can trace any value back to the prose that
produced it.
"""
from src.extraction.models import (
    ExtractionMethod,
    ExtractionResult,
    MonetaryThreshold,
    ObligationItem,
    PenaltyExtraction,
    RuleExtraction,
)

__all__ = [
    "ExtractionMethod",
    "ExtractionResult",
    "MonetaryThreshold",
    "ObligationItem",
    "PenaltyExtraction",
    "RuleExtraction",
]
