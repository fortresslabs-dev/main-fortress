"""
Tests for sitemap-driven compliance URL discovery.

Covers:
  1. Sitemap XML parsing (standard + index)
  2. Compliance keyword filtering
  3. URL classification (HTML vs PDF)
  4. New vs known URL deduplication
  5. scrape_discovered_urls integration
  6. Connector integration (all 5 connectors call discovery)
  7. Error handling (network failures, malformed XML)
"""
from unittest.mock import MagicMock, patch, call

import pytest
import requests


# ---------------------------------------------------------------------------
# Test Data: Sitemap XML fixtures
# ---------------------------------------------------------------------------

STANDARD_SITEMAP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.example.gov/regulations/rule-1</loc></url>
  <url><loc>https://www.example.gov/guidance/advisory-2024</loc></url>
  <url><loc>https://www.example.gov/manual/chapter-3</loc></url>
  <url><loc>https://www.example.gov/bulletins/bulletin-2024-01</loc></url>
  <url><loc>https://www.example.gov/bsa/wire-transfer-guide</loc></url>
  <url><loc>https://www.example.gov/about-us/careers</loc></url>
  <url><loc>https://www.example.gov/news/press-release-42</loc></url>
  <url><loc>https://www.example.gov/compliance/handbook/ch1.pdf</loc></url>
  <url><loc>https://www.example.gov/aml/examination-procedures.pdf</loc></url>
  <url><loc>https://www.example.gov/images/logo.png</loc></url>
  <url><loc>https://www.example.gov/styles/main.css</loc></url>
</urlset>
"""

SITEMAP_INDEX_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.example.gov/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://www.example.gov/sitemap-guidance.xml</loc></sitemap>
</sitemapindex>
"""

CHILD_SITEMAP_PAGES = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.example.gov/regulations/new-rule</loc></url>
  <url><loc>https://www.example.gov/about-us/contact</loc></url>
</urlset>
"""

CHILD_SITEMAP_GUIDANCE = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.example.gov/guidance/advisory-2025</loc></url>
  <url><loc>https://www.example.gov/supervision/exam-manual</loc></url>
</urlset>
"""

# Bare XML (no namespace — some gov sites do this)
BARE_SITEMAP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>https://www.example.gov/regulations/bare-rule</loc></url>
  <url><loc>https://www.example.gov/guidance/bare-guide</loc></url>
  <url><loc>https://www.example.gov/about-us/bare</loc></url>
</urlset>
"""

MALFORMED_XML = "this is not valid xml <broken"

SAMPLE_HTML_WITH_HEADINGS = """
<html><body>
<main>
  <h2>Compliance Rule A</h2>
  <p>Details about compliance rule A for banking institutions under regulatory framework XYZ.</p>
  <h2>Compliance Rule B</h2>
  <p>Additional requirements for rule B covering anti-money laundering procedures.</p>
</main>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(content: str, status_code: int = 200, content_type: str = "text/xml") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content.encode("utf-8") if isinstance(content, str) else content
    resp.text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


def _mock_session_with_url_map(url_map: dict) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.headers = {}

    def get_side_effect(url, **kwargs):
        for pattern, resp in url_map.items():
            if pattern in url:
                return resp
        return _mock_response("Not Found", 404)

    session.get = MagicMock(side_effect=get_side_effect)
    session.head = MagicMock(return_value=_mock_response("", 200))
    return session


# ===================================================================
# 1. Sitemap XML Parsing
# ===================================================================

class TestParseSitemapXml:
    """Test _parse_sitemap_xml helper."""

    def test_parses_standard_sitemap(self):
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response(STANDARD_SITEMAP_XML),
        })

        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml")
        assert len(urls) == 11
        assert "https://www.example.gov/regulations/rule-1" in urls
        assert "https://www.example.gov/about-us/careers" in urls
        assert "https://www.example.gov/images/logo.png" in urls

    def test_parses_sitemap_index_recursively(self):
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        url_map = {
            "sitemap.xml": _mock_response(SITEMAP_INDEX_XML),
            "sitemap-pages.xml": _mock_response(CHILD_SITEMAP_PAGES),
            "sitemap-guidance.xml": _mock_response(CHILD_SITEMAP_GUIDANCE),
        }
        session = _mock_session_with_url_map(url_map)

        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml")
        assert len(urls) == 4
        assert "https://www.example.gov/regulations/new-rule" in urls
        assert "https://www.example.gov/guidance/advisory-2025" in urls
        assert "https://www.example.gov/supervision/exam-manual" in urls

    def test_parses_bare_xml_without_namespace(self):
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response(BARE_SITEMAP_XML),
        })

        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml")
        assert len(urls) == 3
        assert "https://www.example.gov/regulations/bare-rule" in urls

    def test_handles_network_failure(self):
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        session = MagicMock(spec=requests.Session)
        session.get = MagicMock(side_effect=requests.ConnectionError("dns fail"))

        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml")
        assert urls == []

    def test_handles_malformed_xml(self):
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response(MALFORMED_XML),
        })

        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml")
        assert urls == []

    def test_respects_max_depth(self):
        """Sitemap index deeper than max_depth should not recurse infinitely."""
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        # Create a self-referencing index (would loop without depth limit)
        self_ref = """\
<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.example.gov/sitemap.xml</loc></sitemap>
</sitemapindex>
"""
        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response(self_ref),
        })

        # Should not loop — max_depth=2 means 3 levels at most
        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml", max_depth=2)
        assert isinstance(urls, list)

    def test_http_404_returns_empty(self):
        from src.connectors.regulatory.usa._scraper_utils import _parse_sitemap_xml

        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response("Not Found", 404),
        })

        urls = _parse_sitemap_xml(session, "https://www.example.gov/sitemap.xml")
        assert urls == []


# ===================================================================
# 2. Compliance URL Discovery
# ===================================================================

class TestDiscoverComplianceUrls:
    """Test discover_compliance_urls filtering and classification."""

    def _run_discovery(self, existing_urls=None, keywords=None):
        from src.connectors.regulatory.usa._scraper_utils import discover_compliance_urls

        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response(STANDARD_SITEMAP_XML),
        })

        return discover_compliance_urls(
            session,
            agency="occ",
            sitemap_url="https://www.example.gov/sitemap.xml",
            keywords=keywords,
            existing_urls=existing_urls,
        )

    def test_filters_by_compliance_keywords(self):
        result = self._run_discovery()

        # Should include compliance-relevant URLs
        all_found = result["html_urls"] + result["pdf_urls"]
        assert any("/regulations/" in u for u in all_found)
        assert any("/guidance/" in u for u in all_found)
        assert any("/manual/" in u for u in all_found)
        assert any("/bulletins/" in u for u in all_found)
        assert any("/bsa/" in u for u in all_found)
        assert any("/aml/" in u for u in all_found)

        # Should NOT include non-compliance URLs
        all_urls_str = " ".join(all_found)
        assert "/about-us/careers" not in all_urls_str
        assert "/news/press-release" not in all_urls_str

    def test_classifies_html_vs_pdf(self):
        result = self._run_discovery()

        assert len(result["pdf_urls"]) == 2  # the two .pdf URLs
        assert all(u.endswith(".pdf") for u in result["pdf_urls"])

        # HTML URLs should not end in .pdf
        assert all(not u.endswith(".pdf") for u in result["html_urls"])

    def test_excludes_binary_assets(self):
        result = self._run_discovery()

        all_found = result["html_urls"] + result["pdf_urls"]
        assert not any(u.endswith(".png") for u in all_found)
        assert not any(u.endswith(".css") for u in all_found)

    def test_separates_new_vs_known(self):
        existing = {
            "https://www.example.gov/regulations/rule-1",
            "https://www.example.gov/guidance/advisory-2024",
        }
        result = self._run_discovery(existing_urls=existing)

        assert len(result["known_urls"]) == 2
        assert len(result["new_urls"]) > 0
        # New URLs should not be in existing
        for url in result["new_urls"]:
            assert url.split("#")[0].rstrip("/") not in existing

    def test_all_new_when_no_existing(self):
        result = self._run_discovery(existing_urls=set())

        assert len(result["new_urls"]) >= 5  # All compliance URLs are "new"
        assert len(result["known_urls"]) == 0

    def test_custom_keywords(self):
        result = self._run_discovery(keywords=["/careers"])

        # Only /about-us/careers should match custom keyword
        all_found = result["html_urls"] + result["pdf_urls"]
        assert len(all_found) == 1
        assert "/careers" in all_found[0]

    def test_returns_counts(self):
        result = self._run_discovery()

        assert result["all_sitemap_urls"] == 11
        assert result["compliance_urls"] >= 5

    def test_missing_agency_returns_empty(self):
        from src.connectors.regulatory.usa._scraper_utils import discover_compliance_urls

        session = MagicMock(spec=requests.Session)
        result = discover_compliance_urls(session, agency="nonexistent_agency")

        assert result["html_urls"] == []
        assert result["pdf_urls"] == []
        assert result["new_urls"] == []

    def test_max_urls_cap(self):
        from src.connectors.regulatory.usa._scraper_utils import discover_compliance_urls

        session = _mock_session_with_url_map({
            "sitemap.xml": _mock_response(STANDARD_SITEMAP_XML),
        })

        result = discover_compliance_urls(
            session, agency="occ",
            sitemap_url="https://www.example.gov/sitemap.xml",
            max_urls=2,
        )
        total = len(result["html_urls"]) + len(result["pdf_urls"])
        assert total <= 2


# ===================================================================
# 3. Scrape Discovered URLs
# ===================================================================

class TestScrapeDiscoveredUrls:
    """Test scrape_discovered_urls with mock HTTP."""

    def test_scrapes_new_html_urls(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_discovered_urls

        url_map = {
            "rule-1": _mock_response(SAMPLE_HTML_WITH_HEADINGS, content_type="text/html"),
            "advisory": _mock_response(SAMPLE_HTML_WITH_HEADINGS, content_type="text/html"),
        }
        session = _mock_session_with_url_map(url_map)

        discovered = {
            "html_urls": [
                "https://www.example.gov/regulations/rule-1",
                "https://www.example.gov/guidance/advisory",
            ],
            "pdf_urls": [],
            "new_urls": [
                "https://www.example.gov/regulations/rule-1",
                "https://www.example.gov/guidance/advisory",
            ],
            "known_urls": [],
        }

        docs = scrape_discovered_urls(
            session, discovered, source="TEST", category="Discovered",
        )
        assert len(docs) >= 2
        assert all(d["source"] == "TEST" for d in docs)

    def test_respects_max_html_cap(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_discovered_urls

        session = _mock_session_with_url_map({
            "example.gov": _mock_response(SAMPLE_HTML_WITH_HEADINGS, content_type="text/html"),
        })

        discovered = {
            "html_urls": [f"https://www.example.gov/regulations/rule-{i}" for i in range(20)],
            "pdf_urls": [],
            "new_urls": [f"https://www.example.gov/regulations/rule-{i}" for i in range(20)],
            "known_urls": [],
        }

        docs = scrape_discovered_urls(
            session, discovered, source="TEST", category="Discovered",
            max_html_pages=3,
        )
        # Should not scrape more than 3 HTML pages (though each may produce multiple docs)
        get_calls = [c for c in session.get.call_args_list]
        assert len(get_calls) <= 3

    def test_handles_html_scrape_failure(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_discovered_urls

        session = MagicMock(spec=requests.Session)
        session.get = MagicMock(side_effect=requests.ConnectionError("timeout"))
        session.headers = {}

        discovered = {
            "html_urls": ["https://www.example.gov/regulations/broken"],
            "pdf_urls": [],
            "new_urls": ["https://www.example.gov/regulations/broken"],
            "known_urls": [],
        }

        # Should not raise, just return empty
        docs = scrape_discovered_urls(session, discovered, source="TEST")
        assert docs == []

    def test_empty_discovered_returns_empty(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_discovered_urls

        session = MagicMock(spec=requests.Session)
        discovered = {"html_urls": [], "pdf_urls": [], "new_urls": [], "known_urls": []}

        docs = scrape_discovered_urls(session, discovered, source="TEST")
        assert docs == []


# ===================================================================
# 4. Connector Integration — Discovery Called
# ===================================================================

class TestConnectorSitemapIntegration:
    """Verify all 5 connectors call sitemap discovery in _scrape_live."""

    def _test_connector_calls_discovery(self, connector_class, module_path):
        """Generic test: connector._scrape_live() calls discover_compliance_urls."""
        c = connector_class()

        with patch(f"{module_path}._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch(f"{module_path}._scraper_utils.scrape_html_page", return_value=[]),
                patch(f"{module_path}._scraper_utils.scrape_pdf_url", return_value=[]),
                patch(f"{module_path}._scraper_utils.scrape_linked_pages", return_value=[]),
                patch(f"{module_path}._scraper_utils.scrape_fdic_risk_manual_sections", return_value=[]),
                patch(f"{module_path}._scraper_utils.scrape_hmda_fig_pages", return_value=[]),
                patch(f"{module_path}._scraper_utils.resolve_fincen_sar_pdf", return_value=None),
                patch(f"{module_path}._scraper_utils.url_health_check", return_value={}),
                patch(f"{module_path}._scraper_utils.check_rss_feed", return_value=[]),
                patch(f"{module_path}._scraper_utils.discover_compliance_urls") as mock_discover,
                patch(f"{module_path}._scraper_utils.scrape_discovered_urls", return_value=[]),
            ):
                mock_discover.return_value = {
                    "html_urls": [], "pdf_urls": [], "new_urls": [],
                    "known_urls": [], "all_sitemap_urls": 0, "compliance_urls": 0,
                }
                # Some connectors have extra methods
                if hasattr(c, '_scrape_fils'):
                    c._scrape_fils = MagicMock(return_value=[])

                c._scrape_live()

                mock_discover.assert_called_once()

    def test_occ_calls_discovery(self):
        from src.connectors.regulatory.usa.occ_bsa import OccBsaConnector
        self._test_connector_calls_discovery(
            OccBsaConnector, "src.connectors.regulatory.usa"
        )

    def test_fdic_calls_discovery(self):
        from src.connectors.regulatory.usa.fdic import FdicConnector
        self._test_connector_calls_discovery(
            FdicConnector, "src.connectors.regulatory.usa"
        )

    def test_cfpb_calls_discovery(self):
        from src.connectors.regulatory.usa.cfpb import CfpbConnector
        self._test_connector_calls_discovery(
            CfpbConnector, "src.connectors.regulatory.usa"
        )

    def test_fincen_calls_discovery(self):
        from src.connectors.regulatory.usa.fincen_sar import FincenSarConnector
        self._test_connector_calls_discovery(
            FincenSarConnector, "src.connectors.regulatory.usa"
        )

    def test_ofac_calls_discovery(self):
        from src.connectors.regulatory.usa.ofac_sdn import OfacSdnConnector
        self._test_connector_calls_discovery(
            OfacSdnConnector, "src.connectors.regulatory.usa"
        )

    def test_connector_survives_discovery_failure(self):
        """If sitemap discovery raises, connector should still return docs."""
        from src.connectors.regulatory.usa.occ_bsa import OccBsaConnector

        c = OccBsaConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page") as mock_html,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.check_rss_feed", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.discover_compliance_urls") as mock_discover,
            ):
                mock_html.return_value = [{"title": "existing", "content": "data"}]
                mock_discover.side_effect = Exception("Sitemap parse failed!")

                docs = c._scrape_live()

                # Should still have docs from hardcoded scraping
                assert len(docs) >= 1
                assert docs[0]["title"] == "existing"


# ===================================================================
# 5. AGENCY_SITEMAPS and COMPLIANCE_KEYWORDS constants
# ===================================================================

class TestConstants:
    """Verify the constants are properly configured."""

    def test_agency_sitemaps_has_all_five(self):
        from src.connectors.regulatory.usa._scraper_utils import AGENCY_SITEMAPS

        assert "occ" in AGENCY_SITEMAPS
        assert "fdic" in AGENCY_SITEMAPS
        assert "cfpb" in AGENCY_SITEMAPS
        assert "fincen" in AGENCY_SITEMAPS
        assert "ofac" in AGENCY_SITEMAPS
        assert len(AGENCY_SITEMAPS) == 5

    def test_agency_sitemaps_urls_are_valid(self):
        from src.connectors.regulatory.usa._scraper_utils import AGENCY_SITEMAPS

        for agency, url in AGENCY_SITEMAPS.items():
            assert url.startswith("https://"), f"{agency} URL should be HTTPS"
            assert url.endswith(".xml"), f"{agency} URL should end with .xml"

    def test_compliance_keywords_are_lowercase_paths(self):
        from src.connectors.regulatory.usa._scraper_utils import COMPLIANCE_KEYWORDS

        for kw in COMPLIANCE_KEYWORDS:
            assert kw.startswith("/"), f"Keyword {kw} should start with /"
            assert kw.endswith("/"), f"Keyword {kw} should end with /"
            assert kw == kw.lower(), f"Keyword {kw} should be lowercase"

    def test_compliance_keywords_has_core_terms(self):
        from src.connectors.regulatory.usa._scraper_utils import COMPLIANCE_KEYWORDS

        core = ["/regulations/", "/guidance/", "/manual/", "/bulletins/", "/bsa/", "/aml/"]
        for kw in core:
            assert kw in COMPLIANCE_KEYWORDS, f"Missing core keyword: {kw}"
