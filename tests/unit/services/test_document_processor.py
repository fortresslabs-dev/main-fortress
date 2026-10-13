"""Tests for DocumentProcessor — type detection, HTML/PDF/CSV/XML processing."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.document_processor import DocType, DocumentProcessor


# ── Type Detection ──────────────────────────────────────────────────


class TestTypeDetection:
    """Tests for _detect_type based on URL extension and Content-Type."""

    def setup_method(self):
        self.proc = DocumentProcessor()

    def test_pdf_extension(self):
        assert self.proc.detect_type("https://example.com/doc.pdf") == DocType.PDF

    def test_csv_extension(self):
        assert self.proc.detect_type("https://example.com/sdn.csv") == DocType.CSV

    def test_xml_extension(self):
        assert self.proc.detect_type("https://example.com/feed.xml") == DocType.XML

    def test_rss_extension(self):
        assert self.proc.detect_type("https://example.com/news.rss") == DocType.RSS

    def test_rss_path_pattern(self):
        assert self.proc.detect_type("https://example.com/feed/latest") == DocType.RSS

    @patch("src.services.document_processor.requests.Session.head")
    def test_content_type_pdf(self, mock_head):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_head.return_value = mock_resp
        assert self.proc.detect_type("https://example.com/some-document") == DocType.PDF

    @patch("src.services.document_processor.requests.Session.head")
    def test_content_type_html(self, mock_head):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_head.return_value = mock_resp
        assert self.proc.detect_type("https://example.com/page") == DocType.HTML

    @patch("src.services.document_processor.requests.Session.head")
    def test_head_failure_defaults_html(self, mock_head):
        import requests as rq
        mock_head.side_effect = rq.RequestException("timeout")
        assert self.proc.detect_type("https://example.com/unknown") == DocType.HTML


# ── HTML Processing ─────────────────────────────────────────────────


class TestHTMLProcessing:
    """Tests for HTML scraping and heading-based chunking."""

    def setup_method(self):
        self.proc = DocumentProcessor(chunk_size=500)

    @patch("src.services.document_processor.requests.Session.get")
    def test_heading_based_chunking(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b"""
        <html><body>
            <h2>Section A</h2>
            <p>This is content for section A about compliance.</p>
            <h2>Section B</h2>
            <p>This is content for section B about regulations.</p>
        </body></html>
        """
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        chunks = self.proc.process_url(
            "https://example.com/page",
            source="OCC",
            category="BSA",
            format_hint="html",
        )
        assert len(chunks) == 2
        assert chunks[0]["title"] == "Section A"
        assert chunks[1]["title"] == "Section B"
        assert chunks[0]["doc_type"] == "html"

    @patch("src.services.document_processor.requests.Session.get")
    def test_fallback_when_no_headings(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b"""
        <html><body>
            <div>Paragraph one with enough text to pass the 100 char minimum.
            This needs to be at least a hundred characters to trigger the fallback path.</div>
        </body></html>
        """
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        chunks = self.proc.process_url(
            "https://example.com/plain",
            source="FDIC",
            category="Compliance",
            format_hint="html",
        )
        assert len(chunks) >= 1
        assert chunks[0]["doc_type"] == "html"

    @patch("src.services.document_processor.requests.Session.get")
    def test_table_extraction(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b"""
        <html><body>
            <h2>Data Overview</h2>
            <p>Supporting text here.</p>
            <table>
                <caption>Compliance Thresholds</caption>
                <tr><th>Category</th><th>Threshold</th></tr>
                <tr><td>Wire Transfer</td><td>$3,000</td></tr>
                <tr><td>CTR</td><td>$10,000</td></tr>
            </table>
        </body></html>
        """
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        chunks = self.proc.process_url(
            "https://example.com/tables",
            source="OCC",
            category="BSA",
            format_hint="html",
        )
        # Should have heading chunk + table chunk
        doc_types = [c["doc_type"] for c in chunks]
        assert "html_table" in doc_types


# ── CSV Processing ──────────────────────────────────────────────────


class TestCSVProcessing:
    """Tests for CSV parsing and row-batch chunking."""

    def setup_method(self):
        self.proc = DocumentProcessor()

    @patch("src.services.document_processor.requests.Session.get")
    def test_csv_chunking(self, mock_get):
        rows = ["name,type,program"]
        for i in range(75):
            rows.append(f"Entity {i},Individual,SDN")
        csv_content = "\n".join(rows)

        mock_resp = MagicMock()
        mock_resp.text = csv_content
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        chunks = self.proc.process_url(
            "https://example.com/sdn.csv",
            source="OFAC",
            category="Sanctions",
        )
        # 76 rows (header + 75 data) → batches of 50 → 2 chunks
        assert len(chunks) == 2
        assert chunks[0]["doc_type"] == "csv"
        assert "Entity 0" in chunks[0]["content"]


# ── XML Processing ──────────────────────────────────────────────────


class TestXMLProcessing:
    """Tests for XML element-based chunking."""

    def setup_method(self):
        self.proc = DocumentProcessor()

    @patch("src.services.document_processor.requests.Session.get")
    def test_xml_chunking(self, mock_get):
        xml = """<?xml version="1.0"?>
        <sanctions>
            <entry name="Entity Alpha">
                <program>SDGT</program>
                <remarks>This is a long description for entity alpha sanctions entry.</remarks>
            </entry>
            <entry name="Entity Beta">
                <program>IRAN</program>
                <remarks>This is a long description for entity beta economic sanctions.</remarks>
            </entry>
        </sanctions>"""

        mock_resp = MagicMock()
        mock_resp.content = xml.encode()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        chunks = self.proc.process_url(
            "https://example.com/sdn.xml",
            source="OFAC",
            category="Sanctions",
        )
        assert len(chunks) == 2
        assert chunks[0]["doc_type"] == "xml"
        assert chunks[0]["title"] == "Entity Alpha"


# ── Text Splitting ──────────────────────────────────────────────────


class TestTextSplitting:
    """Tests for the _split_text helper."""

    def test_short_text_no_split(self):
        proc = DocumentProcessor(chunk_size=100)
        assert proc._split_text("Hello world") == ["Hello world"]

    def test_long_text_splits(self):
        proc = DocumentProcessor(chunk_size=50, chunk_overlap=10)
        text = "A" * 120
        chunks = proc._split_text(text)
        assert len(chunks) >= 2
        # All text should be covered
        assert all(len(c) <= 50 for c in chunks)


# ── Chunk Schema ────────────────────────────────────────────────────


class TestChunkSchema:
    """Verify chunk dicts contain all required fields."""

    def setup_method(self):
        self.proc = DocumentProcessor()

    @patch("src.services.document_processor.requests.Session.get")
    def test_chunk_has_required_fields(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b"<html><body><h2>Test</h2><p>Content here.</p></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        chunks = self.proc.process_url(
            "https://example.com/doc",
            source="OCC",
            category="BSA",
            format_hint="html",
        )
        assert len(chunks) >= 1
        required = {"source", "category", "title", "content", "url", "doc_type",
                     "chunk_index", "content_hash", "scraped_at"}
        for chunk in chunks:
            assert required.issubset(chunk.keys()), f"Missing: {required - chunk.keys()}"


# ── Error Handling ──────────────────────────────────────────────────


class TestErrorHandling:
    """Verify graceful failure on network and parse errors."""

    def setup_method(self):
        self.proc = DocumentProcessor()

    @patch("src.services.document_processor.requests.Session.get")
    def test_http_error_returns_empty(self, mock_get):
        import requests as rq
        mock_get.side_effect = rq.RequestException("Connection refused")

        chunks = self.proc.process_url(
            "https://example.com/broken",
            source="OCC",
            category="BSA",
            format_hint="html",
        )
        assert chunks == []
