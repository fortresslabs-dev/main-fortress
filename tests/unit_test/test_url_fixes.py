"""
Tests for Tier 1 broken URL fixes:
  1. FDIC Risk Management Manual — section-by-section TOC crawl
  2. CFPB HMDA FIG — static HTML sub-page crawl
  3. FinCEN SAR — year-walking PDF URL resolver
  4. URL health check utility
"""
from unittest.mock import MagicMock, patch, call
from datetime import datetime

import pytest
import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(content: str, status_code: int = 200, content_type: str = "text/html") -> MagicMock:
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
    """Return a mock Session whose .get()/.head() returns responses based on URL."""
    session = MagicMock(spec=requests.Session)
    session.headers = {}

    def get_side_effect(url, **kwargs):
        for pattern, resp in url_map.items():
            if pattern in url:
                return resp
        return _mock_response("Not Found", 404)

    def head_side_effect(url, **kwargs):
        for pattern, resp in url_map.items():
            if pattern in url:
                head_resp = MagicMock()
                head_resp.status_code = resp.status_code
                head_resp.headers = resp.headers
                return head_resp
        head_resp = MagicMock()
        head_resp.status_code = 404
        head_resp.headers = {}
        return head_resp

    session.get = MagicMock(side_effect=get_side_effect)
    session.head = MagicMock(side_effect=head_side_effect)
    return session


# ---------------------------------------------------------------------------
# FDIC Risk Management Manual TOC HTML
# ---------------------------------------------------------------------------

FDIC_RISK_MANUAL_TOC = """
<html><body>
<main>
  <h1>Risk Management Manual of Examination Policies</h1>
  <ul>
    <li><a href="/risk-management-manual-examination-policies/section-31-asset-quality">Section 3.1 — Asset Quality</a></li>
    <li><a href="/risk-management-manual-examination-policies/section-41-management">Section 4.1 — Management</a></li>
    <li><a href="/risk-management-manual-examination-policies/section-51-earnings.pdf">Section 5.1 — Earnings (PDF)</a></li>
  </ul>
</main>
</body></html>
"""

FDIC_SECTION_31 = """
<html><body>
<main>
  <h1>Section 3.1 — Asset Quality</h1>
  <h2>Classification of Assets</h2>
  <p>Banks must classify assets based on credit quality, considering the borrower's ability to repay. Assets should be categorized as pass, special mention, substandard, doubtful, or loss.</p>
  <h2>Allowance for Loan Losses</h2>
  <p>The allowance for loan and lease losses should be maintained at a level adequate to absorb estimated credit losses associated with the loan and lease portfolio.</p>
</main>
</body></html>
"""

FDIC_SECTION_41 = """
<html><body>
<main>
  <h1>Section 4.1 — Management</h1>
  <h2>Board of Directors</h2>
  <p>The board of directors is responsible for establishing policies and overseeing bank management. Directors should be independent of undue influence and conflicts of interest.</p>
</main>
</body></html>
"""


class TestFdicRiskManualTocCrawl:
    """Test FDIC Risk Management Manual section-by-section crawl."""

    def test_toc_crawl_finds_sections(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_fdic_risk_manual_sections

        url_map = {
            "risk-management-manual-examination-policies": _mock_response(FDIC_RISK_MANUAL_TOC),
            "section-31": _mock_response(FDIC_SECTION_31),
            "section-41": _mock_response(FDIC_SECTION_41),
        }
        session = _mock_session_with_url_map(url_map)

        docs = scrape_fdic_risk_manual_sections(
            session,
            toc_url="https://www.fdic.gov/risk-management-manual-examination-policies",
        )

        # Should have docs from TOC page + 2 HTML sections (PDF section will 404)
        assert len(docs) >= 2
        sources = {d["source"] for d in docs}
        assert "FDIC" in sources
        categories = {d["category"] for d in docs}
        assert "Risk Management Manual" in categories

    def test_toc_crawl_handles_failure(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_fdic_risk_manual_sections

        session = MagicMock(spec=requests.Session)
        session.get = MagicMock(side_effect=requests.ConnectionError("timeout"))

        docs = scrape_fdic_risk_manual_sections(
            session,
            toc_url="https://www.fdic.gov/risk-management-manual-examination-policies",
        )
        assert docs == []

    def test_toc_crawl_deduplicates_urls(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_fdic_risk_manual_sections

        toc_with_dups = """
        <html><body><main>
          <a href="/risk-management-manual-examination-policies/section-31-asset-quality">Link 1</a>
          <a href="/risk-management-manual-examination-policies/section-31-asset-quality#top">Link 1 again</a>
          <a href="/risk-management-manual-examination-policies/section-41-management">Link 2</a>
        </main></body></html>
        """
        url_map = {
            "risk-management-manual-examination-policies": _mock_response(toc_with_dups),
            "section-31": _mock_response(FDIC_SECTION_31),
            "section-41": _mock_response(FDIC_SECTION_41),
        }
        session = _mock_session_with_url_map(url_map)

        docs = scrape_fdic_risk_manual_sections(session, toc_url="https://www.fdic.gov/risk-management-manual-examination-policies")
        # section-31 should only be crawled once despite duplicate links
        get_calls = session.get.call_args_list
        section_31_calls = [c for c in get_calls if "section-31" in str(c)]
        assert len(section_31_calls) <= 1

    def test_fdic_connector_uses_toc_crawl(self):
        """Verify FdicConnector._scrape_live uses TOC crawl, falling back to PDF."""
        from src.connectors.regulatory.usa.fdic import FdicConnector

        c = FdicConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_fdic_risk_manual_sections") as mock_toc,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_linked_pages", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.url_health_check", return_value={}),
            ):
                mock_toc.return_value = [{"title": "Section 1", "content": "test"}]
                c._scrape_fils = MagicMock(return_value=[])

                docs = c._scrape_live()

                # TOC crawl should have been called
                mock_toc.assert_called_once()

    def test_fdic_connector_falls_back_to_pdf_when_toc_empty(self):
        """If TOC crawl returns empty, should fall back to legacy PDF."""
        from src.connectors.regulatory.usa.fdic import FdicConnector

        c = FdicConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_fdic_risk_manual_sections") as mock_toc,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_linked_pages", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url") as mock_pdf,
                patch("src.connectors.regulatory.usa._scraper_utils.url_health_check", return_value={}),
            ):
                mock_toc.return_value = []  # Empty TOC
                mock_pdf.return_value = [{"title": "PDF chunk"}]
                c._scrape_fils = MagicMock(return_value=[])

                docs = c._scrape_live()

                # PDF fallback should have been called
                mock_pdf.assert_called()


# ---------------------------------------------------------------------------
# CFPB HMDA FIG HTML Crawler
# ---------------------------------------------------------------------------

HMDA_FIG_OVERVIEW = """
<html><body>
<div id="root">
  <div class="App">
    <h1>HMDA Filing Instructions Guide — 2026</h1>
    <p>This guide provides instructions for collecting, reporting, and submitting HMDA data.</p>
    <nav>
      <a href="/documentation/fig/2026/data-points">Data Points</a>
      <a href="/documentation/fig/2026/filing-instructions">Filing Instructions</a>
      <a href="/documentation/fig/2026/edit-checks">Edit Checks</a>
      <a href="https://external.example.com/other">External Link</a>
    </nav>
  </div>
</div>
</body></html>
"""

HMDA_FIG_DATA_POINTS = """
<html><body>
<div class="App">
  <h1>Data Points</h1>
  <h2>Application Date</h2>
  <p>Enter the date the application was received or the date shown on the application. Report in YYYYMMDD format.</p>
  <h2>Loan Type</h2>
  <p>1 — Conventional. 2 — FHA insured. 3 — VA guaranteed. 4 — USDA/FSA guaranteed.</p>
</div>
</body></html>
"""

HMDA_FIG_EDIT_CHECKS = """
<html><body>
<div class="App">
  <h1>Edit Checks</h1>
  <h2>Syntactical Edits</h2>
  <p>Syntactical edits check that the values entered are in the correct format and within valid ranges for each field.</p>
</div>
</body></html>
"""


class TestHmdaFigHtmlCrawl:
    """Test CFPB HMDA FIG static HTML crawling."""

    def test_fig_crawl_extracts_subpages(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_hmda_fig_pages

        url_map = {
            "overview": _mock_response(HMDA_FIG_OVERVIEW),
            "data-points": _mock_response(HMDA_FIG_DATA_POINTS),
            "filing-instructions": _mock_response(HMDA_FIG_DATA_POINTS),  # reuse
            "edit-checks": _mock_response(HMDA_FIG_EDIT_CHECKS),
        }
        session = _mock_session_with_url_map(url_map)

        docs = scrape_hmda_fig_pages(
            session,
            overview_url="https://ffiec.cfpb.gov/documentation/fig/2026/overview",
        )

        assert len(docs) >= 3
        assert all(d["source"] == "CFPB" for d in docs)
        assert all(d["category"] == "HMDA Filing Guide" for d in docs)

    def test_fig_crawl_skips_external_links(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_hmda_fig_pages

        url_map = {
            "overview": _mock_response(HMDA_FIG_OVERVIEW),
            "data-points": _mock_response(HMDA_FIG_DATA_POINTS),
            "filing-instructions": _mock_response(HMDA_FIG_DATA_POINTS),
            "edit-checks": _mock_response(HMDA_FIG_EDIT_CHECKS),
        }
        session = _mock_session_with_url_map(url_map)

        docs = scrape_hmda_fig_pages(
            session,
            overview_url="https://ffiec.cfpb.gov/documentation/fig/2026/overview",
        )

        # External link should not be followed
        urls_scraped = [str(c) for c in session.get.call_args_list]
        assert not any("external.example.com" in u for u in urls_scraped)

    def test_fig_crawl_handles_overview_failure(self):
        from src.connectors.regulatory.usa._scraper_utils import scrape_hmda_fig_pages

        session = MagicMock(spec=requests.Session)
        session.get = MagicMock(side_effect=requests.ConnectionError("timeout"))

        docs = scrape_hmda_fig_pages(
            session,
            overview_url="https://ffiec.cfpb.gov/documentation/fig/2026/overview",
        )
        assert docs == []

    def test_cfpb_connector_tries_html_before_pdf(self):
        """Verify CfpbConnector._scrape_live tries HTML FIG before PDF."""
        from src.connectors.regulatory.usa.cfpb import CfpbConnector

        c = CfpbConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_hmda_fig_pages") as mock_fig,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url") as mock_pdf,
                patch("src.connectors.regulatory.usa._scraper_utils.check_rss_feed", return_value=[]),
            ):
                mock_fig.return_value = [{"title": "FIG page", "content": "data"}]
                result = c._scrape_live()

                # HTML FIG should be called, PDF should NOT be called
                mock_fig.assert_called()
                mock_pdf.assert_not_called()

    def test_cfpb_connector_falls_back_to_pdf(self):
        """If HTML FIG crawl finds nothing, should fall back to PDF."""
        from src.connectors.regulatory.usa.cfpb import CfpbConnector

        c = CfpbConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_hmda_fig_pages") as mock_fig,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url") as mock_pdf,
                patch("src.connectors.regulatory.usa._scraper_utils.check_rss_feed", return_value=[]),
            ):
                mock_fig.return_value = []  # HTML FIG empty
                mock_pdf.return_value = [{"title": "PDF chunk"}]
                result = c._scrape_live()

                mock_pdf.assert_called()


# ---------------------------------------------------------------------------
# FinCEN SAR Year-Walking PDF Resolver
# ---------------------------------------------------------------------------

class TestFincenSarYearWalking:
    """Test FinCEN SAR PDF URL year-walking resolver."""

    def test_resolves_current_year(self):
        from src.connectors.regulatory.usa._scraper_utils import resolve_fincen_sar_pdf

        current_yy = str(datetime.utcnow().year)[-2:]
        expected_url = f"https://www.fincen.gov/sites/default/files/shared/sar_tti_{current_yy}.pdf"

        session = MagicMock(spec=requests.Session)
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"Content-Type": "application/pdf"}
        session.head = MagicMock(return_value=head_resp)

        result = resolve_fincen_sar_pdf(session)
        assert result == expected_url

    def test_falls_back_to_previous_year(self):
        from src.connectors.regulatory.usa._scraper_utils import resolve_fincen_sar_pdf

        current_yy = str(datetime.utcnow().year)[-2:]
        prev_yy = str(datetime.utcnow().year - 1)[-2:]

        def head_side_effect(url, **kwargs):
            resp = MagicMock()
            resp.headers = {"Content-Type": "application/pdf"}
            if f"sar_tti_{current_yy}" in url:
                resp.status_code = 404
            elif f"sar_tti_{prev_yy}" in url:
                resp.status_code = 200
            else:
                resp.status_code = 404
            return resp

        session = MagicMock(spec=requests.Session)
        session.head = MagicMock(side_effect=head_side_effect)

        result = resolve_fincen_sar_pdf(session)
        assert result is not None
        assert f"sar_tti_{prev_yy}" in result

    def test_tries_fallback_patterns(self):
        from src.connectors.regulatory.usa._scraper_utils import resolve_fincen_sar_pdf

        calls = []

        def head_side_effect(url, **kwargs):
            calls.append(url)
            resp = MagicMock()
            resp.headers = {"Content-Type": "application/pdf"}
            if "/system/files/" in url and "sar_tti_23" in url:
                resp.status_code = 200
            else:
                resp.status_code = 404
            return resp

        session = MagicMock(spec=requests.Session)
        session.head = MagicMock(side_effect=head_side_effect)

        result = resolve_fincen_sar_pdf(
            session,
            base_pattern="https://www.fincen.gov/sites/default/files/shared/sar_tti_{yy}.pdf",
            fallback_patterns=["https://www.fincen.gov/system/files/shared/sar_tti_{yy}.pdf"],
        )
        assert result is not None
        assert "/system/files/" in result

    def test_returns_none_when_all_fail(self):
        from src.connectors.regulatory.usa._scraper_utils import resolve_fincen_sar_pdf

        session = MagicMock(spec=requests.Session)
        head_resp = MagicMock()
        head_resp.status_code = 404
        head_resp.headers = {"Content-Type": "text/html"}
        session.head = MagicMock(return_value=head_resp)

        result = resolve_fincen_sar_pdf(session, max_years_back=2)
        assert result is None

    def test_handles_connection_errors(self):
        from src.connectors.regulatory.usa._scraper_utils import resolve_fincen_sar_pdf

        session = MagicMock(spec=requests.Session)
        session.head = MagicMock(side_effect=requests.ConnectionError("timeout"))

        result = resolve_fincen_sar_pdf(session, max_years_back=2)
        assert result is None

    def test_fincen_connector_uses_resolver(self):
        """Verify FincenSarConnector._scrape_live uses the year-walking resolver."""
        from src.connectors.regulatory.usa.fincen_sar import FincenSarConnector

        c = FincenSarConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.resolve_fincen_sar_pdf") as mock_resolve,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url") as mock_pdf,
            ):
                mock_resolve.return_value = "https://www.fincen.gov/sites/default/files/shared/sar_tti_25.pdf"
                mock_pdf.return_value = [{"title": "SAR Review"}]

                docs = c._scrape_live()

                mock_resolve.assert_called_once()
                # PDF scrape should use the resolved URL
                mock_pdf.assert_called_once()
                pdf_url_arg = mock_pdf.call_args[0][1]
                assert "sar_tti_25" in pdf_url_arg

    def test_fincen_connector_uses_static_url_when_resolver_fails(self):
        """If resolver returns None, should fall back to static configured URL."""
        from src.connectors.regulatory.usa.fincen_sar import FincenSarConnector

        c = FincenSarConnector()

        with patch("src.connectors.regulatory.usa._scraper_utils.make_session") as mock_make:
            mock_session = MagicMock()
            mock_make.return_value = mock_session

            with (
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_html_page", return_value=[]),
                patch("src.connectors.regulatory.usa._scraper_utils.resolve_fincen_sar_pdf") as mock_resolve,
                patch("src.connectors.regulatory.usa._scraper_utils.scrape_pdf_url") as mock_pdf,
            ):
                mock_resolve.return_value = None  # Resolver failed
                mock_pdf.return_value = []

                docs = c._scrape_live()

                # Should fall back to configured URL
                pdf_url_arg = mock_pdf.call_args[0][1]
                assert "sar_tti_23" in pdf_url_arg


# ---------------------------------------------------------------------------
# URL Health Check
# ---------------------------------------------------------------------------

class TestUrlHealthCheck:
    """Test URL health check utility."""

    def test_healthy_urls(self):
        from src.connectors.regulatory.usa._scraper_utils import url_health_check

        session = MagicMock(spec=requests.Session)
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"Content-Type": "text/html"}
        session.head = MagicMock(return_value=head_resp)

        urls = {
            "page1": "https://example.gov/page1",
            "page2": "https://example.gov/page2",
        }
        results = url_health_check(session, urls, "TEST")

        assert len(results) == 2
        assert results["page1"]["ok"] is True
        assert results["page2"]["ok"] is True
        assert results["page1"]["status"] == 200

    def test_mixed_healthy_and_broken(self):
        from src.connectors.regulatory.usa._scraper_utils import url_health_check

        def head_side_effect(url, **kwargs):
            resp = MagicMock()
            resp.headers = {"Content-Type": "text/html"}
            if "broken" in url:
                resp.status_code = 404
            else:
                resp.status_code = 200
            return resp

        session = MagicMock(spec=requests.Session)
        session.head = MagicMock(side_effect=head_side_effect)

        urls = {
            "good": "https://example.gov/good",
            "broken": "https://example.gov/broken",
        }
        results = url_health_check(session, urls, "TEST")

        assert results["good"]["ok"] is True
        assert results["broken"]["ok"] is False
        assert results["broken"]["status"] == 404

    def test_connection_error_handling(self):
        from src.connectors.regulatory.usa._scraper_utils import url_health_check

        session = MagicMock(spec=requests.Session)
        session.head = MagicMock(side_effect=requests.ConnectionError("DNS failed"))

        urls = {"bad": "https://nonexistent.example.gov/"}
        results = url_health_check(session, urls, "TEST")

        assert results["bad"]["ok"] is False
        assert results["bad"]["status"] == 0
        assert "error" in results["bad"]


# ---------------------------------------------------------------------------
# FDIC Connector Default URL Config
# ---------------------------------------------------------------------------

class TestFdicConnectorConfig:
    """Verify FDIC connector has new TOC URL configured."""

    @patch("src.connectors.regulatory.usa.fdic.FdicConnector._scrape_live", return_value=[])
    def test_default_urls_include_toc(self, _):
        from src.connectors.regulatory.usa.fdic import FdicConnector

        c = FdicConnector()
        assert "risk_management_manual_toc" in c.urls
        assert "risk-management-manual-examination-policies" in c.urls["risk_management_manual_toc"]

    @patch("src.connectors.regulatory.usa.fdic.FdicConnector._scrape_live", return_value=[])
    def test_legacy_pdf_url_preserved(self, _):
        from src.connectors.regulatory.usa.fdic import FdicConnector

        c = FdicConnector()
        assert "risk_management_manual_pdf" in c.urls


# ---------------------------------------------------------------------------
# CFPB Connector Default URL Config
# ---------------------------------------------------------------------------

class TestCfpbConnectorConfig:
    """Verify CFPB connector has new HMDA FIG HTML URLs."""

    @patch("src.connectors.regulatory.usa.cfpb.CfpbConnector._scrape_live", return_value=[])
    def test_default_urls_include_fig_html(self, _):
        from src.connectors.regulatory.usa.cfpb import CfpbConnector

        c = CfpbConnector()
        assert "hmda_fig_html_2026" in c.urls
        assert "ffiec.cfpb.gov/documentation/fig/2026" in c.urls["hmda_fig_html_2026"]

    @patch("src.connectors.regulatory.usa.cfpb.CfpbConnector._scrape_live", return_value=[])
    def test_legacy_pdf_url_preserved(self, _):
        from src.connectors.regulatory.usa.cfpb import CfpbConnector

        c = CfpbConnector()
        assert "hmda_filing_guide_pdf" in c.urls


# ---------------------------------------------------------------------------
# FinCEN Connector Default URL Config
# ---------------------------------------------------------------------------

class TestFincenConnectorConfig:
    """Verify FinCEN connector has updated default URL pattern."""

    @patch("src.connectors.regulatory.usa.fincen_sar.FincenSarConnector._scrape_live", return_value=[])
    def test_default_url_uses_sites_default_files(self, _):
        from src.connectors.regulatory.usa.fincen_sar import FincenSarConnector

        c = FincenSarConnector()
        assert "sites/default/files" in c.urls["sar_activity_review_pdf"]
