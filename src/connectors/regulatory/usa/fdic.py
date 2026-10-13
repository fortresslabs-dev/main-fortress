"""
FDIC Consumer Compliance Connector — Federal Deposit Insurance Corporation
Extracts consumer compliance rules, deposit insurance requirements, and exam guidance.

Sources:
  - FDIC Consumer Compliance Examination Manual (HTML)
  - FDIC Laws & Regulations page
  - https://www.fdic.gov/resources/supervision-and-examinations/consumer-compliance-examination-manual/

Priority: P2
Status: ✅ COMPLETE (structured rules)
"""
from datetime import datetime
from typing import Dict, List, Any, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

# Default URLs — overridable via config/connectors/usa.yaml → source_urls.fdic
_DEFAULT_URLS = {
    "base_url": "https://www.fdic.gov",
    "compliance_manual": "https://www.fdic.gov/consumer-compliance-examination-manual",
    "capital_markets": "https://www.fdic.gov/capital-markets",
    "financial_institution_letters": "https://www.fdic.gov/news/financial-institution-letters",
    "laws_and_regulations": "https://www.fdic.gov/laws-and-regulations",
    "deposit_insurance": "https://www.fdic.gov/resources/deposit-insurance/",
    # Legacy combined PDF — kept as fallback but may 404
    "risk_management_manual_pdf": "https://www.fdic.gov/resources/supervision-and-examinations/examination-policies-manual/risk-management-manual-complete.pdf",
    # New TOC page for section-by-section crawl (preferred)
    "risk_management_manual_toc": "https://www.fdic.gov/risk-management-manual-examination-policies",
}


class FdicConnector(RegulatoryConnector):
    """FDIC consumer compliance and deposit insurance connector.

    Covers:
      - 3-tier supervisory exam cycle
      - Capital adequacy requirements
      - Deposit insurance rules ($250K per depositor)
      - Digital signage compliance
      - Truth in Savings (Reg DD)
    """

    connector_name = "fdic"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("fdic_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("fdic_extraction_started")

        regulations = self._build_regulations()
        requirements = self._build_requirements()
        kpis = self._build_kpis()
        test_scenarios = self._build_test_scenarios()

        result: Dict[str, List[Dict]] = {
            "regulations": regulations,
            "requirements": requirements,
            "kpis": kpis,
            "test_scenarios": test_scenarios,
            "scraped_documents": [],
        }

        try:
            result["scraped_documents"] = self._scrape_live()
        except Exception as exc:
            logger.warning("fdic_live_scrape_failed_using_static", error=str(exc))

        logger.info(
            "fdic_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            kpis=len(kpis),
            test_scenarios=len(test_scenarios),
            scraped_documents=len(result["scraped_documents"]),
        )
        return result

    def _infer_regulation_id(self, doc: dict) -> str:
        """Infer a regulation_id from document content for enrich_document."""
        title = (doc.get("section_title", "") + " " + doc.get("document_title", "") +
                 " " + doc.get("title", "") + " " + doc.get("category", "")).lower()
        if "capital" in title or "cet1" in title or "tier 1" in title:
            return "FDIC-CAPITAL-001"
        if "deposit insurance" in title or "coverage" in title or "smdia" in title:
            return "FDIC-DEPOSIT-001"
        return "FDIC-EXAM-001"

    def _scrape_live(self) -> List[Dict]:
        """Scrape FDIC compliance manual, FILs, capital pages, Risk Management Manual, and sitemap-discovered content."""
        from src.connectors.regulatory.usa._scraper_utils import (
            discover_compliance_urls,
            enrich_document,
            make_session,
            scrape_discovered_urls,
            scrape_html_page,
            scrape_linked_pages,
            scrape_pdf_url,
            scrape_fdic_risk_manual_sections,
            url_health_check,
        )
        import hashlib

        session = make_session()
        docs: List[Dict] = []

        # 1. Consumer Compliance Examination Manual — crawl linked chapter pages
        docs.extend(
            scrape_linked_pages(
                session,
                index_url=self.urls["compliance_manual"],
                source="FDIC",
                category="Compliance Manual",
                link_pattern="/consumer-compliance-examination-manual/",
                base_url="https://www.fdic.gov",
                max_pages=50,
            )
        )

        # 2. Financial Institution Letters — custom scrape for new FDIC layout
        docs.extend(self._scrape_fils(session))

        # 3. Capital / laws pages
        docs.extend(
            scrape_html_page(
                session,
                self.urls["capital_markets"],
                source="FDIC",
                category="Capital Adequacy",
            )
        )
        docs.extend(
            scrape_html_page(
                session,
                self.urls["laws_and_regulations"],
                source="FDIC",
                category="Laws and Regulations",
            )
        )

        # 4. Risk Management Manual — TOC crawl (preferred, section-by-section)
        #    Falls back to legacy combined PDF if TOC crawl finds nothing.
        toc_url = self.urls.get("risk_management_manual_toc")
        if toc_url:
            risk_docs = scrape_fdic_risk_manual_sections(session, toc_url=toc_url)
            if risk_docs:
                docs.extend(risk_docs)
                logger.info("fdic_risk_manual_toc_crawl_success", sections=len(risk_docs))
            else:
                # Fallback to legacy combined PDF
                logger.info("fdic_risk_manual_toc_empty_trying_pdf_fallback")
                docs.extend(
                    scrape_pdf_url(
                        session,
                        self.urls["risk_management_manual_pdf"],
                        source="FDIC",
                        category="Risk Management Manual",
                    )
                )
        else:
            # No TOC configured, use legacy PDF
            docs.extend(
                scrape_pdf_url(
                    session,
                    self.urls["risk_management_manual_pdf"],
                    source="FDIC",
                    category="Risk Management Manual",
                )
            )

        # 5. Sitemap-driven discovery — find compliance pages not in hardcoded URLs
        try:
            existing = set(self.urls.values())
            discovered = discover_compliance_urls(
                session, agency="fdic",
                sitemap_url=self.urls.get("sitemap_url"),
                existing_urls=existing,
            )
            if discovered.get("new_urls"):
                logger.info("fdic_sitemap_discovery", new_urls=len(discovered["new_urls"]))
                docs.extend(
                    scrape_discovered_urls(
                        session, discovered, source="FDIC",
                        category="Discovered Compliance Content",
                        max_html_pages=50, max_pdfs=10,
                    )
                )
        except Exception as exc:
            logger.warning("fdic_sitemap_discovery_failed", error=str(exc))

        # Enrich all docs with obligation metadata, CFR citations, and regulation IDs
        enriched = []
        for doc in docs:
            doc_type = "reg_text"
            cat = doc.get("category", "").lower()
            if "financial institution letter" in cat or "fil" in cat:
                doc_type = "guidance"
            elif "risk management" in cat:
                doc_type = "guidance"
            enriched.append(enrich_document(
                doc,
                regulation_id=self._infer_regulation_id(doc),
                state_code=None,
                doc_type=doc_type,
            ))
        return enriched

    def _scrape_fils(self, session) -> List[Dict]:
        """Scrape FDIC Financial Institution Letters from the listing page."""
        import hashlib
        from bs4 import BeautifulSoup

        docs: List[Dict] = []
        now = datetime.utcnow().isoformat()

        try:
            resp = session.get(self.urls["financial_institution_letters"], timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("fdic_fil_listing_failed", error=str(exc))
            return docs

        soup = BeautifulSoup(resp.content, "lxml")
        news_items = soup.find_all("div", class_="news-content")

        for item in news_items[:20]:
            link = item.find("a")
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if not href.startswith("http"):
                href = self.urls["base_url"] + href

            # Fetch individual FIL page
            try:
                fil_resp = session.get(href, timeout=30)
                fil_resp.raise_for_status()
                fil_soup = BeautifulSoup(fil_resp.content, "lxml")
                article = fil_soup.find("article")
                content = article.get_text(strip=True) if article else ""
                if content:
                    docs.append({
                        "source": "FDIC",
                        "category": "Financial Institution Letter",
                        "title": title,
                        "content": content[:10000],
                        "url": href,
                        "scraped_at": now,
                        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    })
            except Exception as exc:
                logger.warning("fdic_fil_page_failed", url=href, error=str(exc))

        logger.info("fdic_fils_scraped", count=len(docs))
        return docs

    def check_updates(self) -> List[Dict]:
        """Check FDIC FIL listing for recent updates (RSS feed deprecated)."""
        from src.connectors.regulatory.usa._scraper_utils import make_session
        return self._scrape_fils(make_session())[:5]

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        return [
            # C-4 — cite corrected from 12 CFR 337.6 (which is Brokered
            # Deposits) to 12 CFR 337.12 (Frequency of Examination). Also
            # adds the two conditions the prior text omitted: management
            # rating 1-2 and no formal enforcement action.
            {
                "regulation_id": "FDIC-EXAM-001",
                "regulation_name": "Frequency of Safety-and-Soundness Examinations",
                "cfr_citation": "12 C.F.R. § 337.12",
                "authority_type": "LAW",
                "rule_text": (
                    "FDIC-supervised institutions are examined on a risk-based cycle of 12 to 18 "
                    "months. To qualify for the 18-month cycle an institution must have (i) total "
                    "assets under $3 billion, (ii) a composite CAMELS rating of 1 or 2, (iii) a "
                    "management rating of 1 or 2, and (iv) no formal enforcement action pending. "
                    "All others are examined at least every 12 months. 12 C.F.R. § 337.12."
                ),
                "category": "Examination",
                "subcategory": "Consumer Compliance",
                "regulator": "FDIC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "12-18 months (risk-based)",
                "source_url": "https://www.ecfr.gov/current/title-12/section-337.12",
            },
            {
                "regulation_id": "FDIC-DEPOSIT-001",
                "regulation_name": "Standard Maximum Deposit Insurance Amount",
                "cfr_citation": "12 C.F.R. § 330.1",
                "rule_text": (
                    "The standard maximum deposit insurance amount (SMDIA) is $250,000 per "
                    "depositor, per FDIC-insured bank, per ownership category. Ownership "
                    "categories include single accounts, joint accounts, revocable trust "
                    "accounts, irrevocable trust accounts, and certain retirement accounts. "
                    "Institutions must display the official FDIC sign at each teller station "
                    "and on digital banking platforms. 12 C.F.R. § 330.1; FDIA § 11(a)(1)."
                ),
                "category": "Deposit Insurance",
                "subcategory": "Coverage",
                "regulator": "FDIC",
                "geography": "usa",
                "effective_date": "2008-10-03",
                "exam_frequency": "ongoing",
                "source_url": self.urls["deposit_insurance"],
            },
            {
                "regulation_id": "FDIC-CAPITAL-001",
                "regulation_name": "Risk-Based Capital Requirements",
                "cfr_citation": "12 C.F.R. § 324.10",
                "rule_text": (
                    "FDIC-supervised institutions must maintain minimum capital ratios: "
                    "Common Equity Tier 1 (CET1) of 4.5% of risk-weighted assets; "
                    "Tier 1 capital ratio of 6% of risk-weighted assets; "
                    "Total capital ratio of 8% of risk-weighted assets; "
                    "Leverage ratio of 4% of average total assets. "
                    "Well-capitalized thresholds are CET1 ≥ 6.5%, Tier 1 ≥ 8%, Total ≥ 10%. "
                    "12 C.F.R. §§ 324.10–324.12; Basel III capital framework."
                ),
                "category": "Capital Adequacy",
                "subcategory": "Basel III",
                "regulator": "FDIC",
                "geography": "usa",
                "effective_date": "2015-01-01",
                "exam_frequency": "quarterly",
                "source_url": self.urls["capital_markets"],
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "FDIC-DEPOSIT-001-R1",
                "regulation_id": "FDIC-DEPOSIT-001",
                "cfr_citation": "12 C.F.R. § 330.1",
                "requirement_text": "Standard maximum deposit insurance is $250,000 per depositor, per insured bank, per ownership category",
                "rule_type": "deposit_insurance",
                "threshold_amount": 250000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify system displays correct FDIC insurance coverage and alerts when deposits exceed $250K per ownership category",
                "parameters": {
                    "coverage_per_depositor": 250000,
                    "ownership_categories": ["single", "joint", "revocable_trust", "irrevocable_trust", "retirement"],
                },
            },
            {
                "requirement_id": "FDIC-CAPITAL-001-R1",
                "regulation_id": "FDIC-CAPITAL-001",
                "cfr_citation": "12 C.F.R. § 324.10",
                "requirement_text": "Minimum Common Equity Tier 1 (CET1) capital ratio of 4.5%",
                "rule_type": "capital_ratio",
                "threshold_amount": 4.5,
                "threshold_currency": None,
                "test_requirement": "Verify capital ratio calculation and alert when CET1 ratio falls below 4.5%",
                "parameters": {
                    "ratio_type": "CET1",
                    "minimum_percent": 4.5,
                    "well_capitalized_percent": 6.5,
                },
            },
            # C-5 — cite corrected from 12 CFR 324.12 (which is the CBLR
            # framework) to 12 CFR 324.10(a)(3) (Minimum Total Capital
            # Ratio). 324.12 is a separate exemption path, not the
            # total-capital minimum.
            {
                "requirement_id": "FDIC-CAPITAL-001-R2",
                "regulation_id": "FDIC-CAPITAL-001",
                "cfr_citation": "12 C.F.R. § 324.10(a)(3)",
                "authority_type": "LAW",
                "requirement_text": "Minimum Total Risk-Based Capital ratio of 8%",
                "rule_type": "capital_ratio",
                "threshold_amount": 8.0,
                "threshold_currency": None,
                "test_requirement": "Verify total risk-based capital ratio meets 8% minimum",
                "parameters": {
                    "ratio_type": "total_risk_based",
                    "minimum_percent": 8.0,
                    "well_capitalized_percent": 10.0,
                },
            },
            # C-orphan — parent rule FDIC-SIGN-001 does not exist. Re-parented
            # to FDIC-DEPOSIT-001 (SMDIA / deposit-insurance disclosure) per
            # docs/correction_smb.md §4. Signage is a deposit-insurance
            # disclosure obligation, not an exam-frequency one.
            {
                "requirement_id": "FDIC-SIGN-001-R1",
                "regulation_id": "FDIC-DEPOSIT-001",
                "cfr_citation": "12 C.F.R. § 328.3",
                "authority_type": "LAW",
                "requirement_text": "FDIC official sign must be displayed at each teller window and on digital banking platforms",
                "rule_type": "digital_signage",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify FDIC digital sign appears on online/mobile banking login and deposit pages",
                "parameters": {
                    "display_locations": ["teller_window", "atm_screen", "online_banking", "mobile_app"],
                    "sign_specification": "FDIC official digital sign",
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — KPIs carry parent_requirement_id + inherited citation +
        # authority_type. Test Factory / examiner-package readers no longer
        # have to re-derive the chain back to the parent rule.
        return [
            {
                "kpi_id": "FDIC-KPI-001",
                "regulation_id": "FDIC-CAPITAL-001",
                "parent_requirement_id": "FDIC-CAPITAL-001-R1",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 324.10",
                "kpi_name": "CET1 Capital Ratio",
                "kpi_formula": "Common Equity Tier 1 Capital / Risk-Weighted Assets * 100",
                "target_value": 6.5,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 5.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "FDIC-KPI-002",
                "regulation_id": "FDIC-DEPOSIT-001",
                "parent_requirement_id": "FDIC-SIGN-001-R1",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 328.3",
                "kpi_name": "Deposit Insurance Disclosure Compliance",
                "kpi_formula": "(Pages with FDIC sign / Total required pages) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 99.0,
                "criticality": "medium",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # C-11/C-12 — TCs carry parent_requirement_id + inherited citation +
        # authority_type + regulator=FDIC (was implicitly OCC on prior rows).
        return [
            {
                "scenario_id": "TC-FDIC-001",
                "regulation_id": "FDIC-DEPOSIT-001",
                "parent_requirement_id": "FDIC-DEPOSIT-001-R1",
                "regulator": "FDIC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 330.1",
                "scenario_name": "Deposit exceeds $250K FDIC coverage - alert displayed",
                "priority": "high",
                "given": "Customer has single-ownership deposit account with $240,000 balance",
                "when_step": "Customer deposits $20,000 (new balance $260,000)",
                "then_step": "System alerts: 'Deposit exceeds FDIC coverage by $10,000'; suggests options",
            },
            {
                "scenario_id": "TC-FDIC-002",
                "regulation_id": "FDIC-CAPITAL-001",
                "parent_requirement_id": "FDIC-CAPITAL-001-R1",
                "regulator": "FDIC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 324.10",
                "scenario_name": "CET1 ratio drops below well-capitalized threshold",
                "priority": "critical",
                "given": "Bank CET1 ratio is 6.8% (well-capitalized)",
                "when_step": "Quarter-end calculation shows CET1 at 6.3% (adequately capitalized)",
                "then_step": "System generates capital adequacy alert to risk committee",
            },
            {
                "scenario_id": "TC-FDIC-003",
                "regulation_id": "FDIC-DEPOSIT-001",
                "parent_requirement_id": "FDIC-SIGN-001-R1",
                "regulator": "FDIC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 328.3",
                "scenario_name": "FDIC digital sign missing from mobile app deposit page",
                "priority": "medium",
                "given": "Mobile app deposit page deployed without FDIC sign",
                "when_step": "Compliance scanner checks mobile app pages",
                "then_step": "Violation flagged: 'Missing FDIC sign on deposit page'; remediation ticket created",
            },
        ]
