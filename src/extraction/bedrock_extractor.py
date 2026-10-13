"""Bedrock-backed structured extractor — Claude tool_use + Pydantic validation.

One generic `extract(schema, text, ...)` entrypoint that:
  1. Derives a JSON Schema from the Pydantic ExtractionResult subclass
     (minus our internal `extraction_method` field — the LLM doesn't set it).
  2. Forces Claude (via Bedrock) to call exactly one tool with that schema.
  3. Validates the tool input through Pydantic — invalid output (e.g.
     penalty > $50B) raises and is logged; the caller decides whether to
     fall back to regex or drop the field.

Why tool_use over JSON mode:
  Tool_use is the only Bedrock-Claude path that gives schema-enforced
  structured output. JSON mode (system prompt "return JSON") still
  produces stray markdown fences and prose preambles in ~5% of calls.

Why this is a separate module from BedrockService:
  BedrockService.generate_code() is shaped around plain-text generation
  (returns a str). Tool_use needs the full response envelope to find the
  tool_use content block, so we build a parallel `_invoke_with_tool()`.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

import boto3
import structlog
from botocore.config import Config
from pydantic import ValidationError

from src.extraction.models import ExtractionMethod, ExtractionResult

logger = structlog.get_logger(__name__)

# CloudWatch Embedded Metric Format namespace. Mirrors the pattern in
# compliance_rule_resolver.py — emitting structured _aws blocks lets CW
# Alarms watch the count without us standing up a custom metric pipeline.
_EMF_NAMESPACE = "Fortress/P1/Extraction"


def _emit_extraction_metric(
    metric_name: str,
    schema_name: str,
    dimensions: Optional[Dict[str, str]] = None,
    value: int = 1,
    unit: str = "Count",
) -> None:
    """Emit a CloudWatch EMF record for an extraction outcome.

    Call this on each terminal outcome (success, low_confidence,
    validation_failed, invoke_failed, regex_fallback). The structured
    `_aws` block is what `aws logs put-metric-filter` and CW Alarms
    consume — no other pipeline needed.

    Dimensions default to {Schema: <name>}; callers can add Regulator
    or Mode for finer-grained alarms.
    """
    dims = {"Schema": schema_name}
    if dimensions:
        dims.update(dimensions)
    logger.info(
        "metric_extraction",
        _aws={
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": _EMF_NAMESPACE,
                "Dimensions": [list(dims.keys())],
                "Metrics": [{"Name": metric_name, "Unit": unit}],
            }],
        },
        **dims,
        **{metric_name: value},
    )

T = TypeVar("T", bound=ExtractionResult)

# Haiku is the right default for single-field extraction: ~3x cheaper than
# Sonnet, latency under 2s, and accuracy on classification tasks matches
# Sonnet within 1-2pp on the golden set. Sonnet is reserved for tasks that
# require multi-step reasoning across long documents.
_DEFAULT_EXTRACTION_MODEL_ENV = "P1_OBLIGATION_EXTRACTION_MODEL"

# Fields we strip from the Pydantic-derived schema before sending to Claude
# — the LLM should not pick its own provenance.
_INTERNAL_FIELDS = ("extraction_method",)


def _schema_for_tool(schema_cls: Type[ExtractionResult]) -> Dict[str, Any]:
    """Derive a JSON Schema that Claude's tool_use can validate against."""
    js = schema_cls.model_json_schema()
    props = dict(js.get("properties", {}))
    required = [r for r in js.get("required", []) if r not in _INTERNAL_FIELDS]
    for f in _INTERNAL_FIELDS:
        props.pop(f, None)
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _mock_payload(schema_cls: Type[ExtractionResult]) -> Dict[str, Any]:
    """Deterministic fake when LLM_MOCK_MODE=true (local dev, CI tests).

    Returns the lowest-confidence valid payload — callers should treat
    LLM_MOCK_MODE results as 'extractor was bypassed', not 'no penalty
    found in document'.
    """
    fields = schema_cls.model_fields
    payload: Dict[str, Any] = {
        "confidence": 0.0,
        "source_span": "",
        "reasoning": "LLM_MOCK_MODE=true — extractor skipped",
    }
    if "amount_usd" in fields:
        payload["amount_usd"] = None
    if "is_penalty_not_asset" in fields:
        payload["is_penalty_not_asset"] = False
    return payload


class BedrockExtractor:
    """Stateful client; one per process is plenty.

    Reuses the same Config (read_timeout=600, retries=2) as BedrockService
    so a slow Sonnet call doesn't compound into a 5-minute retry storm.
    """

    def __init__(self, model_id: Optional[str] = None):
        from src.utils.config import settings
        import os
        self.region = os.getenv("AWS_REGION", settings.AWS_REGION or "us-east-1")
        # Resolution: explicit arg → P1_OBLIGATION_EXTRACTION_MODEL (Haiku) →
        # BEDROCK_MODEL (Sonnet) → boto default. Extraction is a Haiku-class
        # task; Sonnet is the safety net, not the default.
        self.model_id = (
            model_id
            or getattr(settings, _DEFAULT_EXTRACTION_MODEL_ENV, None)
            or settings.BEDROCK_MODEL
        )
        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=self.region,
            config=Config(
                read_timeout=600,
                connect_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        logger.info(
            "bedrock_extractor_initialized",
            region=self.region,
            model_id=self.model_id,
        )

    async def extract(
        self,
        schema_cls: Type[T],
        text: str,
        instructions: str,
        examples: Optional[List[Dict[str, Any]]] = None,
        tool_name: str = "record_extraction",
        tool_description: str = "Record the structured extraction result.",
        model_id: Optional[str] = None,
    ) -> T:
        """Run one extraction and return a validated Pydantic instance.

        Raises pydantic.ValidationError if the LLM returns a payload that
        violates the schema (e.g. penalty > $50B). Callers should catch
        and fall back to a deterministic default (None, regex, etc.).
        """
        from src.utils.config import settings

        if settings.LLM_MOCK_MODE:
            payload = _mock_payload(schema_cls)
            return schema_cls(extraction_method=ExtractionMethod.MOCK, **payload)

        tool = {
            "name": tool_name,
            "description": tool_description,
            "input_schema": _schema_for_tool(schema_cls),
        }
        user_msg = self._build_user_message(text, instructions, examples)
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "temperature": 0.0,
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": tool_name},
                "messages": [{"role": "user", "content": user_msg}],
            }
        )

        schema_name = schema_cls.__name__
        started = time.time()

        try:
            response = await asyncio.to_thread(
                self.client.invoke_model,
                modelId=model_id or self.model_id,
                body=body,
            )
            envelope = json.loads(response["body"].read())
        except Exception as exc:
            # Bedrock failure is an OPERATIONAL alert — distinct from a
            # schema validation miss (model behavior) or low confidence
            # (data shape). CW alarm "BedrockInvokeFailures >= 3 in 5min"
            # should page on this metric.
            logger.error(
                "extraction_invoke_failed",
                schema=schema_name,
                error=str(exc),
                error_class=type(exc).__name__,
                elapsed_ms=int((time.time() - started) * 1000),
                exc_info=True,
            )
            _emit_extraction_metric(
                "BedrockInvokeFailures", schema_name,
                dimensions={"ErrorClass": type(exc).__name__},
            )
            raise

        payload = self._unpack_tool_use(envelope, tool_name)
        try:
            result = schema_cls(
                extraction_method=ExtractionMethod.LLM, **payload
            )
        except ValidationError as ve:
            # Pydantic rejected the LLM's tool_use payload — usually a
            # schema-cap breach (penalty > $50B) or a typed-field shape
            # the model got wrong. CW alarm "ValidationFailures >= 5 in
            # 5min" flags model drift; callers fall back to regex.
            logger.error(
                "extraction_validation_failed",
                schema=schema_name,
                payload=payload,
                errors=ve.errors()[:5],
                elapsed_ms=int((time.time() - started) * 1000),
            )
            _emit_extraction_metric(
                "ValidationFailures", schema_name,
            )
            raise

        usage = envelope.get("usage") or {}
        logger.info(
            "extraction_succeeded",
            schema=schema_name,
            confidence=result.confidence,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _emit_extraction_metric("ExtractionSuccesses", schema_name)
        if usage.get("input_tokens"):
            _emit_extraction_metric(
                "BedrockInputTokens", schema_name,
                value=int(usage["input_tokens"]), unit="None",
            )
        if usage.get("output_tokens"):
            _emit_extraction_metric(
                "BedrockOutputTokens", schema_name,
                value=int(usage["output_tokens"]), unit="None",
            )
        return result

    @staticmethod
    def _build_user_message(
        text: str,
        instructions: str,
        examples: Optional[List[Dict[str, Any]]],
    ) -> str:
        parts = [instructions.strip(), ""]
        if examples:
            parts.append("Examples:")
            for i, ex in enumerate(examples, 1):
                parts.append(f"Example {i} input:")
                parts.append(ex["input"])
                parts.append(f"Example {i} output (call the tool with):")
                parts.append(json.dumps(ex["output"], indent=2))
                parts.append("")
        parts.append("Document to extract from:")
        parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _unpack_tool_use(envelope: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
        """Pluck the tool_use block from the Claude response envelope."""
        for block in envelope.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                return block.get("input") or {}
        raise RuntimeError(
            f"bedrock response had no tool_use block for {tool_name!r}: "
            f"stop_reason={envelope.get('stop_reason')}"
        )


_default_extractor: Optional[BedrockExtractor] = None


def get_extractor() -> BedrockExtractor:
    """Lazy singleton — defers boto3 client construction until first use."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = BedrockExtractor()
    return _default_extractor
