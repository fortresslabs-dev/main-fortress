"""
Unit tests for all 5 USA regulatory connectors with mocked HTTP responses.

Tests structured rule extraction (no network) and scraping (mocked HTTP).
"""
import hashlib
from unittest.mock import MagicMock, patch

import pytest
import requests


# ---------------------------------------------------------------------------
# Auto-patch _scrape_live for all tests in this module so they never
# hit real URLs or trigger slow unstructured imports.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_live_scraping():
    """Prevent live HTTP calls + unstructured loading in connector unit tests."""
    with (
        patch("src.connectors.regulatory.usa.occ_bsa.OccBsaConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.fdic.FdicConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.cfpb.CfpbConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.fincen_sar.FincenSarConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.ofac_sdn.OfacSdnConnector._scrape_live", return_value=[]),
    ):
        yield

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_HTML_WITH_HEADINGS = """
<html><body>
<main>
  <h2>Section Alpha</h2>
  <p>Alpha paragraph one.</p>
  <p>Alpha paragraph two.</p>
  <h2>Section Beta</h2>
  <p>Beta content here with details.</p>
  <h3>Sub-section Beta-1</h3>
  <p>Nested content for beta one.</p>
</main>
</body></html>
"""

SAMPLE_HTML_NO_HEADINGS = """
<html><body>
<article>
  <h1>Page Title</h1>
  <p>This is the full page body without sub-headings. It contains more than one hundred characters of meaningful content that should be captured by the article-level fallback.</p>
</article>
</body></html>
"""

SAMPLE_SDN_CSV = (
    '"12345","DOE, John","individual","SDGT","","","","","","","",""\n'
    '"12346","EVIL CORP","entity","SDGT; IRAN","","","","","","","",""\n'
    '"12347","SMITH, Jane","individual","UKRAINE-EO13662","","","","","","","",""\n'
    '"-0-","","","","","","","","","","",""\n'
)


def _mock_response(content: str, status_code: int = 200) -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content.encode("utf-8")
    resp.text = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            response=resp
        )
    return resp


def _mock_session(*responses):
    """Return a mock Session whose .get() returns responses in order."""
    session = MagicMock(spec=requests.Session)
    session.get = MagicMock(side_effect=list(responses))
    session.headers = {}
    return session


# ===================================================================
# 1. OCC BSA/AML Connector
# ===================================================================

class TestOccBsaConnector:
    """Test OccBsaConnector structured rules and scraping."""

    def _make_connector(self):
        from src.connectors.regulatory.usa.occ_bsa import OccBsaConnector
        return OccBsaConnector()

    def test_structured_rules_present(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        assert "regulations" in result
        assert "requirements" in result
        assert "kpis" in result
        assert "test_scenarios" in result
        assert len(result["regulations"]) >= 3
        assert len(result["requirements"]) >= 8
        assert len(result["kpis"]) >= 5
        assert len(result["test_scenarios"]) >= 8

    def test_regulation_ids_unique(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        ids = [r["regulation_id"] for r in result["regulations"]]
        assert len(ids) == len(set(ids))

    def test_wire_transfer_rule_exists(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        rule_ids = [r["regulation_id"] for r in result["regulations"]]
        assert "OCC-BSA-WIRE-001" in rule_ids

    def test_requirements_have_required_keys(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        for req in result["requirements"]:
            assert "requirement_id" in req
            assert "requirement_text" in req or "description" in req

    def test_connect_disconnect(self):
        c = self._make_connector()
        assert c.connect() is True
        assert c.connected is True
        c.disconnect()
        assert c.connected is False


# ===================================================================
# 2. FDIC Connector
# ===================================================================

class TestFdicConnector:
    """Test FdicConnector structured rules and scraping."""

    def _make_connector(self):
        from src.connectors.regulatory.usa.fdic import FdicConnector
        return FdicConnector()

    def test_structured_rules_present(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        assert len(result["regulations"]) >= 3
        assert len(result["requirements"]) >= 4
        assert len(result["kpis"]) >= 2
        assert len(result["test_scenarios"]) >= 3

    def test_deposit_insurance_rule(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        rule_texts = " ".join(
            r.get("regulation_name", "") + r.get("category", "")
            for r in result["regulations"]
        ).lower()
        assert "deposit" in rule_texts or "fdic" in rule_texts

    def test_regulation_ids_unique(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        ids = [r["regulation_id"] for r in result["regulations"]]
        assert len(ids) == len(set(ids))

    def test_connect_disconnect(self):
        c = self._make_connector()
        assert c.connect() is True
        c.disconnect()
        assert c.connected is False


# ===================================================================
# 3. CFPB Connector
# ===================================================================

class TestCfpbConnector:

    def _make_connector(self):
        from src.connectors.regulatory.usa.cfpb import CfpbConnector
        return CfpbConnector()

    def test_structured_rules_present(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        assert len(result["regulations"]) >= 4  # 1071, HMDA, RegB, RegE
        assert len(result["requirements"]) >= 9
        assert len(result["kpis"]) >= 5
        assert len(result["test_scenarios"]) >= 8

    def test_reg_e_present(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        reg_ids = [r["regulation_id"] for r in result["regulations"]]
        assert "CFPB-REGE-001" in reg_ids

    def test_reg_e_requirements_present(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        rege_reqs = [
            r for r in result["requirements"]
            if "REGE" in r.get("requirement_id", "")
        ]
        assert len(rege_reqs) >= 4

    def test_section_1071_regulation(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        reg_ids = [r["regulation_id"] for r in result["regulations"]]
        assert "CFPB-1071-001" in reg_ids

    def test_regulation_ids_unique(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        ids = [r["regulation_id"] for r in result["regulations"]]
        assert len(ids) == len(set(ids))


# ===================================================================
# 4. FinCEN SAR Connector
# ===================================================================

class TestFincenSarConnector:

    def _make_connector(self):
        from src.connectors.regulatory.usa.fincen_sar import FincenSarConnector
        return FincenSarConnector()

    def test_structured_rules_present(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        assert len(result["regulations"]) >= 2
        assert len(result["requirements"]) >= 6
        assert len(result["kpis"]) >= 3
        assert len(result["test_scenarios"]) >= 6

    def test_sar_filing_triggers(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        all_text = " ".join(
            r.get("requirement_text", "") + r.get("description", "")
            for r in result["requirements"]
        ).lower()
        assert "sar" in all_text or "suspicious" in all_text

    def test_30_day_deadline_in_requirements(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        all_text = " ".join(
            r.get("requirement_text", "") + r.get("description", "")
            for r in result["requirements"]
        ).lower()
        assert "30" in all_text or "calendar" in all_text

    def test_regulation_ids_unique(self):
        c = self._make_connector()
        result = c.extract_all_rules()
        ids = [r["regulation_id"] for r in result["regulations"]]
        assert len(ids) == len(set(ids))


# ===================================================================
# 5. OFAC SDN Connector
# ===================================================================

class TestOfacSdnConnector:

    def _make_connector(self):
        from src.connectors.regulatory.usa.ofac_sdn import OfacSdnConnector
        return OfacSdnConnector()

    def test_structured_rules_with_mocked_csv(self):
        c = self._make_connector()
        with patch("src.connectors.regulatory.usa.ofac_sdn.requests.get") as mock_get:
            mock_resp = _mock_response(SAMPLE_SDN_CSV)
            mock_resp.iter_lines = MagicMock(
                return_value=iter(SAMPLE_SDN_CSV.encode().splitlines())
            )
            mock_get.return_value = mock_resp

            result = c.extract_all_rules()
            assert len(result["regulations"]) >= 1
            assert len(result["requirements"]) >= 4
            assert len(result["kpis"]) >= 3
            assert len(result["test_scenarios"]) >= 5
            assert len(result["sdn_entries"]) == 3

    def test_sdn_entity_fields(self):
        c = self._make_connector()
        with patch("src.connectors.regulatory.usa.ofac_sdn.requests.get") as mock_get:
            mock_resp = _mock_response(SAMPLE_SDN_CSV)
            mock_resp.iter_lines = MagicMock(
                return_value=iter(SAMPLE_SDN_CSV.encode().splitlines())
            )
            mock_get.return_value = mock_resp

            result = c.extract_all_rules()
            entry = result["sdn_entries"][0]
            assert "uid" in entry
            assert "full_name" in entry
            assert "sdn_type" in entry

    def test_connection_check(self):
        c = self._make_connector()
        with patch("src.connectors.regulatory.usa.ofac_sdn.requests.head") as mock_head:
            mock_head.return_value = _mock_response("", 200)
            assert c.test_connection() is True


# ===================================================================
# 6. Scraper Utils
# ===================================================================

class TestScraperUtils:
    """Test _scraper_utils helper functions with mocked HTTP."""

    def test_scrape_html_page_heading_extraction(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_html_page
        session = _mock_session(_mock_response(SAMPLE_HTML_WITH_HEADINGS))

        docs = scrape_html_page(session, "https://example.gov/test", "TEST", "Cat")
        assert len(docs) >= 2
        assert docs[0]["source"] == "TEST"
        assert docs[0]["category"] == "Cat"
        assert "content_hash" in docs[0]
        assert docs[0]["title"] == "Section Alpha"

    def test_scrape_html_page_article_fallback(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_html_page
        session = _mock_session(_mock_response(SAMPLE_HTML_NO_HEADINGS))

        docs = scrape_html_page(session, "https://example.gov/page", "TEST", "Cat")
        assert len(docs) == 1
        assert docs[0]["title"] == "Page Title"
        assert len(docs[0]["content"]) > 100

    def test_scrape_html_page_http_error(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_html_page
        session = _mock_session(_mock_response("Not Found", 404))

        docs = scrape_html_page(session, "https://example.gov/404", "TEST", "Cat")
        assert docs == []

    def test_content_hash_is_sha256(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_html_page
        session = _mock_session(_mock_response(SAMPLE_HTML_WITH_HEADINGS))

        docs = scrape_html_page(session, "https://example.gov/test", "TEST", "Cat")
        for doc in docs:
            assert len(doc["content_hash"]) == 64

    def test_make_session(self):
        from src.connectors.regulatory.usa._scraper_utils import make_session
        s = make_session()
        assert isinstance(s, requests.Session)
        assert "TextMind" in s.headers.get("User-Agent", "")

    def test_scrape_pdf_url_without_unstructured(self):
        from unittest.mock import patch
        from src.connectors.regulatory.usa._scraper_utils import scrape_pdf_url
        session = _mock_session(_mock_response(b"%PDF-fake".decode()))
        # Mock unstructured away to test graceful fallback
        with patch.dict("sys.modules", {"unstructured.chunking.title": None, "unstructured.partition.auto": None}):
            docs = scrape_pdf_url(session, "https://example.gov/test.pdf", "TEST", "Cat", save_local=False)
        # unstructured import blocked, so should return []
        assert isinstance(docs, list)
        assert docs == []


# ===================================================================
# 7. PluginManager Connector Loading
# ===================================================================

class TestPluginManager:
    """Test PluginManager loads the core USA regulatory connectors."""

    def test_loads_all_regulatory_connectors(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        connector_dict = mgr.load_connectors()

        regulatory = {
            k: v for k, v in connector_dict.items()
            if k.startswith("regulatory.")
        }
        assert len(regulatory) == 6

        expected_classes = {
            "OccBsaConnector",
            "FdicConnector",
            "CfpbConnector",
            "FincenSarConnector",
            "FincenPart1010Connector",
            "OfacSdnConnector",
        }
        actual_classes = {type(v).__name__ for v in regulatory.values()}
        assert actual_classes == expected_classes

    def test_connector_names_match_config(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        connector_dict = mgr.load_connectors()

        regulatory_keys = sorted(
            k for k in connector_dict if k.startswith("regulatory.")
        )
        assert "regulatory.occ_bsa" in regulatory_keys
        assert "regulatory.fdic" in regulatory_keys
        assert "regulatory.cfpb" in regulatory_keys
        assert "regulatory.fincen_sar" in regulatory_keys
        assert "regulatory.fincen_part1010" in regulatory_keys
        assert "regulatory.ofac_sdn" in regulatory_keys


# ===================================================================
# 8. PDF Pipeline
# ===================================================================

class TestPDFPipeline:
    """Test PDFPipeline download, extract, and local copy features."""

    def test_download_pdf_saves_locally(self, tmp_path):
        from src.services.pdf_pipeline import PDFPipeline

        pipeline = PDFPipeline(pdf_dir=tmp_path)
        pipeline._session = _mock_session(
            _mock_response("%PDF-1.4 fake pdf content here")
        )

        result = pipeline._download_pdf(
            "https://example.gov/docs/handbook.pdf", "OCC"
        )
        assert result is not None
        assert result.exists()
        assert result.name == "handbook.pdf"
        assert "OCC" in str(result.parent)

    def test_download_pdf_creates_regulator_dirs(self, tmp_path):
        from src.services.pdf_pipeline import PDFPipeline

        pipeline = PDFPipeline(pdf_dir=tmp_path)
        pipeline._session = _mock_session(
            _mock_response("%PDF-1.4 content"),
            _mock_response("%PDF-1.4 content"),
        )

        pipeline._download_pdf("https://ex.gov/a.pdf", "OCC")
        pipeline._download_pdf("https://ex.gov/b.pdf", "FDIC")

        assert (tmp_path / "OCC").is_dir()
        assert (tmp_path / "FDIC").is_dir()

    def test_download_pdf_http_error(self, tmp_path):
        from src.services.pdf_pipeline import PDFPipeline

        pipeline = PDFPipeline(pdf_dir=tmp_path)
        pipeline._session = _mock_session(_mock_response("Not Found", 404))

        result = pipeline._download_pdf("https://ex.gov/missing.pdf", "OCC")
        assert result is None

    def test_extract_only_returns_chunks(self, tmp_path):
        """Test extract_only with mocked download + mocked unstructured."""
        from unittest.mock import MagicMock
        from src.services.pdf_pipeline import PDFPipeline

        pipeline = PDFPipeline(pdf_dir=tmp_path)

        # Mock the download to return a temp file
        fake_pdf = tmp_path / "OCC" / "test.pdf"
        fake_pdf.parent.mkdir(parents=True, exist_ok=True)
        fake_pdf.write_bytes(b"%PDF-1.4 fake content")

        with patch.object(pipeline, "_download_pdf", return_value=fake_pdf):
            with patch.object(pipeline, "_extract_chunks") as mock_extract:
                mock_extract.return_value = [
                    {
                        "doc_id": "OCC-abc123",
                        "regulator": "OCC",
                        "document_title": "Test",
                        "section_title": "Section 1",
                        "content": "Test content here",
                        "page_number": 1,
                        "chunk_index": 0,
                        "source_url": "https://ex.gov/test.pdf",
                        "document_type": "handbook",
                        "content_hash": "abc123",
                        "scraped_at": "2026-04-06T00:00:00",
                    }
                ]

                chunks = pipeline.extract_only([{
                    "url": "https://ex.gov/test.pdf",
                    "regulator": "OCC",
                    "category": "BSA/AML",
                    "doc_type": "handbook",
                }])

                assert len(chunks) == 1
                assert chunks[0]["regulator"] == "OCC"
                assert chunks[0]["document_type"] == "handbook"

    def test_chunk_metadata_keys(self, tmp_path):
        """Verify all expected metadata keys are present on chunks."""
        from src.services.pdf_pipeline import PDFPipeline

        pipeline = PDFPipeline(pdf_dir=tmp_path)

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")

        with patch.object(pipeline, "_download_pdf", return_value=fake_pdf):
            with patch.object(pipeline, "_extract_chunks") as mock_extract:
                mock_extract.return_value = [{
                    "doc_id": "X-123",
                    "regulator": "TEST",
                    "document_title": "Doc",
                    "section_title": "Sec",
                    "content": "Content",
                    "page_number": 1,
                    "chunk_index": 0,
                    "source_url": "https://ex.gov/t.pdf",
                    "document_type": "handbook",
                    "content_hash": "hash",
                    "scraped_at": "2026-01-01",
                }]

                chunks = pipeline.extract_only([{
                    "url": "https://ex.gov/t.pdf",
                    "regulator": "TEST",
                    "category": "Cat",
                    "doc_type": "handbook",
                }])

                expected_keys = {
                    "doc_id", "regulator", "document_title", "section_title",
                    "content", "page_number", "chunk_index", "source_url",
                    "document_type", "content_hash", "scraped_at",
                }
                assert set(chunks[0].keys()) == expected_keys
