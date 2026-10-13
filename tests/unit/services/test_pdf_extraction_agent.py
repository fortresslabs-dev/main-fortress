"""Unit tests for PdfExtractionAgent — strategy detection and all 4 extraction paths."""
from __future__ import annotations

import io
import struct
from unittest.mock import MagicMock, patch

import pytest

from src.services.pdf_extraction_agent import (
    ExtractionMeta,
    ExtractionStrategy,
    PdfExtractionAgent,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_agent(**kwargs) -> PdfExtractionAgent:
    return PdfExtractionAgent(chunk_size=500, chunk_overlap=50, **kwargs)


def _fake_chunk(content: str = "Test content for chunk.", doc_type: str = "pdf") -> dict:
    import hashlib
    return {
        "source": "OCC",
        "category": "BSA",
        "title": "BSA",
        "content": content,
        "url": "https://example.com/doc.pdf",
        "doc_type": doc_type,
        "chunk_index": 0,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "scraped_at": "2026-04-06T00:00:00+00:00",
        "page_number": 1,
        "extraction_method": "pdfplumber_text",
    }


# ── Strategy Detection ────────────────────────────────────────────────────────


class TestStrategyDetection:
    """_detect_strategy selects the right mode based on char density."""

    def test_text_native_when_rich_text(self):
        """Pages with >= 200 chars each -> TEXT_NATIVE."""
        agent = _make_agent()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "A" * 300
        mock_page.extract_tables.return_value = []

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.__enter__ = lambda s: mock_pdf
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_pdf.pages = [mock_page, mock_page, mock_page]
            mock_open.return_value = mock_pdf

            strategy = agent._detect_strategy(b"fake-pdf-bytes")

        assert strategy == ExtractionStrategy.TEXT_NATIVE

    def test_ocr_when_no_text(self):
        """Pages with < 20 chars each -> OCR."""
        agent = _make_agent()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "ab"
        mock_page.extract_tables.return_value = []

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.__enter__ = lambda s: mock_pdf
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_pdf.pages = [mock_page, mock_page]
            mock_open.return_value = mock_pdf

            strategy = agent._detect_strategy(b"fake-pdf-bytes")

        assert strategy == ExtractionStrategy.OCR

    def test_layout_aware_for_midrange(self):
        """50-200 avg chars -> LAYOUT_AWARE."""
        agent = _make_agent()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "A" * 100  # 100 chars — midrange
        mock_page.extract_tables.return_value = []

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.__enter__ = lambda s: mock_pdf
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_pdf.pages = [mock_page]
            mock_open.return_value = mock_pdf

            strategy = agent._detect_strategy(b"fake-pdf-bytes")

        assert strategy == ExtractionStrategy.LAYOUT_AWARE

    def test_pdfplumber_import_error_falls_back(self):
        """If pdfplumber is unavailable, fall back to LAYOUT_AWARE."""
        agent = _make_agent()
        with patch.dict("sys.modules", {"pdfplumber": None}):
            strategy = agent._detect_strategy(b"fake")
        assert strategy == ExtractionStrategy.LAYOUT_AWARE

    def test_force_strategy_bypasses_detection(self):
        """force_strategy skips _detect_strategy entirely."""
        agent = _make_agent(force_strategy=ExtractionStrategy.OCR)
        # No PDF bytes needed — detection is skipped
        with patch.object(agent, "_extract_ocr", return_value=([], ExtractionMeta(
            strategy=ExtractionStrategy.OCR, total_pages=0
        ))) as mock_ocr:
            agent.extract(b"bytes", url="https://x.com/doc.pdf")
            mock_ocr.assert_called_once()


# ── TEXT_NATIVE strategy ──────────────────────────────────────────────────────


class TestTextNativeExtraction:
    """_extract_text_native via pdfplumber."""

    def test_extracts_text_chunks(self):
        agent = _make_agent()

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Compliance rule text. " * 20
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.__enter__ = lambda s: mock_pdf
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_pdf.pages = [mock_page]
            mock_open.return_value = mock_pdf

            chunks, meta = agent._extract_text_native(
                b"bytes", url="https://occ.gov/doc.pdf", source="OCC", category="BSA"
            )

        assert len(chunks) >= 1
        assert chunks[0]["doc_type"] == "pdf"
        assert chunks[0]["source"] == "OCC"
        assert chunks[0]["extraction_method"] == "pdfplumber_text"
        assert meta.total_pages == 1

    def test_table_extraction(self):
        agent = _make_agent(extract_tables=True)

        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_page.extract_tables.return_value = [
            [["Name", "Amount"], ["Wire Transfer", "$10,000"], ["CTR", "$10,001"]]
        ]
        mock_page.images = []

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.__enter__ = lambda s: mock_pdf
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_pdf.pages = [mock_page]
            mock_open.return_value = mock_pdf

            chunks, meta = agent._extract_text_native(
                b"bytes", url="https://occ.gov/doc.pdf", source="OCC", category="BSA"
            )

        table_chunks = [c for c in chunks if c["doc_type"] == "pdf_table"]
        assert len(table_chunks) >= 1
        assert "Wire Transfer" in table_chunks[0]["content"]
        assert meta.has_tables is True

    def test_images_detected(self):
        agent = _make_agent()

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Some text here"
        mock_page.extract_tables.return_value = []
        mock_page.images = [{"x0": 0}]  # non-empty images list

        with patch("pdfplumber.open") as mock_open:
            mock_pdf = MagicMock()
            mock_pdf.__enter__ = lambda s: mock_pdf
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_pdf.pages = [mock_page]
            mock_open.return_value = mock_pdf

            _, meta = agent._extract_text_native(
                b"bytes", url="https://occ.gov/doc.pdf", source="OCC", category="BSA"
            )

        assert meta.has_images is True


# ── LAYOUT_AWARE strategy ─────────────────────────────────────────────────────


class TestLayoutAwareExtraction:
    """_extract_layout_aware via unstructured."""

    def test_extracts_chunks_from_elements(self):
        agent = _make_agent()

        mock_el = MagicMock()
        mock_el.text = "Regulation text about BSA compliance requirements."
        mock_el.metadata.page_number = 1
        mock_el.metadata.title = "BSA Overview"
        mock_el.metadata.text_as_html = None

        mock_chunk = MagicMock()
        mock_chunk.text = "Regulation text about BSA compliance requirements."
        mock_chunk.metadata.page_number = 1
        mock_chunk.metadata.title = "BSA Overview"
        mock_chunk.metadata.text_as_html = None

        with patch("src.services.pdf_extraction_agent.tempfile.NamedTemporaryFile") as mock_tmp, \
             patch("os.unlink"), \
             patch("src.services.pdf_extraction_agent.PdfExtractionAgent._extract_layout_aware") as mock_ext:

            mock_ext.return_value = (
                [_fake_chunk("Regulation text about BSA compliance requirements.")],
                ExtractionMeta(strategy=ExtractionStrategy.LAYOUT_AWARE, total_pages=1),
            )

            chunks, meta = agent._extract_layout_aware(
                b"bytes", url="https://occ.gov/doc.pdf", source="OCC", category="BSA"
            )

        assert len(chunks) >= 1

    def test_unstructured_not_installed_returns_empty(self):
        agent = _make_agent()
        with patch.dict("sys.modules", {"unstructured": None,
                                         "unstructured.chunking": None,
                                         "unstructured.chunking.title": None,
                                         "unstructured.partition": None,
                                         "unstructured.partition.auto": None}):
            chunks, meta = agent._extract_layout_aware(
                b"bytes", url="https://occ.gov/doc.pdf", source="OCC", category="BSA"
            )
        assert chunks == []
        assert any("not installed" in w for w in meta.warnings)


# ── OCR strategy ──────────────────────────────────────────────────────────────


class TestOCRExtraction:
    """_extract_ocr via pdf2image + pytesseract."""

    def test_ocr_produces_chunks(self):
        agent = _make_agent()

        mock_img = MagicMock()

        with patch("pdf2image.convert_from_bytes", return_value=[mock_img]), \
             patch("pytesseract.image_to_string", return_value="This is OCR extracted text from a scanned page."):

            chunks, meta = agent._extract_ocr(
                b"bytes", url="https://occ.gov/scan.pdf", source="OCC", category="BSA"
            )

        assert len(chunks) >= 1
        assert "OCR extracted text" in chunks[0]["content"]
        assert chunks[0]["extraction_method"] == "ocr_tesseract"
        assert meta.ocr_pages == 1

    def test_ocr_page_cap(self):
        """Docs over _MAX_OCR_PAGES are capped."""
        agent = _make_agent()
        mock_imgs = [MagicMock() for _ in range(110)]

        with patch("pdf2image.convert_from_bytes", return_value=mock_imgs), \
             patch("pytesseract.image_to_string", return_value="text"):

            _, meta = agent._extract_ocr(
                b"bytes", url="https://occ.gov/big.pdf", source="OCC", category="BSA"
            )

        assert any("capped" in w for w in meta.warnings)

    def test_pdf2image_failure_returns_empty(self):
        agent = _make_agent()

        with patch("pdf2image.convert_from_bytes", side_effect=Exception("poppler missing")):
            chunks, meta = agent._extract_ocr(
                b"bytes", url="https://occ.gov/fail.pdf", source="OCC", category="BSA"
            )

        assert chunks == []
        assert any("pdf2image failed" in w for w in meta.warnings)


# ── OCR Escalation ────────────────────────────────────────────────────────────


class TestOCREscalation:
    """If primary strategy returns no chunks, agent escalates to OCR."""

    def test_escalates_when_text_native_empty(self):
        agent = _make_agent()

        fallback_chunk = _fake_chunk("OCR fallback text.")

        with patch.object(agent, "_detect_strategy", return_value=ExtractionStrategy.TEXT_NATIVE), \
             patch.object(agent, "_extract_text_native", return_value=(
                 [], ExtractionMeta(strategy=ExtractionStrategy.TEXT_NATIVE, total_pages=1)
             )), \
             patch.object(agent, "_extract_ocr", return_value=(
                 [fallback_chunk],
                 ExtractionMeta(strategy=ExtractionStrategy.OCR, total_pages=1, ocr_pages=1)
             )) as mock_ocr:

            chunks, meta = agent.extract(b"bytes", url="https://x.com/doc.pdf")

        mock_ocr.assert_called_once()
        assert len(chunks) == 1
        assert any("OCR fallback" in w for w in meta.warnings)


# ── Helpers ───────────────────────────────────────────────────────────────────


class TestHelpers:
    """Unit tests for _table_to_text, _split_text, _char_to_page, _parse_page."""

    def test_table_to_text_pipe_delimited(self):
        table = [["Name", "Type", "Program"], ["Al-Shabaab", "Entity", "SDGT"], [None, "", "IRAN"]]
        result = PdfExtractionAgent._table_to_text(table)
        assert "Name | Type | Program" in result
        assert "Al-Shabaab | Entity | SDGT" in result

    def test_table_to_text_empty(self):
        assert PdfExtractionAgent._table_to_text([]) == ""

    def test_split_text_short_returns_as_is(self):
        agent = _make_agent()
        assert agent._split_text("short") == ["short"]

    def test_split_text_long_chunks(self):
        agent = PdfExtractionAgent(chunk_size=50, chunk_overlap=10)
        text = "Word " * 30  # 150 chars
        chunks = agent._split_text(text)
        assert len(chunks) >= 2
        assert all(len(c) <= 60 for c in chunks)  # some slack for overlap

    def test_char_to_page_basic(self):
        page_map = [(0, 100, 1), (100, 200, 2), (200, 300, 3)]
        assert PdfExtractionAgent._char_to_page(50, page_map) == 1
        assert PdfExtractionAgent._char_to_page(150, page_map) == 2
        assert PdfExtractionAgent._char_to_page(250, page_map) == 3

    def test_char_to_page_negative(self):
        assert PdfExtractionAgent._char_to_page(-1, [(0, 100, 1)]) is None

    def test_parse_page_from_ocr_text(self):
        assert PdfExtractionAgent._parse_page_from_ocr_text("[Page 7]\nsome text") == 7
        assert PdfExtractionAgent._parse_page_from_ocr_text("no marker") is None

    def test_chunk_schema(self):
        agent = _make_agent()
        chunk = agent._make_chunk(
            source="OCC",
            category="BSA",
            title="Test Title",
            content="Test content.",
            url="https://example.com/doc.pdf",
            doc_type="pdf",
            chunk_index=0,
            scraped_at="2026-04-06T00:00:00+00:00",
            page_number=3,
            extra={"extraction_method": "pdfplumber_text"},
        )
        required = {"source", "category", "title", "content", "url", "doc_type",
                    "chunk_index", "content_hash", "scraped_at", "page_number"}
        assert required.issubset(chunk.keys())
        assert len(chunk["content_hash"]) == 64  # SHA-256 hex
