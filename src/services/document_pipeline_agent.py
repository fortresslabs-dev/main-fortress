"""
Document Pipeline Agent — reads compliance_sources.yaml and usa.yaml source_urls,
decides document type and scraping/chunking strategy for each URL, then orchestrates
DocumentProcessor to produce chunks ready for Postgres and S3 chunk export.

This is the "smart planning" layer that:
  1. Reads compliance_sources.yaml for format hints and field specs
  2. Reads usa.yaml source_urls for the actual URLs
  3. For each URL, builds a processing plan (type detection, chunking strategy)
  4. Executes via DocumentProcessor
  5. Returns structured results + quality assessments

Usage:
    from src.services.document_pipeline_agent import DocumentPipelineAgent

    agent = DocumentPipelineAgent()
    plan = agent.plan()           # inspect what will be fetched and how
    results = agent.execute()     # run the full pipeline
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
import structlog

from src.services.document_processor import DocType, DocumentProcessor

logger = structlog.get_logger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_COMPLIANCE_SOURCES = _CONFIG_DIR / "compliance_sources.yaml"
_CONNECTORS_CONFIG = _CONFIG_DIR / "connectors" / "usa.yaml"


@dataclass
class ProcessingPlan:
    """Plan for processing a single URL."""
    url: str
    source_id: str
    regulator: str
    category: str
    format_hint: str  # from compliance_sources.yaml or auto-detected
    detected_type: str  # actual detection result
    chunking_strategy: str  # heading, title, row, element
    fields: List[str] = field(default_factory=list)
    priority: int = 3
    notes: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Result from processing a single URL."""
    source_id: str
    url: str
    doc_type: str
    chunks: int
    duration_seconds: float
    quality_notes: List[str] = field(default_factory=list)
    error: Optional[str] = None


class DocumentPipelineAgent:
    """Plans and executes document processing for all compliance sources."""

    def __init__(
        self,
        use_paid_pdf_api: bool = False,
        chunk_size: int = 2000,
        sources_filter: Optional[List[str]] = None,
    ):
        """
        Args:
            use_paid_pdf_api: Enable paid API fallback for PDFs with tables/images.
            chunk_size: Max chunk size in characters.
            sources_filter: If set, only process these source_ids.
        """
        self.processor = DocumentProcessor(
            chunk_size=chunk_size,
            use_paid_pdf_api=use_paid_pdf_api,
        )
        self.sources_filter = sources_filter
        self._compliance_sources = self._load_compliance_sources()
        self._source_urls = self._load_source_urls()

    # ── Public API ──────────────────────────────────────────────────

    def plan(self) -> List[ProcessingPlan]:
        """Generate processing plans without executing anything.

        Useful for dry-run inspection: what format, what strategy, what priority.
        """
        plans: List[ProcessingPlan] = []

        # Build a merged source map:  source_id → (yaml metadata, list of URLs)
        merged = self._merge_sources()

        for source_id, info in merged.items():
            if self.sources_filter and source_id not in self.sources_filter:
                continue

            for url in info["urls"]:
                format_hint = info.get("format", "")
                detected = self.processor.detect_type(url)
                strategy = self._pick_chunking_strategy(detected, format_hint)
                notes = self._quality_notes(detected, format_hint, url)

                plans.append(ProcessingPlan(
                    url=url,
                    source_id=source_id,
                    regulator=info.get("regulator", ""),
                    category=info.get("category", ""),
                    format_hint=format_hint,
                    detected_type=detected.value,
                    chunking_strategy=strategy,
                    fields=info.get("fields", []),
                    priority=info.get("priority", 3),
                    notes=notes,
                ))

        # Sort by priority (1 = highest)
        plans.sort(key=lambda p: p.priority)
        return plans

    def execute(
        self, plans: Optional[List[ProcessingPlan]] = None
    ) -> List[PipelineResult]:
        """Execute processing plans and return results.

        If no plans provided, calls self.plan() first.
        Rate-limits between URLs to avoid CPU/network saturation.
        """
        if plans is None:
            plans = self.plan()

        results: List[PipelineResult] = []

        # Get request delay for rate limiting between URLs
        try:
            from src.utils.config import settings
            request_delay = settings.SCRAPER_REQUEST_DELAY
        except Exception:
            import os
            request_delay = float(os.getenv("SCRAPER_REQUEST_DELAY", "1.0"))

        logger.info("pipeline_execution_started", total_urls=len(plans))

        for idx, plan in enumerate(plans):
            # Rate limit: sleep between URLs (skip before the first one)
            if idx > 0 and request_delay > 0:
                time.sleep(request_delay)

            t0 = time.monotonic()
            try:
                chunks = self.processor.process_url(
                    url=plan.url,
                    source=plan.regulator,
                    category=plan.category,
                    format_hint=plan.detected_type,
                )
                elapsed = time.monotonic() - t0

                quality = list(plan.notes)
                if not chunks:
                    quality.append("WARNING: zero chunks extracted")

                results.append(PipelineResult(
                    source_id=plan.source_id,
                    url=plan.url,
                    doc_type=plan.detected_type,
                    chunks=len(chunks),
                    duration_seconds=round(elapsed, 2),
                    quality_notes=quality,
                ))

                logger.info(
                    "source_processed",
                    source_id=plan.source_id,
                    url=plan.url,
                    doc_type=plan.detected_type,
                    chunks=len(chunks),
                    elapsed=round(elapsed, 2),
                )

            except Exception as exc:
                elapsed = time.monotonic() - t0
                results.append(PipelineResult(
                    source_id=plan.source_id,
                    url=plan.url,
                    doc_type=plan.detected_type,
                    chunks=0,
                    duration_seconds=round(elapsed, 2),
                    error=str(exc),
                ))
                logger.error(
                    "source_processing_failed",
                    source_id=plan.source_id,
                    url=plan.url,
                    error=str(exc),
                )

        # Summary
        total_chunks = sum(r.chunks for r in results)
        errors = [r for r in results if r.error]
        logger.info(
            "pipeline_execution_complete",
            total_sources=len(results),
            total_chunks=total_chunks,
            errors=len(errors),
        )

        return results

    def report(self, results: Optional[List[PipelineResult]] = None) -> str:
        """Generate a human-readable report of a pipeline run."""
        if results is None:
            results = self.execute()

        lines = ["=== Document Pipeline Report ===", ""]
        for r in results:
            status = "ERROR" if r.error else "OK"
            lines.append(
                f"[{status}] {r.source_id:25s} | {r.doc_type:5s} | "
                f"{r.chunks:4d} chunks | {r.duration_seconds:.1f}s"
            )
            if r.error:
                lines.append(f"         Error: {r.error}")
            for note in r.quality_notes:
                lines.append(f"         Note: {note}")

        total = sum(r.chunks for r in results)
        errs = sum(1 for r in results if r.error)
        lines.append("")
        lines.append(f"Total: {len(results)} sources, {total} chunks, {errs} errors")
        return "\n".join(lines)

    # ── Config Loaders ──────────────────────────────────────────────

    @staticmethod
    def _load_compliance_sources() -> List[Dict[str, Any]]:
        """Load config/compliance_sources.yaml."""
        if not _COMPLIANCE_SOURCES.exists():
            logger.warning("compliance_sources_not_found", path=str(_COMPLIANCE_SOURCES))
            return []
        with open(_COMPLIANCE_SOURCES) as f:
            data = yaml.safe_load(f) or {}
        return data.get("sources", [])

    @staticmethod
    def _load_source_urls() -> Dict[str, Dict[str, str]]:
        """Load source_urls section from config/connectors/usa.yaml."""
        if not _CONNECTORS_CONFIG.exists():
            logger.warning("usa_config_not_found", path=str(_CONNECTORS_CONFIG))
            return {}
        with open(_CONNECTORS_CONFIG) as f:
            data = yaml.safe_load(f) or {}
        return data.get("source_urls", {})

    # ── Merging Logic ───────────────────────────────────────────────

    def _merge_sources(self) -> Dict[str, Dict[str, Any]]:
        """Merge compliance_sources.yaml definitions with usa.yaml URLs.

        Strategy:
          1. Start with compliance_sources.yaml entries (have format, fields, priority)
          2. Add any usa.yaml URLs that aren't already covered
          3. Result: source_id → { regulator, category, format, priority, fields, urls[] }
        """
        merged: Dict[str, Dict[str, Any]] = {}

        # 1. From compliance_sources.yaml (authoritative format/field metadata)
        for src in self._compliance_sources:
            sid = src.get("source_id", "")
            if not sid:
                continue
            merged[sid] = {
                "regulator": src.get("regulator", ""),
                "category": src.get("category", ""),
                "format": src.get("format", ""),
                "priority": src.get("priority", 3),
                "fields": src.get("fields", []),
                "urls": [src["url"]] if src.get("url") else [],
            }

        # 2. From usa.yaml source_urls (may have extra URLs not in compliance_sources)
        for connector_name, url_map in self._source_urls.items():
            if connector_name not in merged:
                merged[connector_name] = {
                    "regulator": connector_name.upper().split("_")[0],
                    "category": connector_name,
                    "format": "",  # will be auto-detected
                    "priority": 3,
                    "fields": [],
                    "urls": [],
                }

            existing_urls = set(merged[connector_name]["urls"])
            for _key, url in url_map.items():
                if url not in existing_urls:
                    merged[connector_name]["urls"].append(url)
                    existing_urls.add(url)

        return merged

    # ── Strategy Decisions ──────────────────────────────────────────

    @staticmethod
    def _pick_chunking_strategy(detected: DocType, format_hint: str) -> str:
        """Decide chunking strategy based on detected type."""
        strategies = {
            DocType.HTML: "heading_based",
            DocType.PDF: "unstructured_title",
            DocType.CSV: "row_batch",
            DocType.XML: "element_tree",
            DocType.RSS: "entry_based",
        }
        return strategies.get(detected, "text_split")

    @staticmethod
    def _quality_notes(detected: DocType, format_hint: str, url: str) -> List[str]:
        """Generate quality/capability notes for a URL."""
        notes = []
        if format_hint and format_hint != detected.value:
            notes.append(
                f"Format mismatch: config says '{format_hint}' but detected '{detected.value}'"
            )
        if detected == DocType.PDF:
            notes.append(
                "PDF: using unstructured (free/local). "
                "Tables/images may need paid API (unstructured.io or AWS Textract)."
            )
        if "xml" in url.lower() and detected != DocType.XML:
            notes.append(f"URL contains 'xml' but detected as {detected.value}")
        return notes
