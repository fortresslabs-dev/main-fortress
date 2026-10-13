"""
CFPB Section 1071 Connector — Consumer Financial Protection Bureau
Extracts small business lending data collection requirements.

Sources:
  - CFPB Section 1071 Final Rule (HTML/PDF)
  - https://www.consumerfinance.gov/rules-policy/final-rules/small-business-lending-under-the-equal-credit-opportunity-act-regulation-b/

CRITICAL: July 2026 compliance deadline.
Priority: P2
Status: ✅ COMPLETE (structured rules)
"""
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

# Default URLs — overridable via config/connectors/usa.yaml → source_urls.cfpb
_DEFAULT_URLS = {
    "section_1071": "https://www.consumerfinance.gov/rules-policy/final-rules/small-business-lending-under-the-equal-credit-opportunity-act-regulation-b/",
    "hmda": "https://www.consumerfinance.gov/data-research/hmda/",
    "regulation_e": "https://www.consumerfinance.gov/rules-policy/regulations/1005/",
    "regulation_b": "https://www.consumerfinance.gov/rules-policy/regulations/1002/",
    "regulation_e_commentary": "https://www.consumerfinance.gov/rules-policy/regulations/1005/interp/",
    "final_rules": "https://www.consumerfinance.gov/rules-policy/final-rules/",
    "supervisory_highlights": "https://www.consumerfinance.gov/compliance/supervisory-highlights/",
    "compliance_resources": "https://www.consumerfinance.gov/compliance/compliance-resources/",
    # Legacy PDF URL — kept as fallback but may 404
    "hmda_filing_guide_pdf": "https://ffiec.cfpb.gov/resources/HMDA_Filing_Instructions_Guide_2024_v1.pdf",
    # New: HMDA FIG as static HTML pages (preferred — works without JS)
    "hmda_fig_html_2026": "https://ffiec.cfpb.gov/documentation/fig/2026/overview",
    "hmda_fig_html_2025": "https://ffiec.cfpb.gov/documentation/fig/2025/overview",
    "rss_feed": "https://www.consumerfinance.gov/feed/",
    # REQ-015: Interpretive guidance
    "hmda_faq": "https://www.consumerfinance.gov/data-research/hmda/for-filers",
    "exam_procedures": "https://www.consumerfinance.gov/policy-compliance/guidance/supervision-examinations/",
    # REQ-009: Fair lending peer benchmark data sources
    "hmda_api": "https://ffiec.cfpb.gov/api/hmda/",
    "ffiec_census": "https://www.ffiec.gov/censusapp.htm",
    "fdic_bankfind": "https://banks.data.fdic.gov/api/",
}


class CfpbConnector(RegulatoryConnector):
    """CFPB regulatory connector.

    Covers:
      - Section 1071: Small business lending data collection (Jul 2026 deadline)
      - HMDA: Home Mortgage Disclosure Act reporting
      - Regulation B: Equal Credit Opportunity Act
      - Regulation E: Electronic Fund Transfer Act (12 CFR Part 1005)
    """

    connector_name = "cfpb"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("cfpb_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("cfpb_extraction_started")

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
            logger.warning("cfpb_live_scrape_failed_using_static", error=str(exc))

        logger.info(
            "cfpb_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            kpis=len(kpis),
            test_scenarios=len(test_scenarios),
            scraped_documents=len(result["scraped_documents"]),
        )
        return result

    def _scrape_live(self) -> List[Dict]:
        """Scrape CFPB Section 1071, HMDA, Reg E pages, HMDA FIG, and sitemap-discovered content."""
        from src.connectors.regulatory.usa._scraper_utils import (
            check_rss_feed,
            discover_compliance_urls,
            make_session,
            scrape_discovered_urls,
            scrape_html_page,
            scrape_hmda_fig_pages,
            scrape_pdf_url,
        )

        session = make_session()
        docs: List[Dict] = []

        # 1. Section 1071 final rule page
        docs.extend(
            scrape_html_page(
                session,
                self.urls["section_1071"],
                source="CFPB",
                category="Section 1071",
            )
        )

        # 2. HMDA page
        docs.extend(
            scrape_html_page(
                session,
                self.urls["hmda"],
                source="CFPB",
                category="HMDA",
            )
        )

        # 3. Regulation E (12 CFR Part 1005)
        docs.extend(
            scrape_html_page(
                session,
                self.urls["regulation_e"],
                source="CFPB",
                category="Regulation E",
                content_selectors=["div.regulation-content", "div.reg-section"],
            )
        )

        # 4. Regulation B (ECOA)
        docs.extend(
            scrape_html_page(
                session,
                self.urls["regulation_b"],
                source="CFPB",
                category="Regulation B",
            )
        )

        # 5. Regulation E Official Commentary / Interpretations
        docs.extend(
            scrape_html_page(
                session,
                self.urls["regulation_e_commentary"],
                source="CFPB",
                category="Regulation E Commentary",
                content_selectors=["div.regulation-content", "div.reg-section"],
            )
        )

        # 6. CFPB Final Rules Index
        docs.extend(
            scrape_html_page(
                session,
                self.urls["final_rules"],
                source="CFPB",
                category="Final Rules",
            )
        )

        # 7. Supervisory Highlights
        docs.extend(
            scrape_html_page(
                session,
                self.urls["supervisory_highlights"],
                source="CFPB",
                category="Supervisory Highlights",
            )
        )

        # 8. Compliance Resources / Bulletins
        docs.extend(
            scrape_html_page(
                session,
                self.urls["compliance_resources"],
                source="CFPB",
                category="Compliance Resources",
            )
        )

        # 9. HMDA Filing Instructions Guide — try HTML pages first (2026, then 2025),
        #    fall back to legacy PDF if HTML crawl finds nothing.
        fig_docs: List[Dict] = []
        for fig_key in ("hmda_fig_html_2026", "hmda_fig_html_2025"):
            fig_url = self.urls.get(fig_key)
            if fig_url:
                fig_docs = scrape_hmda_fig_pages(session, overview_url=fig_url)
                if fig_docs:
                    logger.info("hmda_fig_html_crawl_success", url=fig_url, docs=len(fig_docs))
                    break

        if fig_docs:
            docs.extend(fig_docs)
        else:
            # Fall back to legacy PDF
            logger.info("hmda_fig_html_empty_trying_pdf_fallback")
            docs.extend(
                scrape_pdf_url(
                    session,
                    self.urls["hmda_filing_guide_pdf"],
                    source="CFPB",
                    category="HMDA Filing Guide",
                )
            )

        # 10. HMDA FAQ and exam procedures (doc_type=faq/examination) — REQ-015
        docs.extend(
            scrape_html_page(
                session,
                self.urls["hmda_faq"],
                source="CFPB",
                category="HMDA FAQ",
            )
        )
        docs.extend(
            scrape_html_page(
                session,
                self.urls["exam_procedures"],
                source="CFPB",
                category="Examination Procedures",
            )
        )

        # 11. Sitemap-driven discovery — find compliance pages not in hardcoded URLs
        try:
            existing = set(self.urls.values())
            discovered = discover_compliance_urls(
                session, agency="cfpb",
                sitemap_url=self.urls.get("sitemap_url"),
                existing_urls=existing,
            )
            if discovered.get("new_urls"):
                logger.info("cfpb_sitemap_discovery", new_urls=len(discovered["new_urls"]))
                docs.extend(
                    scrape_discovered_urls(
                        session, discovered, source="CFPB",
                        category="Discovered Compliance Content",
                        max_html_pages=50, max_pdfs=10,
                    )
                )
        except Exception as exc:
            logger.warning("cfpb_sitemap_discovery_failed", error=str(exc))

        return docs

    def check_updates(self) -> List[Dict]:
        """Check CFPB RSS feed for rule and guidance updates."""
        from src.connectors.regulatory.usa._scraper_utils import check_rss_feed

        return check_rss_feed(
            self.urls["rss_feed"],
            keywords=["1071", "hmda", "reg e", "lending", "compliance"],
            max_items=20,
        )

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        # C-cite + C-13 — cfr_citations backfilled per docs/correction_smb.md
        # §5. effective_date nulled where the prior value was a page-publish
        # date rather than an FR `effective_on` (repopulate from FR connector).
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "CFPB-1071-001",
                "regulation_name": "Section 1071 Small Business Lending Data Collection",
                "cfr_citation": "12 C.F.R. Part 1002, Subpart B",
                "authority_type": "LAW",
                # 1071 compliance-date tiering shifted repeatedly under
                # litigation; the prior 2026-07-01 value is not a stable FR
                # effective date. Surface WATCH warning in UI.
                "verify_before_display": True,
                "category": "Fair Lending",
                "subcategory": "Section 1071",
                "regulator": "CFPB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["section_1071"],
                "last_updated": now,
            },
            {
                "regulation_id": "CFPB-HMDA-001",
                "regulation_name": "Home Mortgage Disclosure Act (HMDA) Reporting",
                "cfr_citation": "12 C.F.R. Part 1003",
                "authority_type": "LAW",
                "category": "Fair Lending",
                "subcategory": "HMDA",
                "regulator": "CFPB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["hmda"],
                "last_updated": now,
                "parameters": {
                    # C-cite — reporting-threshold tiering. 100-loan closed-end
                    # threshold was vacated by NCRC v. CFPB (2022); current
                    # threshold is 25 closed-end + 200 open-end. EGRRCPA
                    # partial exemption for institutions below 500 in each of
                    # the prior 2 calendar years.
                    "reporting_threshold_closed_end": 25,
                    "reporting_threshold_open_end":  200,
                    "partial_exemption": {
                        "closed_end_lt": 500,
                        "open_end_lt":   500,
                        "in_each_of_prior_years": 2,
                    },
                },
            },
            {
                "regulation_id": "CFPB-REGB-001",
                "regulation_name": "Regulation B — Equal Credit Opportunity Act",
                "cfr_citation": "12 C.F.R. Part 1002",
                "authority_type": "LAW",
                "category": "Fair Lending",
                "subcategory": "ECOA",
                "regulator": "CFPB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["regulation_b"],
                "last_updated": now,
            },
            {
                "regulation_id": "CFPB-REGE-001",
                "regulation_name": "Regulation E — Electronic Fund Transfer Act (12 CFR Part 1005)",
                "cfr_citation": "12 C.F.R. Part 1005 (incl. Subpart B Remittance Transfer Rule)",
                "authority_type": "LAW",
                "category": "Consumer Protection",
                "subcategory": "EFT",
                "regulator": "CFPB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["regulation_e"],
                "last_updated": now,
            },
            # C-class — the 2:1 peer-disparity trigger is supervisory
            # practice, not codified in a CFR section → GUIDANCE.
            {
                "regulation_id": "CFPB-FAIRLEND-001",
                "regulation_name": "Fair Lending — Comparative File Review and Statistical Analysis",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "CFPB Fair Lending Examination Procedures; ECOA/FHA-derived supervisory practice",
                "rule_text": "Banks are expected to conduct comparative file review and statistical "
                             "analysis of HMDA loan data to detect disparate treatment or disparate "
                             "impact across protected classes. Peer denial rates by MSA and applicant "
                             "demographics are compared against peer institutions. This is supervisory "
                             "practice under ECOA and the Fair Housing Act, not a codified CFR mandate.",
                "category": "Fair Lending",
                "subcategory": "Statistical Analysis",
                "regulator": "CFPB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["hmda"],
                "last_updated": now,
            },
            # C-class — peer-comparison methodology is exam practice, not a
            # rule → GUIDANCE.
            {
                "regulation_id": "CFPB-HMDA-PEER-001",
                "regulation_name": "HMDA Peer Denial Rate Benchmarking",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "CFPB HMDA Examination Procedures (peer-comparison methodology)",
                "rule_text": "Institutions are examined for mortgage loan denial rates by race, ethnicity, "
                             "gender, and income against peer institutions in the same MSA using public "
                             "HMDA data. Denial-rate disparities in the neighbourhood of 2:1 relative to "
                             "peer median trigger heightened fair-lending review. This is supervisory "
                             "practice, not a codified regulatory threshold.",
                "category": "Fair Lending",
                "subcategory": "HMDA Peer Analysis",
                "regulator": "CFPB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["hmda_api"],
                "last_updated": now,
                "parameters": {
                    "disparity_ratio_alert_threshold": 2.0,
                    "peer_group": "same_msa_asset_tier",
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            # --- Section 1071: Demographic data collection ---
            {
                "requirement_id": "CFPB-1071-DEMO-001",
                "regulation_id": "CFPB-1071-001",
                "cfr_citation": "12 C.F.R. § 1002.107(a)(18)-(19)",
                "authority_type": "LAW",
                "requirement_text": "Collect race, sex, and ethnicity of principal owners for small business loan applications",
                "rule_type": "required_field",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify loan application form collects demographic data with decline option",
                "parameters": {
                    "required_fields": ["race", "sex", "ethnicity"],
                    "applicant_may_decline": True,
                    "collection_method": "visual_observation_or_surname_if_declined",
                },
            },
            {
                "requirement_id": "CFPB-1071-BIZ-001",
                "regulation_id": "CFPB-1071-001",
                "cfr_citation": "12 C.F.R. § 1002.107(a)",
                "authority_type": "LAW",
                "requirement_text": "Collect NAICS code, gross annual revenue, time in business, and number of workers",
                "rule_type": "required_field",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify business data fields are collected and NAICS code is validated (2-6 digits)",
                "parameters": {
                    "required_fields": ["naics_code", "gross_annual_revenue", "time_in_business", "number_of_workers"],
                    "naics_format": r"^\d{2,6}$",
                },
            },
            {
                "requirement_id": "CFPB-1071-OWN-001",
                "regulation_id": "CFPB-1071-001",
                "cfr_citation": "12 C.F.R. § 1002.107(a)(18)",
                "authority_type": "LAW",
                "requirement_text": "Collect minority-owned and women-owned business status",
                "rule_type": "required_field",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify ownership status fields collected with decline option",
                "parameters": {
                    "required_fields": ["minority_owned", "women_owned"],
                    "applicant_may_decline": True,
                },
            },
            # --- HMDA: Loan Application Register ---
            {
                "requirement_id": "CFPB-HMDA-LAR-001",
                "regulation_id": "CFPB-HMDA-001",
                "cfr_citation": "12 C.F.R. § 1003.5(a)",
                "authority_type": "LAW",
                "requirement_text": "Submit annual Loan Application Register (LAR) with all required fields to CFPB",
                "rule_type": "annual_filing",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify LAR export contains all HMDA-required fields and passes CFPB edit checks",
                "parameters": {
                    "filing_deadline": "March 1 of following year",
                    "required_fields": [
                        "application_date", "loan_type", "loan_purpose", "preapproval",
                        "action_taken", "action_taken_date", "property_type", "loan_amount",
                        "rate_spread", "applicant_ethnicity", "applicant_race", "applicant_sex",
                        "applicant_income", "census_tract",
                    ],
                },
            },
            # --- Reg B: Adverse action notice ---
            {
                "requirement_id": "CFPB-REGB-ADV-001",
                "regulation_id": "CFPB-REGB-001",
                "cfr_citation": "12 C.F.R. § 1002.9(a)(1)",
                "authority_type": "LAW",
                "requirement_text": "Adverse action notice must be sent within 30 days of application with specific reasons for denial",
                "rule_type": "adverse_action",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify adverse action notice auto-generated within 30 days with principal denial reasons",
                "parameters": {
                    "deadline_days": 30,
                    "max_reasons": 4,
                    "required_content": [
                        "specific_reasons_for_denial",
                        "cfpb_contact_information",
                        "applicant_rights_statement",
                    ],
                },
            },
            # --- Reg E: Electronic Fund Transfer Act ---
            {
                "requirement_id": "CFPB-REGE-ERR-001",
                "regulation_id": "CFPB-REGE-001",
                "cfr_citation": "12 C.F.R. § 1005.11(c)",
                "authority_type": "LAW",
                "requirement_text": "Investigate consumer error claims within 10 business days (or 45 calendar days with provisional credit)",
                "rule_type": "error_resolution",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify error resolution process adheres to §1005.11 timelines",
                "parameters": {
                    "initial_investigation_days": 10,
                    "extended_investigation_days": 45,
                    "provisional_credit_days": 10,
                    "new_account_extended_days": 20,
                },
            },
            {
                "requirement_id": "CFPB-REGE-LIAB-001",
                "regulation_id": "CFPB-REGE-001",
                "cfr_citation": "12 C.F.R. § 1005.6(b)",
                "authority_type": "LAW",
                "requirement_text": "Consumer liability for unauthorized EFT limited based on notification timing",
                "rule_type": "liability_limit",
                "threshold_amount": 50.0,
                "threshold_currency": "USD",
                "test_requirement": "Verify consumer liability caps: $0 if reported before use, $50 within 2 days, $500 within 60 days",
                "parameters": {
                    "tiers": [
                        {"within_days": 0, "max_liability_usd": 0, "description": "Reported before unauthorized use"},
                        {"within_days": 2, "max_liability_usd": 50, "description": "Reported within 2 business days"},
                        {"within_days": 60, "max_liability_usd": 500, "description": "Reported within 60 days of statement"},
                        {"within_days": -1, "max_liability_usd": -1, "description": "After 60 days — unlimited liability"},
                    ],
                },
            },
            {
                "requirement_id": "CFPB-REGE-DISC-001",
                "regulation_id": "CFPB-REGE-001",
                "cfr_citation": "12 C.F.R. § 1005.7",
                "authority_type": "LAW",
                "requirement_text": "Provide initial disclosures for EFT services including fee schedule, error resolution rights, and liability limits",
                "rule_type": "disclosure",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify all §1005.7 initial disclosure fields are present at account opening",
                "parameters": {
                    "required_disclosures": [
                        "liability_for_unauthorized_transfers",
                        "types_of_eft_available",
                        "fees_for_eft_services",
                        "right_to_stop_preauthorized_transfers",
                        "error_resolution_procedures",
                        "bank_liability_for_failure",
                    ],
                },
            },
            {
                "requirement_id": "CFPB-REGE-PREAUTH-001",
                "regulation_id": "CFPB-REGE-001",
                "cfr_citation": "12 C.F.R. § 1005.10(c)",
                "authority_type": "LAW",
                "requirement_text": "Consumer has right to stop preauthorized EFT by notifying institution at least 3 business days before scheduled date",
                "rule_type": "stop_payment",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify stop-payment processed when received 3+ business days before transfer date",
                "parameters": {
                    "notice_days": 3,
                    "oral_notice_valid": True,
                    "written_confirmation_deadline_days": 14,
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        return [
            {
                "kpi_id": "CFPB-1071-KPI-001",
                "regulation_id": "CFPB-1071-001",
                "parent_requirement_id": "CFPB-1071-DEMO-001",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1002.107(a)(18)-(19)",
                "kpi_name": "Section 1071 Data Completeness",
                "kpi_formula": "(Loan apps with all 1071 fields / Total loan apps) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "weekly",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
            {
                "kpi_id": "CFPB-HMDA-KPI-001",
                "regulation_id": "CFPB-HMDA-001",
                "parent_requirement_id": "CFPB-HMDA-LAR-001",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1003.5(a)",
                "kpi_name": "HMDA LAR Edit Check Pass Rate",
                "kpi_formula": "(LAR records passing edit checks / Total LAR records) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 98.0,
                "criticality": "high",
            },
            {
                "kpi_id": "CFPB-REGB-KPI-001",
                "regulation_id": "CFPB-REGB-001",
                "parent_requirement_id": "CFPB-REGB-ADV-001",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1002.9(a)(1)",
                "kpi_name": "Adverse Action Notice Timeliness",
                "kpi_formula": "(Notices sent within 30 days / Total adverse actions) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
            {
                "kpi_id": "CFPB-REGE-KPI-001",
                "regulation_id": "CFPB-REGE-001",
                "parent_requirement_id": "CFPB-REGE-ERR-001",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1005.11(c)",
                "kpi_name": "Reg E Error Resolution Timeliness",
                "kpi_formula": "(Error claims resolved within 10 days or provisionally credited / Total error claims) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
            {
                "kpi_id": "CFPB-REGE-KPI-002",
                "regulation_id": "CFPB-REGE-001",
                "parent_requirement_id": "CFPB-REGE-DISC-001",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1005.7",
                "kpi_name": "Reg E Disclosure Completeness",
                "kpi_formula": "(Accounts with complete EFT disclosures / Total EFT-enabled accounts) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "weekly",
                "alert_threshold": 98.0,
                "criticality": "medium",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-CFPB-001",
                "regulation_id": "CFPB-1071-001",
                "parent_requirement_id": "CFPB-1071-DEMO-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1002.107(a)(18)-(19)",
                "scenario_name": "Loan application missing ethnicity field rejected",
                "priority": "high",
                "given": "Small business loan application submitted",
                "when_step": "Application missing ethnicity field and applicant did not explicitly decline",
                "then_step": "System flags: 'Missing required Section 1071 field: ethnicity'; application held for correction",
            },
            {
                "scenario_id": "TC-CFPB-002",
                "regulation_id": "CFPB-1071-001",
                "parent_requirement_id": "CFPB-1071-BIZ-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1002.107(a)",
                "scenario_name": "Invalid NAICS code format rejected",
                "priority": "medium",
                "given": "Loan application with NAICS code 'ABC123'",
                "when_step": "System validates NAICS code format",
                "then_step": "Validation error: 'NAICS code must be 2-6 digits'; applicant prompted to correct",
            },
            {
                "scenario_id": "TC-CFPB-003",
                "regulation_id": "CFPB-HMDA-001",
                "parent_requirement_id": "CFPB-HMDA-LAR-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1003.5(a)",
                "scenario_name": "HMDA LAR annual export passes edit checks",
                "priority": "high",
                "given": "Calendar year 2025 mortgage applications in system",
                "when_step": "Generate HMDA LAR export for 2025",
                "then_step": "Export passes all CFPB quality and validity edit checks; ready for submission by March 1",
            },
            {
                "scenario_id": "TC-CFPB-004",
                "regulation_id": "CFPB-REGB-001",
                "parent_requirement_id": "CFPB-REGB-ADV-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1002.9(a)(1)",
                "scenario_name": "Adverse action notice sent within 30 days",
                "priority": "high",
                "given": "Loan application denied on March 1",
                "when_step": "Day 28 — adverse action notice not yet sent",
                "then_step": "System generates urgent reminder; notice auto-drafted with denial reasons",
            },
            # --- Reg E test scenarios ---
            {
                "scenario_id": "TC-CFPB-005",
                "regulation_id": "CFPB-REGE-001",
                "parent_requirement_id": "CFPB-REGE-LIAB-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1005.6(b)",
                "scenario_name": "Unauthorized EFT reported within 2 days — $50 liability cap",
                "priority": "high",
                "given": "Consumer reports unauthorized $500 EFT within 2 business days",
                "when_step": "System calculates consumer liability",
                "then_step": "Consumer liability capped at $50; bank absorbs remaining $450",
            },
            {
                "scenario_id": "TC-CFPB-006",
                "regulation_id": "CFPB-REGE-001",
                "parent_requirement_id": "CFPB-REGE-ERR-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1005.11(c)",
                "scenario_name": "Error resolution — provisional credit within 10 days",
                "priority": "high",
                "given": "Consumer disputes $200 EFT charge on day 1",
                "when_step": "Investigation not complete by day 10",
                "then_step": "System issues provisional credit of $200 and extends investigation to 45 days",
            },
            {
                "scenario_id": "TC-CFPB-007",
                "regulation_id": "CFPB-REGE-001",
                "parent_requirement_id": "CFPB-REGE-PREAUTH-001",
                "regulator": "CFPB",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 1005.10(c)",
                "scenario_name": "Stop preauthorized EFT with 3 business days notice",
                "priority": "medium",
                "given": "Consumer requests stop on recurring $100 monthly EFT",
                "when_step": "Stop request received 4 business days before next transfer",
                "then_step": "Transfer is stopped; written confirmation requested within 14 days",
            },
            {
                "scenario_id": "TC-CFPB-008",
                "regulation_id": "CFPB-REGE-001",
                "scenario_name": "EFT initial disclosure missing fee schedule",
                "priority": "medium",
                "given": "New checking account opened with debit card",
                "when_step": "Initial EFT disclosures generated without fee schedule",
                "then_step": "System flags: 'Missing required Reg E disclosure: fees_for_eft_services'",
            },
            # REQ-009: Fair lending scenarios
            {
                "scenario_id": "TC-CFPB-009",
                "regulation_id": "CFPB-FAIRLEND-001",
                "scenario_name": "HMDA denial rate disparity triggers fair lending review",
                "priority": "critical",
                "given": "Bank's HMDA data shows minority applicant denial rate is 2.3x peer median in the MSA",
                "when_step": "Annual fair lending analysis runs against peer HMDA data",
                "then_step": "System flags the disparity ratio ≥ 2.0 threshold and generates a "
                             "comparative file review task assigned to the fair lending officer",
            },
            {
                "scenario_id": "TC-CFPB-010",
                "regulation_id": "CFPB-HMDA-PEER-001",
                "scenario_name": "HMDA peer benchmark comparison available for current year",
                "priority": "high",
                "given": "Bank files HMDA LAR for calendar year 2025",
                "when_step": "Compliance officer requests peer denial rate comparison for 2025",
                "then_step": "Dashboard displays bank denial rates vs peer median by race, ethnicity, "
                             "and income band for the bank's primary MSA",
            },
        ]
