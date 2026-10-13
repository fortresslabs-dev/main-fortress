"""Tests for DocumentPipelineAgent — planning, source merging, execution."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.document_pipeline_agent import DocumentPipelineAgent, ProcessingPlan


class TestSourceMerging:
    """Verify compliance_sources.yaml and usa.yaml are merged correctly."""

    @patch.object(DocumentPipelineAgent, "_load_source_urls")
    @patch.object(DocumentPipelineAgent, "_load_compliance_sources")
    def test_merge_compliance_and_url_sources(self, mock_cs, mock_urls):
        mock_cs.return_value = [
            {
                "source_id": "ofac_sdn",
                "regulator": "OFAC",
                "category": "Sanctions",
                "url": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                "format": "csv",
                "priority": 1,
                "fields": ["entity_name", "entity_type"],
            }
        ]
        mock_urls.return_value = {
            "ofac_sdn": {
                "sdn_csv": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                "sdn_xml": "https://example.com/sdn.xml",  # extra URL
            }
        }

        agent = DocumentPipelineAgent()
        merged = agent._merge_sources()

        assert "ofac_sdn" in merged
        # Should have the original CSV URL + the extra XML URL (deduped)
        assert len(merged["ofac_sdn"]["urls"]) == 2
        assert merged["ofac_sdn"]["format"] == "csv"
        assert merged["ofac_sdn"]["priority"] == 1

    @patch.object(DocumentPipelineAgent, "_load_source_urls")
    @patch.object(DocumentPipelineAgent, "_load_compliance_sources")
    def test_url_only_source_added(self, mock_cs, mock_urls):
        """Source in usa.yaml but NOT in compliance_sources.yaml should still appear."""
        mock_cs.return_value = []
        mock_urls.return_value = {
            "new_connector": {
                "main_page": "https://example.com/new",
            }
        }

        agent = DocumentPipelineAgent()
        merged = agent._merge_sources()

        assert "new_connector" in merged
        assert merged["new_connector"]["urls"] == ["https://example.com/new"]
        assert merged["new_connector"]["format"] == ""  # auto-detect


class TestPlanning:
    """Verify plan() generates correct ProcessingPlan objects."""

    @patch.object(DocumentPipelineAgent, "_load_source_urls")
    @patch.object(DocumentPipelineAgent, "_load_compliance_sources")
    def test_plan_generates_plans(self, mock_cs, mock_urls):
        mock_cs.return_value = [
            {
                "source_id": "test_src",
                "regulator": "TEST",
                "category": "Testing",
                "url": "https://example.com/doc.pdf",
                "format": "pdf",
                "priority": 1,
                "fields": ["field_a"],
            }
        ]
        mock_urls.return_value = {}

        agent = DocumentPipelineAgent()
        plans = agent.plan()

        assert len(plans) == 1
        plan = plans[0]
        assert plan.source_id == "test_src"
        assert plan.url == "https://example.com/doc.pdf"
        assert plan.detected_type == "pdf"
        assert plan.chunking_strategy == "unstructured_title"

    @patch.object(DocumentPipelineAgent, "_load_source_urls")
    @patch.object(DocumentPipelineAgent, "_load_compliance_sources")
    def test_filter_limits_sources(self, mock_cs, mock_urls):
        mock_cs.return_value = [
            {"source_id": "src_a", "regulator": "A", "category": "A", "url": "https://a.com", "format": "html", "priority": 1},
            {"source_id": "src_b", "regulator": "B", "category": "B", "url": "https://b.com", "format": "html", "priority": 2},
        ]
        mock_urls.return_value = {}

        agent = DocumentPipelineAgent(sources_filter=["src_a"])
        plans = agent.plan()

        assert len(plans) == 1
        assert plans[0].source_id == "src_a"


class TestChunkingStrategy:
    """Verify correct strategy selection per DocType."""

    def test_html_strategy(self):
        from src.services.document_processor import DocType
        strategy = DocumentPipelineAgent._pick_chunking_strategy(DocType.HTML, "html")
        assert strategy == "heading_based"

    def test_pdf_strategy(self):
        from src.services.document_processor import DocType
        strategy = DocumentPipelineAgent._pick_chunking_strategy(DocType.PDF, "pdf")
        assert strategy == "unstructured_title"

    def test_csv_strategy(self):
        from src.services.document_processor import DocType
        strategy = DocumentPipelineAgent._pick_chunking_strategy(DocType.CSV, "csv")
        assert strategy == "row_batch"

    def test_xml_strategy(self):
        from src.services.document_processor import DocType
        strategy = DocumentPipelineAgent._pick_chunking_strategy(DocType.XML, "xml")
        assert strategy == "element_tree"


class TestQualityNotes:
    """Verify quality notes for format mismatches and PDF warnings."""

    def test_format_mismatch_note(self):
        from src.services.document_processor import DocType
        notes = DocumentPipelineAgent._quality_notes(DocType.HTML, "pdf", "https://example.com")
        assert any("mismatch" in n.lower() for n in notes)

    def test_pdf_paid_api_note(self):
        from src.services.document_processor import DocType
        notes = DocumentPipelineAgent._quality_notes(DocType.PDF, "pdf", "https://example.com/doc.pdf")
        assert any("paid api" in n.lower() for n in notes)
