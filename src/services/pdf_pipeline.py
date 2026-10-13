"""
PDF Extraction Pipeline — Download, extract, and chunk compliance PDFs.

Saves a local copy of every downloaded PDF under data/pdfs/ for manual analysis
and returns the extracted chunks for the caller to persist (e.g. via the
scraper_scheduler's OpenSearch/pgvector indexing path).

Usage:
    from src.services.pdf_pipeline import PDFPipeline
    pipeline = PDFPipeline()
    stats = pipeline.run(pdf_sources)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import structlog

logger = structlog.get_logger(__name__)

# Where local PDF copies are saved (project_root/data/pdfs/<regulator>/)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PDF_DIR = _PROJECT_ROOT / "data" / "pdfs"

_UA = "TextMind-Compliance/1.0"
_TIMEOUT = 120


def _sanitize_filename(name: str) -> str:
    """Remove non-alphanumeric chars (keep hyphens/underscores) for safe filenames."""
    return re.sub(r"[^\w\-.]", "_", name)


class PDFPipeline:
    """Download → extract → chunk pipeline for compliance PDFs."""

    def __init__(
        self,
        pdf_dir: Optional[Path] = None,
        max_chars_per_chunk: int = 2000,
    ):
        self.pdf_dir = pdf_dir or _PDF_DIR
        self.max_chars_per_chunk = max_chars_per_chunk
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA})

    def run(self, pdf_sources: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Process a list of PDF sources.

        Each source dict should have:
            url         — PDF download URL
            regulator   — e.g. "OCC", "FDIC"
            category    — e.g. "BSA/AML Handbook"
            doc_type    — e.g. "handbook", "bulletin", "advisory"

        Returns stats dict with counts of downloads, chunks, and any errors.
        """
        stats: Dict[str, Any] = {
            "total_sources": len(pdf_sources),
            "downloaded": 0,
            "chunks_extracted": 0,
            "errors": [],
        }

        all_chunks: List[Dict[str, Any]] = []

        for src in pdf_sources:
            url = src["url"]
            regulator = src.get("regulator", "UNKNOWN")
            category = src.get("category", "General")
            doc_type = src.get("doc_type", "document")

            logger.info("pdf_pipeline_processing", url=url, regulator=regulator)

            # 1. Download and save local copy
            local_path = self._download_pdf(url, regulator)
            if not local_path:
                stats["errors"].append({"url": url, "error": "download_failed"})
                continue
            stats["downloaded"] += 1

            # 2. Extract and chunk
            chunks = self._extract_chunks(
                local_path, url, regulator, category, doc_type
            )
            stats["chunks_extracted"] += len(chunks)
            all_chunks.extend(chunks)

            logger.info(
                "pdf_pipeline_extracted",
                url=url,
                chunks=len(chunks),
                local_path=str(local_path),
            )

        stats["chunks"] = all_chunks
        logger.info(
            "pdf_pipeline_complete",
            **{k: v for k, v in stats.items() if k != "chunks"},
        )
        return stats

    def _download_pdf(self, url: str, regulator: str) -> Optional[Path]:
        """Download PDF, save local copy under data/pdfs/<regulator>/."""
        try:
            resp = self._session.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("pdf_download_failed", url=url, error=str(exc))
            return None

        # Determine filename from URL
        parsed = urlparse(url)
        raw_name = os.path.basename(parsed.path) or "document.pdf"
        filename = _sanitize_filename(raw_name)
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        # Save to data/pdfs/<regulator>/
        dest_dir = self.pdf_dir / regulator.upper()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        dest_path.write_bytes(resp.content)
        logger.info("pdf_saved_locally", path=str(dest_path), size_kb=len(resp.content) // 1024)
        return dest_path

    def _extract_chunks(
        self,
        pdf_path: Path,
        source_url: str,
        regulator: str,
        category: str,
        doc_type: str,
    ) -> List[Dict[str, Any]]:
        """Partition PDF with unstructured, chunk by title, return dicts."""
        try:
            from unstructured.chunking.title import chunk_by_title
            from unstructured.partition.auto import partition
        except ImportError:
            logger.error("unstructured_not_installed")
            return []

        try:
            elements = partition(filename=str(pdf_path))
            chunks = chunk_by_title(
                elements, max_characters=self.max_chars_per_chunk
            )
        except Exception as exc:
            logger.warning("pdf_partition_failed", path=str(pdf_path), error=str(exc))
            return []

        now = datetime.utcnow().isoformat()
        doc_title = pdf_path.stem.replace("_", " ").title()
        results: List[Dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            text = getattr(chunk, "text", "").strip()
            if not text:
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()
            section_title = getattr(chunk.metadata, "title", None) or category
            page_number = getattr(chunk.metadata, "page_number", None)

            results.append({
                "doc_id": f"{regulator}-{content_hash[:12]}",
                "regulator": regulator.upper(),
                "document_title": doc_title,
                "section_title": section_title,
                "content": text,
                "page_number": page_number or 0,
                "chunk_index": idx,
                "source_url": source_url,
                "document_type": doc_type,
                "content_hash": content_hash,
                "scraped_at": now,
            })

        return results

    def extract_only(self, pdf_sources: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Download + extract only. For testing/inspection."""
        all_chunks: List[Dict[str, Any]] = []
        for src in pdf_sources:
            url = src["url"]
            regulator = src.get("regulator", "UNKNOWN")
            category = src.get("category", "General")
            doc_type = src.get("doc_type", "document")

            local_path = self._download_pdf(url, regulator)
            if not local_path:
                continue

            chunks = self._extract_chunks(
                local_path, url, regulator, category, doc_type
            )
            all_chunks.extend(chunks)

        return all_chunks
