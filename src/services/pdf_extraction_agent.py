"""
PDF Extraction Agent — multi-strategy, open-source PDF extraction.

Strategy selection (auto-routed per PDF):

  TEXT_NATIVE   pdfplumber      Digital PDFs with embedded text.
                                Fast, high-fidelity text + structured tables.
  LAYOUT_AWARE  unstructured    Complex layouts: multi-column, mixed element
                + inference     types (Title/Table/Image/List). Uses detectron2
                                layout model when available.
  OCR           pdf2image       Scanned / image-only PDFs. Converts each page
                + pytesseract   to image then runs Tesseract 5 OCR.
  HYBRID        per-page        Mixed PDFs — text pages use pdfplumber, image
                                pages fall back to OCR.

Auto-detection heuristic (first 3 pages):
  chars_per_page >= 200  -> TEXT_NATIVE
  chars_per_page < 50    -> OCR  (or HYBRID if some pages have text)
  50-200 chars / complex layout indicators -> LAYOUT_AWARE

Usage:
    agent = PdfExtractionAgent()
    chunks, meta = agent.extract(pdf_bytes, url="https://...", source="OCC", category="BSA")
    # chunks: List[Dict] ready for DocumentProcessor / downstream indexing
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_DEFAULT_CHUNK_SIZE = 2000
_DEFAULT_CHUNK_OVERLAP = 200
_TEXT_NATIVE_THRESHOLD = 200   # avg chars/page above which we use pdfplumber
_OCR_THRESHOLD = 50            # avg chars/page below which we trigger OCR
_PROBE_PAGES = 3               # pages to sample for strategy detection
_OCR_DPI = 300                 # DPI for pdf2image rasterisation
_OCR_LANG = "eng"              # Tesseract language(s), e.g. "eng+spa"
_MAX_OCR_PAGES = 100           # safety cap to avoid huge scanned docs


# ── Data types ────────────────────────────────────────────────────────────────

class ExtractionStrategy(str, Enum):
    TEXT_NATIVE = "text_native"
    LAYOUT_AWARE = "layout_aware"
    OCR = "ocr"
    HYBRID = "hybrid"


@dataclass
class ExtractionMeta:
    strategy: ExtractionStrategy
    total_pages: int
    ocr_pages: int = 0
    has_tables: bool = False
    has_images: bool = False
    avg_chars_per_page: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ── Main agent ────────────────────────────────────────────────────────────────

class PdfExtractionAgent:
    """
    Open-source PDF extraction agent with automatic strategy routing.

    Parameters
    ----------
    chunk_size:          Target max characters per output chunk.
    chunk_overlap:       Overlap between adjacent text chunks.
    ocr_dpi:             Rasterisation DPI for OCR pages (default 300).
    ocr_lang:            Tesseract language string (default "eng").
    force_strategy:      Override auto-detection (ExtractionStrategy or None).
    extract_tables:      If True, export tables as pipe-delimited chunks.
    min_chunk_chars:     Discard chunks shorter than this (noise filter).
    """

    def __init__(
        self,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
        ocr_dpi: int = _OCR_DPI,
        ocr_lang: str = _OCR_LANG,
        force_strategy: Optional[ExtractionStrategy] = None,
        extract_tables: bool = True,
        min_chunk_chars: int = 50,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.ocr_dpi = ocr_dpi
        self.ocr_lang = ocr_lang
        self.force_strategy = force_strategy
        self.extract_tables = extract_tables
        self.min_chunk_chars = min_chunk_chars

    # ── Public API ────────────────────────────────────────────────────────────

    def extract(
        self,
        pdf_bytes: bytes,
        url: str = "",
        source: str = "",
        category: str = "",
    ) -> Tuple[List[Dict[str, Any]], ExtractionMeta]:
        """
        Extract text and tables from PDF bytes.

        Returns
        -------
        (chunks, meta)
            chunks : list of standardised chunk dicts
            meta   : ExtractionMeta with strategy used, page count, quality info
        """
        strategy = self.force_strategy or self._detect_strategy(pdf_bytes)

        logger.info(
            "pdf_extraction_started",
            url=url,
            source=source,
            strategy=strategy.value,
            size_kb=len(pdf_bytes) // 1024,
        )

        dispatch = {
            ExtractionStrategy.TEXT_NATIVE: self._extract_text_native,
            ExtractionStrategy.LAYOUT_AWARE: self._extract_layout_aware,
            ExtractionStrategy.OCR: self._extract_ocr,
            ExtractionStrategy.HYBRID: self._extract_hybrid,
        }
        extractor = dispatch[strategy]

        chunks, meta = extractor(pdf_bytes, url=url, source=source, category=category)
        meta.strategy = strategy

        # Quality gate: if chosen strategy produced nothing, escalate to OCR
        if not chunks and strategy != ExtractionStrategy.OCR:
            logger.warning(
                "pdf_extraction_empty_escalating_to_ocr",
                url=url,
                strategy=strategy.value,
            )
            chunks, ocr_meta = self._extract_ocr(
                pdf_bytes, url=url, source=source, category=category
            )
            meta.ocr_pages = ocr_meta.ocr_pages
            meta.warnings.append(
                f"Primary strategy '{strategy.value}' produced no text; OCR fallback used."
            )

        logger.info(
            "pdf_extraction_complete",
            url=url,
            strategy=strategy.value,
            chunks=len(chunks),
            has_tables=meta.has_tables,
            has_images=meta.has_images,
        )
        return chunks, meta

    # ── Strategy Detection ────────────────────────────────────────────────────

    def _detect_strategy(self, pdf_bytes: bytes) -> ExtractionStrategy:
        """Sample first _PROBE_PAGES pages to decide extraction strategy."""
        try:
            import pdfplumber
        except ImportError:
            return ExtractionStrategy.LAYOUT_AWARE

        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                total = len(pdf.pages)
                probe = min(_PROBE_PAGES, total)
                char_counts: List[int] = []
                has_tables = False

                for i in range(probe):
                    page = pdf.pages[i]
                    text = page.extract_text() or ""
                    char_counts.append(len(text.strip()))
                    if page.extract_tables():
                        has_tables = True

                avg_chars = sum(char_counts) / max(len(char_counts), 1)

                if avg_chars >= _TEXT_NATIVE_THRESHOLD:
                    return ExtractionStrategy.TEXT_NATIVE

                if avg_chars < _OCR_THRESHOLD:
                    # Very little text — likely scanned; check for mixed doc
                    all_zero = all(c < 20 for c in char_counts)
                    return ExtractionStrategy.OCR if all_zero else ExtractionStrategy.HYBRID

                # Middle ground — use unstructured for layout intelligence
                return ExtractionStrategy.LAYOUT_AWARE

        except Exception as exc:
            logger.warning("pdf_strategy_detection_failed", error=str(exc))
            return ExtractionStrategy.LAYOUT_AWARE

    # ── TEXT_NATIVE: pdfplumber ───────────────────────────────────────────────

    def _extract_text_native(
        self,
        pdf_bytes: bytes,
        url: str,
        source: str,
        category: str,
    ) -> Tuple[List[Dict[str, Any]], ExtractionMeta]:
        """Extract using pdfplumber — best for digital PDFs with embedded text."""
        import pdfplumber

        meta = ExtractionMeta(
            strategy=ExtractionStrategy.TEXT_NATIVE,
            total_pages=0,
        )
        now = datetime.now(timezone.utc).isoformat()
        chunks: List[Dict[str, Any]] = []
        full_text_buffer: List[str] = []
        page_map: List[Tuple[int, int, int]] = []  # (start_char, end_char, page_num)

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            meta.total_pages = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                # ── Tables first (structured extraction) ────────────────────
                if self.extract_tables:
                    for tbl_idx, table in enumerate(page.extract_tables() or []):
                        table_text = self._table_to_text(table)
                        if table_text and len(table_text) >= self.min_chunk_chars:
                            meta.has_tables = True
                            chunks.append(self._make_chunk(
                                source=source,
                                category=category,
                                title=f"{category} — Table p{page_num}:{tbl_idx + 1}",
                                content=table_text[:self.chunk_size * 2],
                                url=url,
                                doc_type="pdf_table",
                                chunk_index=len(chunks),
                                page_number=page_num,
                                scraped_at=now,
                                extra={"extraction_method": "pdfplumber_table"},
                            ))

                # ── Regular text ─────────────────────────────────────────────
                text = (page.extract_text(layout=True) or "").strip()
                if text:
                    start = sum(len(t) for t in full_text_buffer)
                    full_text_buffer.append(text)
                    page_map.append((start, start + len(text), page_num))

                # Detect images via page objects
                if page.images:
                    meta.has_images = True

        # Chunk the full text stream, preserving page provenance
        full_text = "\n\n".join(full_text_buffer)
        avg_chars = len(full_text) / max(meta.total_pages, 1)
        meta.avg_chars_per_page = round(avg_chars, 1)

        for i, chunk_text in enumerate(self._split_text(full_text)):
            if len(chunk_text) < self.min_chunk_chars:
                continue
            # Find which page this chunk starts on
            chunk_start = full_text.find(chunk_text[:50])
            page_num = self._char_to_page(chunk_start, page_map)
            chunks.append(self._make_chunk(
                source=source,
                category=category,
                title=category,
                content=chunk_text,
                url=url,
                doc_type="pdf",
                chunk_index=i,
                page_number=page_num,
                scraped_at=now,
                extra={"extraction_method": "pdfplumber_text"},
            ))

        return chunks, meta

    # ── LAYOUT_AWARE: unstructured ────────────────────────────────────────────

    def _extract_layout_aware(
        self,
        pdf_bytes: bytes,
        url: str,
        source: str,
        category: str,
    ) -> Tuple[List[Dict[str, Any]], ExtractionMeta]:
        """Extract using unstructured — layout-aware, element-typed extraction."""
        try:
            from unstructured.chunking.title import chunk_by_title
            from unstructured.partition.auto import partition
        except ImportError:
            logger.warning("unstructured_not_installed", url=url)
            return [], ExtractionMeta(strategy=ExtractionStrategy.LAYOUT_AWARE, total_pages=0,
                                      warnings=["unstructured not installed"])

        meta = ExtractionMeta(
            strategy=ExtractionStrategy.LAYOUT_AWARE,
            total_pages=0,
        )
        now = datetime.now(timezone.utc).isoformat()
        chunks: List[Dict[str, Any]] = []

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            elements = partition(
                filename=tmp_path,
                strategy="hi_res",
                infer_table_structure=True,
                languages=[self.ocr_lang],
            )

            page_nums = set()
            for el in elements:
                el_type = type(el).__name__
                page_num = getattr(el.metadata, "page_number", None)
                if page_num:
                    page_nums.add(page_num)
                if el_type == "Table":
                    meta.has_tables = True
                if el_type == "Image":
                    meta.has_images = True

            meta.total_pages = max(page_nums) if page_nums else 0

            text_chunks = chunk_by_title(
                elements,
                max_characters=self.chunk_size,
                overlap=self.chunk_overlap,
                overlap_all=False,
            )

            for i, chunk in enumerate(text_chunks):
                text = getattr(chunk, "text", "").strip()
                if not text or len(text) < self.min_chunk_chars:
                    continue

                el_type = type(chunk).__name__
                page_num = getattr(chunk.metadata, "page_number", None)
                title = getattr(chunk.metadata, "title", None) or category

                table_html = getattr(chunk.metadata, "text_as_html", None)
                extra: Dict[str, Any] = {"extraction_method": "unstructured"}
                if el_type == "Table" and table_html:
                    extra["table_html"] = table_html

                chunks.append(self._make_chunk(
                    source=source,
                    category=category,
                    title=title,
                    content=text,
                    url=url,
                    doc_type="pdf_table" if el_type == "Table" else "pdf",
                    chunk_index=i,
                    page_number=page_num,
                    scraped_at=now,
                    extra=extra,
                ))

        except Exception as exc:
            logger.warning("unstructured_extraction_failed", url=url, error=str(exc))
            meta.warnings.append(f"unstructured error: {exc}")
        finally:
            os.unlink(tmp_path)

        return chunks, meta

    # ── OCR: pdf2image + pytesseract ──────────────────────────────────────────

    def _extract_ocr(
        self,
        pdf_bytes: bytes,
        url: str,
        source: str,
        category: str,
    ) -> Tuple[List[Dict[str, Any]], ExtractionMeta]:
        """OCR each page via pdf2image + Tesseract — for scanned PDFs."""
        try:
            from pdf2image import convert_from_bytes
            import pytesseract
        except ImportError:
            logger.warning("pdf2image_or_pytesseract_not_installed", url=url)
            return [], ExtractionMeta(strategy=ExtractionStrategy.OCR, total_pages=0,
                                      warnings=["pdf2image or pytesseract not installed"])

        meta = ExtractionMeta(
            strategy=ExtractionStrategy.OCR,
            total_pages=0,
        )
        now = datetime.now(timezone.utc).isoformat()
        chunks: List[Dict[str, Any]] = []
        chunk_index = 0

        try:
            images = convert_from_bytes(
                pdf_bytes,
                dpi=self.ocr_dpi,
                fmt="jpeg",
            )
        except Exception as exc:
            logger.error("pdf2image_conversion_failed", url=url, error=str(exc))
            meta.warnings.append(f"pdf2image failed: {exc}")
            return chunks, meta

        total_pages = len(images)
        meta.total_pages = total_pages

        if total_pages > _MAX_OCR_PAGES:
            logger.warning(
                "pdf_ocr_page_cap",
                url=url,
                total_pages=total_pages,
                cap=_MAX_OCR_PAGES,
            )
            images = images[:_MAX_OCR_PAGES]
            meta.warnings.append(
                f"OCR capped at {_MAX_OCR_PAGES} pages (doc has {total_pages})"
            )

        page_texts: List[str] = []
        for page_num, img in enumerate(images, start=1):
            try:
                text = pytesseract.image_to_string(
                    img,
                    lang=self.ocr_lang,
                    config="--oem 3 --psm 3",
                )
                text = text.strip()
                if text:
                    page_texts.append(f"[Page {page_num}]\n{text}")
                    meta.ocr_pages += 1
            except Exception as exc:
                logger.warning("ocr_page_failed", page=page_num, url=url, error=str(exc))
                meta.warnings.append(f"OCR failed on page {page_num}: {exc}")

        full_text = "\n\n".join(page_texts)
        meta.avg_chars_per_page = round(len(full_text) / max(total_pages, 1), 1)

        for chunk_text in self._split_text(full_text):
            if len(chunk_text) < self.min_chunk_chars:
                continue
            page_num = self._parse_page_from_ocr_text(chunk_text)
            chunks.append(self._make_chunk(
                source=source,
                category=category,
                title=category,
                content=chunk_text,
                url=url,
                doc_type="pdf",
                chunk_index=chunk_index,
                page_number=page_num,
                scraped_at=now,
                extra={"extraction_method": "ocr_tesseract"},
            ))
            chunk_index += 1

        return chunks, meta

    # ── HYBRID: per-page routing ──────────────────────────────────────────────

    def _extract_hybrid(
        self,
        pdf_bytes: bytes,
        url: str,
        source: str,
        category: str,
    ) -> Tuple[List[Dict[str, Any]], ExtractionMeta]:
        """
        Hybrid extraction: text pages -> pdfplumber, image pages -> OCR.

        Each page is assessed individually so mixed documents (e.g. a report
        with mostly digital text but a few scanned appendix pages) are handled
        correctly.
        """
        try:
            import pdfplumber
            from pdf2image import convert_from_bytes
            import pytesseract
        except ImportError:
            # Degrade gracefully
            return self._extract_text_native(pdf_bytes, url=url, source=source, category=category)

        meta = ExtractionMeta(
            strategy=ExtractionStrategy.HYBRID,
            total_pages=0,
        )
        now = datetime.now(timezone.utc).isoformat()
        chunks: List[Dict[str, Any]] = []
        chunk_index = 0

        # Pre-render all pages to images (needed for OCR pages)
        try:
            images = convert_from_bytes(pdf_bytes, dpi=self.ocr_dpi, fmt="jpeg")
        except Exception as exc:
            logger.warning("hybrid_pdf2image_failed_falling_back", url=url, error=str(exc))
            return self._extract_text_native(pdf_bytes, url=url, source=source, category=category)

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            meta.total_pages = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text(layout=True) or "").strip()

                if len(text) >= _OCR_THRESHOLD:
                    # ── Digital page: use pdfplumber ─────────────────────────
                    if self.extract_tables:
                        for tbl in page.extract_tables() or []:
                            table_text = self._table_to_text(tbl)
                            if table_text and len(table_text) >= self.min_chunk_chars:
                                meta.has_tables = True
                                chunks.append(self._make_chunk(
                                    source=source,
                                    category=category,
                                    title=f"{category} — Table p{page_num}",
                                    content=table_text[:self.chunk_size * 2],
                                    url=url,
                                    doc_type="pdf_table",
                                    chunk_index=chunk_index,
                                    page_number=page_num,
                                    scraped_at=now,
                                    extra={"extraction_method": "hybrid_pdfplumber"},
                                ))
                                chunk_index += 1

                    for chunk_text in self._split_text(text):
                        if len(chunk_text) < self.min_chunk_chars:
                            continue
                        chunks.append(self._make_chunk(
                            source=source,
                            category=category,
                            title=category,
                            content=chunk_text,
                            url=url,
                            doc_type="pdf",
                            chunk_index=chunk_index,
                            page_number=page_num,
                            scraped_at=now,
                            extra={"extraction_method": "hybrid_pdfplumber"},
                        ))
                        chunk_index += 1

                else:
                    # ── Scanned page: OCR ────────────────────────────────────
                    if page_num - 1 >= len(images):
                        continue
                    img = images[page_num - 1]
                    try:
                        ocr_text = pytesseract.image_to_string(
                            img,
                            lang=self.ocr_lang,
                            config="--oem 3 --psm 3",
                        ).strip()
                    except Exception as exc:
                        logger.warning("hybrid_ocr_page_failed", page=page_num, error=str(exc))
                        meta.warnings.append(f"OCR failed p{page_num}: {exc}")
                        continue

                    if ocr_text:
                        meta.ocr_pages += 1
                        for chunk_text in self._split_text(ocr_text):
                            if len(chunk_text) < self.min_chunk_chars:
                                continue
                            chunks.append(self._make_chunk(
                                source=source,
                                category=category,
                                title=category,
                                content=chunk_text,
                                url=url,
                                doc_type="pdf",
                                chunk_index=chunk_index,
                                page_number=page_num,
                                scraped_at=now,
                                extra={"extraction_method": "hybrid_ocr"},
                            ))
                            chunk_index += 1

                if page.images:
                    meta.has_images = True

        total_chars = sum(len(c["content"]) for c in chunks)
        meta.avg_chars_per_page = round(total_chars / max(meta.total_pages, 1), 1)
        return chunks, meta

    # ── Helpers ───────────────────────────────────────────────────────────────

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
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        doc: Dict[str, Any] = {
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
        """Sentence-aware text splitter with overlap."""
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            if end < len(text):
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
    def _table_to_text(table: List[List[Any]]) -> str:
        """Convert pdfplumber table (list of lists) to pipe-delimited text."""
        if not table:
            return ""
        rows = []
        for row in table:
            cells = [str(cell).strip() if cell is not None else "" for cell in row]
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    @staticmethod
    def _char_to_page(char_pos: int, page_map: List[Tuple[int, int, int]]) -> Optional[int]:
        """Map a character offset to its page number using the page_map index."""
        if char_pos < 0:
            return None
        for start, end, page_num in page_map:
            if start <= char_pos < end:
                return page_num
        return page_map[-1][2] if page_map else None

    @staticmethod
    def _parse_page_from_ocr_text(text: str) -> Optional[int]:
        """Extract page number from OCR text prefix like '[Page 3]'."""
        m = re.match(r"\[Page (\d+)\]", text.strip())
        return int(m.group(1)) if m else None
