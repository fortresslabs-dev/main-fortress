"""
Smart Document Processor — auto-detects document type and applies the right
scraping + chunking strategy per URL.

Supported document types:
  html  → BeautifulSoup heading-based chunking
  pdf   → unstructured (local free) with paid-API fallback stub
  csv   → csv.DictReader row-level parsing
  xml   → ElementTree parsing
  rss   → feedparser

The processor inspects Content-Type headers and URL patterns to decide the
strategy *at runtime*, so connectors don't need to hardcode which function
to call.  This replaces the manual scrape_html_page / scrape_pdf_url split.

Usage:
    from src.services.document_processor import DocumentProcessor

    processor = DocumentProcessor()
    results = processor.process_url(url, source="OCC", category="BSA/AML")
    # results = [{ doc_type, source, category, title, content, chunks, ... }]
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

_UA = "TextMind-Compliance/1.0"
_TIMEOUT_HTML = 30
_TIMEOUT_PDF = 120
_TIMEOUT_DATA = 60

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PDF_DIR = _PROJECT_ROOT / "data" / "pdfs"

# Max chunk size for text splitting (characters)
_DEFAULT_CHUNK_SIZE = 2000
_DEFAULT_CHUNK_OVERLAP = 200


class DocType(str, Enum):
    HTML = "html"
    PDF = "pdf"
    CSV = "csv"
    XML = "xml"
    RSS = "rss"
    UNKNOWN = "unknown"


class DocumentProcessor:
    """Auto-detecting document processor with per-type chunking strategies."""

    def __init__(
        self,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
        use_paid_pdf_api: bool = False,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.use_paid_pdf_api = use_paid_pdf_api
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA})

    # ── Public API ──────────────────────────────────────────────────

    def process_url(
        self,
        url: str,
        source: str = "",
        category: str = "",
        *,
        format_hint: Optional[str] = None,
        content_selectors: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch a URL, detect its type, and return chunked documents.

        Args:
            url: The URL to fetch.
            source: Regulator name (e.g. "OCC", "CFPB").
            category: Document category for labelling.
            format_hint: Optional override ("html", "pdf", "csv", "xml", "rss").
            content_selectors: CSS selectors for HTML content extraction.

        Returns:
            List of document chunk dicts ready for downstream indexing.
        """
        doc_type = DocType(format_hint) if format_hint else self._detect_type(url)

        logger.info(
            "document_processing_started",
            url=url,
            detected_type=doc_type.value,
            source=source,
        )

        handler = {
            DocType.HTML: self._process_html,
            DocType.PDF: self._process_pdf,
            DocType.CSV: self._process_csv,
            DocType.XML: self._process_xml,
            DocType.RSS: self._process_rss,
        }.get(doc_type)

        if not handler:
            logger.warning("unsupported_document_type", url=url, doc_type=doc_type)
            return []

        try:
            chunks = handler(
                url=url,
                source=source,
                category=category,
                content_selectors=content_selectors,
            )
        except Exception as exc:
            logger.error("document_processing_failed", url=url, error=str(exc))
            return []

        logger.info(
            "document_processing_complete",
            url=url,
            doc_type=doc_type.value,
            chunks=len(chunks),
        )
        return chunks

    def detect_type(self, url: str) -> DocType:
        """Public wrapper for type detection (useful for testing / reporting)."""
        return self._detect_type(url)

    # ── Type Detection ──────────────────────────────────────────────

    def _detect_type(self, url: str) -> DocType:
        """Detect document type using URL pattern + HTTP Content-Type header.

        Strategy (ordered by cost):
          1. URL extension (.pdf, .csv, .xml, .rss …)
          2. HEAD request Content-Type header
          3. Fallback to HTML (most gov sites serve HTML by default)
        """
        # 1. URL-based detection (free, no network call)
        parsed = urlparse(url)
        path_lower = parsed.path.lower()

        extension_map = {
            ".pdf": DocType.PDF,
            ".csv": DocType.CSV,
            ".xml": DocType.XML,
            ".rss": DocType.RSS,
            ".atom": DocType.RSS,
            ".json": DocType.XML,  # treat JSON feeds as structured data
        }
        for ext, dt in extension_map.items():
            if path_lower.endswith(ext):
                return dt

        # RSS feed URL patterns
        if any(p in path_lower for p in ["/feed", "/rss", "/atom"]):
            return DocType.RSS

        # 2. HEAD request for Content-Type (one cheap network call)
        try:
            resp = self._session.head(url, timeout=10, allow_redirects=True)
            content_type = resp.headers.get("Content-Type", "").lower()

            if "application/pdf" in content_type:
                return DocType.PDF
            if "text/csv" in content_type or "application/csv" in content_type:
                return DocType.CSV
            if "text/xml" in content_type or "application/xml" in content_type:
                return DocType.XML
            if "application/rss" in content_type or "application/atom" in content_type:
                return DocType.RSS
            if "text/html" in content_type:
                return DocType.HTML
        except requests.RequestException:
            pass

        # 3. Default to HTML
        return DocType.HTML

    # ── HTML Processing ─────────────────────────────────────────────

    def _process_html(
        self, url: str, source: str, category: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Scrape HTML page with heading-based chunking."""
        content_selectors = kwargs.get("content_selectors")

        resp = self._session.get(url, timeout=_TIMEOUT_HTML)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content, "lxml")

        # Find the main content area — skip selectors that match empty Drupal
        # wrapper divs (e.g., div.field--name-body on FDIC with <300 chars).
        _MIN_CONTENT_CHARS = 500
        content_div = None
        for sel in (content_selectors or []) + [
            "div.article-body",
            "div.field--name-body",
            "div.content-wrapper",
            "div.regulation-content",
            "main",
            "article",
        ]:
            candidate = soup.select_one(sel)
            if candidate and len(candidate.get_text(strip=True)) >= _MIN_CONTENT_CHARS:
                content_div = candidate
                break
        if not content_div:
            content_div = soup.body or soup

        now = datetime.now(timezone.utc).isoformat()
        docs: List[Dict[str, Any]] = []

        # Phase 1: heading-based semantic chunking
        for heading in content_div.find_all(["h1", "h2", "h3"]):
            title = heading.get_text(strip=True)
            if not title:
                continue

            parts: List[str] = []
            for sibling in heading.find_next_siblings():
                if sibling.name in ("h1", "h2", "h3"):
                    break
                if sibling.name in ("p", "ul", "ol", "div", "table", "dl"):
                    text = sibling.get_text(strip=True)
                    if text:
                        parts.append(text)

            content = " ".join(parts)
            if not content:
                continue

            # Split large sections into sub-chunks
            for i, chunk in enumerate(self._split_text(content)):
                docs.append(self._make_chunk(
                    source=source,
                    category=category,
                    title=title,
                    content=chunk,
                    url=url,
                    doc_type="html",
                    chunk_index=i,
                    scraped_at=now,
                ))

        # Phase 2: table extraction (structured data in HTML tables)
        for table in content_div.find_all("table"):
            table_text = self._extract_table_text(table)
            if table_text and len(table_text) > 50:
                caption = table.find("caption")
                title = caption.get_text(strip=True) if caption else f"{category} — Table"
                docs.append(self._make_chunk(
                    source=source,
                    category=category,
                    title=title,
                    content=table_text[:self.chunk_size],
                    url=url,
                    doc_type="html_table",
                    chunk_index=0,
                    scraped_at=now,
                ))

        # Phase 3: fallback — if heading-based extraction found nothing
        if not docs:
            full_text = content_div.get_text(separator=" ", strip=True)
            if len(full_text) > 100:
                page_title_el = (
                    content_div.find("h1") or soup.find("h1") or soup.find("title")
                )
                page_title = page_title_el.get_text(strip=True) if page_title_el else category
                for i, chunk in enumerate(self._split_text(full_text)):
                    docs.append(self._make_chunk(
                        source=source,
                        category=category,
                        title=page_title,
                        content=chunk,
                        url=url,
                        doc_type="html",
                        chunk_index=i,
                        scraped_at=now,
                    ))

        return docs

    # ── PDF Processing ──────────────────────────────────────────────

    def _process_pdf(
        self, url: str, source: str, category: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Extract text from PDF using PdfExtractionAgent (multi-strategy).

        Strategy is auto-detected per PDF:
          text_native  → pdfplumber (fast, digital PDFs)
          layout_aware → unstructured hi-res (complex/multi-column)
          ocr          → pdf2image + tesseract (scanned)
          hybrid       → per-page routing (mixed docs)
        """
        from src.services.pdf_extraction_agent import PdfExtractionAgent

        resp = self._session.get(url, timeout=_TIMEOUT_PDF)
        resp.raise_for_status()
        pdf_bytes = resp.content

        # Save local copy for manual analysis
        self._save_pdf_locally(url, source, pdf_bytes)

        agent = PdfExtractionAgent(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        chunks, meta = agent.extract(pdf_bytes, url=url, source=source, category=category)

        if meta.warnings:
            for w in meta.warnings:
                logger.warning("pdf_extraction_warning", url=url, warning=w)

        return chunks

    # ── CSV Processing ──────────────────────────────────────────────

    def _process_csv(
        self, url: str, source: str, category: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Parse CSV data into structured document chunks."""
        resp = self._session.get(url, timeout=_TIMEOUT_DATA)
        resp.raise_for_status()

        now = datetime.now(timezone.utc).isoformat()
        docs: List[Dict[str, Any]] = []

        reader = csv.reader(io.StringIO(resp.text))
        rows: List[List[str]] = []
        for row in reader:
            if len(row) >= 2:
                rows.append(row)

        # Batch rows into chunks to avoid one-doc-per-row explosion
        batch_size = 50
        for batch_idx in range(0, len(rows), batch_size):
            batch = rows[batch_idx : batch_idx + batch_size]
            content = "\n".join(" | ".join(cell.strip() for cell in row) for row in batch)
            docs.append(self._make_chunk(
                source=source,
                category=category,
                title=f"{category} — rows {batch_idx + 1}-{batch_idx + len(batch)}",
                content=content[:self.chunk_size * 2],  # CSV chunks can be larger
                url=url,
                doc_type="csv",
                chunk_index=batch_idx // batch_size,
                scraped_at=now,
            ))

        return docs

    # ── XML Processing ──────────────────────────────────────────────

    def _process_xml(
        self, url: str, source: str, category: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Parse XML documents into chunked text."""
        resp = self._session.get(url, timeout=_TIMEOUT_DATA)
        resp.raise_for_status()

        now = datetime.now(timezone.utc).isoformat()
        docs: List[Dict[str, Any]] = []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("xml_parse_failed", url=url, error=str(exc))
            return []

        # Extract text from all leaf elements, grouped by top-level parent
        for i, child in enumerate(root):
            text_parts = []
            self._xml_collect_text(child, text_parts)
            content = " ".join(text_parts)
            if not content or len(content) < 20:
                continue

            tag_name = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            title = child.attrib.get("name", child.attrib.get("id", tag_name))

            for ci, chunk in enumerate(self._split_text(content)):
                docs.append(self._make_chunk(
                    source=source,
                    category=category,
                    title=f"{title}",
                    content=chunk,
                    url=url,
                    doc_type="xml",
                    chunk_index=ci,
                    scraped_at=now,
                ))

        return docs

    # ── RSS Processing ──────────────────────────────────────────────

    def _process_rss(
        self, url: str, source: str, category: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Parse RSS/Atom feed entries into document chunks."""
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser_not_installed", url=url)
            return []

        feed = feedparser.parse(url)
        now = datetime.now(timezone.utc).isoformat()
        docs: List[Dict[str, Any]] = []

        for i, entry in enumerate(feed.entries[:30]):
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link = getattr(entry, "link", url)
            published = getattr(entry, "published", "")

            content = f"{title}. {summary}" if summary else title
            if not content or len(content) < 10:
                continue

            docs.append(self._make_chunk(
                source=source,
                category=category,
                title=title or f"{category} item {i + 1}",
                content=content[:self.chunk_size],
                url=link,
                doc_type="rss",
                chunk_index=i,
                scraped_at=now,
                extra={"published": published},
            ))

        return docs

    # ── Helpers ──────────────────────────────────────────────────────

    def _make_chunk(
        self,
        source: str,
        category: str,
        title: str,
        content: str,
        url: str,
        doc_type: str,
        chunk_index: int,
        scraped_at: str,
        page_number: Optional[int] = None,
        extra: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Build a standardised document chunk dict."""
        doc = {
            "source": source,
            "category": category,
            "title": title,
            "content": content,
            "url": url,
            "doc_type": doc_type,
            "chunk_index": chunk_index,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "scraped_at": scraped_at,
        }
        if page_number is not None:
            doc["page_number"] = page_number
        if extra:
            doc.update(extra)
        return doc

    def _split_text(self, text: str) -> List[str]:
        """Split long text into overlapping chunks of self.chunk_size."""
        if len(text) <= self.chunk_size:
            return [text]

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size

            # Try to break at a sentence boundary
            if end < len(text):
                # Look for sentence end in last 20% of chunk
                search_start = max(start, end - self.chunk_size // 5)
                last_period = text.rfind(". ", search_start, end)
                if last_period > search_start:
                    end = last_period + 1

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - self.chunk_overlap if end < len(text) else end

        return chunks

    @staticmethod
    def _extract_table_text(table) -> str:
        """Convert HTML table to pipe-delimited text."""
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    @staticmethod
    def _xml_collect_text(element, parts: list) -> None:
        """Recursively collect text from XML element tree."""
        if element.text and element.text.strip():
            parts.append(element.text.strip())
        for child in element:
            DocumentProcessor._xml_collect_text(child, parts)
        if element.tail and element.tail.strip():
            parts.append(element.tail.strip())

    @staticmethod
    def _save_pdf_locally(url: str, source: str, pdf_bytes: bytes) -> None:
        """Save PDF to data/pdfs/<SOURCE>/ for manual analysis."""
        try:
            parsed = urlparse(url)
            raw_name = os.path.basename(parsed.path) or "document.pdf"
            filename = re.sub(r"[^\w\-.]", "_", raw_name)
            if not filename.endswith(".pdf"):
                filename += ".pdf"
            local_dir = _PDF_DIR / source.upper().replace(" ", "_").replace("/", "_")
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / filename
            local_path.write_bytes(pdf_bytes)
            logger.info("pdf_saved_locally", path=str(local_path), size_kb=len(pdf_bytes) // 1024)
        except Exception as exc:
            logger.warning("pdf_local_save_failed", url=url, error=str(exc))
