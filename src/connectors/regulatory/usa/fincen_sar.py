"""
FinCEN SAR Connector — Suspicious Activity Report Filing Requirements
Extracts SAR filing triggers, thresholds, required fields, and deadlines.

Sources:
  - https://www.fincen.gov/resources/filing-information
  - FinCEN SAR Instructions PDF
  - BSA E-Filing System

Priority: P0 CRITICAL — 6 test cases (17% of total)
Status: ✅ COMPLETE
"""
from datetime import datetime
from typing import Dict, List, Any, Optional
import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

# Default URLs — overridable via config/connectors/usa.yaml → source_urls.fincen_sar
_DEFAULT_URLS = {
    "filing_information": "https://www.fincen.gov/resources/filing-information",
    "legal_authorities": "https://www.fincen.gov/resources/fincens-legal-authorities",
    "beneficial_ownership": "https://www.fincen.gov/boi",
    "administrative_rulings": "https://www.fincen.gov/resources/statutes-regulations/administrative-rulings",
    "guidance": "https://www.fincen.gov/resources/statutes-regulations/guidance",
    # REQ-015: Add FinCEN guidance URL (interpretive guidance)
    "fincen_guidance": "https://www.fincen.gov/resources/statutes-regulations/guidance",
    # Resolved dynamically via year-walking; this is the last known working URL
    "sar_activity_review_pdf": "https://www.fincen.gov/sites/default/files/shared/sar_tti_23.pdf",
    "news_page": "https://www.fincen.gov/news",
}

# URL patterns for year-walking SAR PDF resolution
_SAR_PDF_PATTERNS = [
    "https://www.fincen.gov/sites/default/files/shared/sar_tti_{yy}.pdf",
    "https://www.fincen.gov/system/files/shared/sar_tti_{yy}.pdf",
]


class FincenSarConnector(RegulatoryConnector):
    """FinCEN SAR filing connector.

    Covers:
      - SAR filing triggers ($0 insider abuse, $5K identified, $25K unidentified)
      - 30-day filing deadline
      - Required SAR form fields
      - Continuing activity SARs (90-day follow-up)
    """

    connector_name = "fincen_sar"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("fincen_sar_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("fincen_sar_extraction_started")

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
            logger.warning("fincen_sar_live_scrape_failed_using_static", error=str(exc))

        logger.info(
            "fincen_sar_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            kpis=len(kpis),
            test_scenarios=len(test_scenarios),
            scraped_documents=len(result["scraped_documents"]),
        )
        return result

    def _scrape_live(self) -> List[Dict]:
        """Scrape FinCEN filing info, legal authorities, BOI page, PDFs, and sitemap-discovered content."""
        from src.connectors.regulatory.usa._scraper_utils import (
            discover_compliance_urls,
            enrich_document,
            make_session,
            resolve_fincen_sar_pdf,
            scrape_discovered_urls,
            scrape_html_page,
            scrape_pdf_url,
        )

        session = make_session()
        docs: List[Dict] = []

        # 1. Filing information page
        docs.extend(
            scrape_html_page(
                session,
                self.urls["filing_information"],
                source="FinCEN",
                category="SAR Filing",
            )
        )

        # 2. Legal authorities (statutes & regulations)
        docs.extend(
            scrape_html_page(
                session,
                self.urls["legal_authorities"],
                source="FinCEN",
                category="Legal Authorities",
            )
        )

        # 3. Beneficial Ownership Information page
        docs.extend(
            scrape_html_page(
                session,
                self.urls["beneficial_ownership"],
                source="FinCEN",
                category="Beneficial Ownership",
                content_selectors=["div.field--name-body", "article"],
            )
        )

        # 4. Administrative Rulings
        docs.extend(
            scrape_html_page(
                session,
                self.urls["administrative_rulings"],
                source="FinCEN",
                category="Administrative Rulings",
                content_selectors=["div.view-content", "div.field--name-body", "article"],
            )
        )

        # 5. Guidance (statutes & regulations)
        docs.extend(
            scrape_html_page(
                session,
                self.urls["guidance"],
                source="FinCEN",
                category="Guidance",
                content_selectors=["div.view-content", "div.field--name-body", "article"],
            )
        )

        # 6. SAR Activity Review PDF — resolve via year-walking
        #    Probes current year → previous years across multiple URL patterns.
        resolved_url = resolve_fincen_sar_pdf(
            session,
            base_pattern=_SAR_PDF_PATTERNS[0],
            fallback_patterns=_SAR_PDF_PATTERNS[1:],
        )
        pdf_url = resolved_url or self.urls["sar_activity_review_pdf"]
        docs.extend(
            scrape_pdf_url(
                session,
                pdf_url,
                source="FinCEN",
                category="SAR Activity Review",
            )
        )

        # 7. Sitemap-driven discovery — find compliance pages not in hardcoded URLs
        try:
            existing = set(self.urls.values())
            discovered = discover_compliance_urls(
                session, agency="fincen",
                sitemap_url=self.urls.get("sitemap_url"),
                existing_urls=existing,
            )
            if discovered.get("new_urls"):
                logger.info("fincen_sitemap_discovery", new_urls=len(discovered["new_urls"]))
                docs.extend(
                    scrape_discovered_urls(
                        session, discovered, source="FinCEN",
                        category="Discovered Compliance Content",
                        max_html_pages=30, max_pdfs=10,
                    )
                )
        except Exception as exc:
            logger.warning("fincen_sitemap_discovery_failed", error=str(exc))

        # Enrich all docs with obligation metadata, CFR citations, and regulation IDs
        enriched = []
        for doc in docs:
            doc_type = "reg_text"
            cat = doc.get("category", "").lower()
            if "guidance" in cat or "ruling" in cat:
                doc_type = "guidance"
            elif "activity review" in cat:
                doc_type = "reg_text"
            reg_id = "FINCEN-SAR-001"
            title = (doc.get("title", "") + " " + doc.get("section_title", "")).lower()
            if "continuing" in title:
                reg_id = "FINCEN-SAR-002"
            enriched.append(enrich_document(
                doc,
                regulation_id=reg_id,
                state_code=None,
                doc_type=doc_type,
            ))
        return enriched

    def check_updates(self) -> List[Dict]:
        """Scrape FinCEN news page for recent advisories and guidance.

        FinCEN deprecated their RSS feed; we poll the news page instead.
        """
        from src.connectors.regulatory.usa._scraper_utils import (
            make_session,
            scrape_html_page,
        )

        session = make_session()
        return scrape_html_page(
            session,
            self.urls["news_page"],
            source="FinCEN",
            category="News & Advisories",
        )

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        filing_url = self.urls["filing_information"]
        return [
            {
                "regulation_id": "FINCEN-SAR-001",
                "regulation_name": "Suspicious Activity Report Filing Thresholds and Deadlines",
                "cfr_citation": "31 C.F.R. § 1020.320",
                "rule_text": (
                    "Banks must file a Suspicious Activity Report (SAR) for any transaction "
                    "that involves or aggregates to $5,000 or more where the institution knows, "
                    "suspects, or has reason to suspect money laundering, BSA violations, or "
                    "structuring. For insider abuse, SAR must be filed regardless of amount. "
                    "For transactions of $25,000 or more, a SAR must be filed even if no suspect "
                    "can be identified. SARs must be filed within 30 calendar days of initial "
                    "detection. 31 C.F.R. § 1020.320(b)(3); BSA/AML Examination Manual."
                ),
                "category": "AML",
                "subcategory": "SAR Filing",
                "regulator": "FinCEN",
                "geography": "usa",
                "effective_date": "2012-03-01",
                "exam_frequency": "monthly",
                "source_url": filing_url,
            },
            {
                "regulation_id": "FINCEN-SAR-002",
                "regulation_name": "SAR Continuing Activity Reports",
                "cfr_citation": "31 C.F.R. § 1020.320",
                "authority_type": "LAW",
                "rule_text": (
                    "When suspicious activity continues after an initial SAR is filed, the "
                    "institution reviews the account for a 90-day period following the prior "
                    "SAR, then has 30 additional calendar days to file — a 120-calendar-day "
                    "deadline from the date of the previously related SAR filing. Per FinCEN's "
                    "Oct-2025 SAR FAQs this 120-day path is ELECTIVE; an institution may "
                    "instead file within 30 days of identifying continuing activity. A separate "
                    "90-day review is NOT required where risk-based monitoring is reasonably "
                    "designed to identify and report continuing activity. "
                    "31 C.F.R. § 1020.320; FIN-2007-G003; FinCEN SAR FAQs (Oct 2025)."
                ),
                "category": "AML",
                "subcategory": "SAR Filing",
                "regulator": "FinCEN",
                "geography": "usa",
                "effective_date": None,   # rule text unchanged; last FAQ clarification 2025-10
                "exam_frequency": "quarterly",
                "source_url": filing_url,
                "thresholds": {
                    "continuing_sar_deadline_days": 120,
                    "continuing_sar_review_days":    90,
                    "continuing_sar_is_elective":  True,
                    "separate_review_required":   False,
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements — filing triggers + SAR fields
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            # --- Filing triggers ---
            {
                "requirement_id": "FINCEN-SAR-TRIG-001",
                "regulation_id": "FINCEN-SAR-001",
                "cfr_citation": "31 C.F.R. § 1020.320(a)",
                "requirement_text": "Insider abuse involving any amount requires SAR filing",
                "rule_type": "sar_trigger",
                "threshold_amount": 0,
                "threshold_currency": "USD",
                "test_requirement": "Verify SAR auto-triggered for insider abuse regardless of amount",
                "parameters": {
                    "trigger_type": "insider_abuse",
                    "minimum_amount": 0,
                    "filing_mandatory": True,
                    "filing_deadline_days": 30,
                    "deadline_from": "date_of_detection",
                },
            },
            {
                "requirement_id": "FINCEN-SAR-TRIG-002",
                "regulation_id": "FINCEN-SAR-001",
                "cfr_citation": "31 C.F.R. § 1020.320(b)(3)",
                "requirement_text": "Violations aggregating $5,000 or more where suspect can be identified require SAR filing",
                "rule_type": "sar_trigger",
                "threshold_amount": 5000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify SAR triggered when suspicious activity >= $5K and suspect identified",
                "parameters": {
                    "trigger_type": "identified_suspect",
                    "minimum_amount": 5000.00,
                    "filing_mandatory": True,
                    "filing_deadline_days": 30,
                },
            },
            {
                "requirement_id": "FINCEN-SAR-TRIG-003",
                "regulation_id": "FINCEN-SAR-001",
                "cfr_citation": "31 C.F.R. § 1020.320(b)(3)",
                "requirement_text": "Violations aggregating $25,000 or more regardless of suspect identification require SAR filing",
                "rule_type": "sar_trigger",
                "threshold_amount": 25000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify SAR triggered when suspicious activity >= $25K even without identified suspect",
                "parameters": {
                    "trigger_type": "unidentified_suspect",
                    "minimum_amount": 25000.00,
                    "filing_mandatory": True,
                    "filing_deadline_days": 30,
                },
            },
            # --- Filing deadline ---
            # 30 days when a suspect is identified; 60 days when no
            # suspect can be identified but the activity is nonetheless
            # reportable. 31 C.F.R. § 1020.320(b)(3).
            {
                "requirement_id": "FINCEN-SAR-DEADLINE-001",
                "regulation_id": "FINCEN-SAR-001",
                "cfr_citation": "31 C.F.R. § 1020.320(b)(3)",
                "authority_type": "LAW",
                "requirement_text": (
                    "SAR must be filed within 30 calendar days of initial detection of "
                    "suspicious activity. When no suspect can be identified, the deadline "
                    "extends to 60 calendar days."
                ),
                "rule_type": "filing_deadline",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify system enforces 30-day SAR filing deadline (60-day when no suspect identified) with alerts at 20 and 25 days",
                "parameters": {
                    "deadline_days": 30,
                    "deadline_days_no_suspect": 60,
                    "early_warning_days": [20, 25],
                    "escalation_on_miss": True,
                },
            },
            # --- Continuing activity SAR (120-day deadline, elective) ---
            # 90-day review + 30-day file window; prior text stated 90 days
            # to file which is incorrect. Per FinCEN Oct-2025 SAR FAQs, the
            # 120-day path is elective — institutions may instead file
            # within 30 days of identifying continuing activity.
            {
                "requirement_id": "FINCEN-SAR-CONT-001",
                "regulation_id": "FINCEN-SAR-002",
                "cfr_citation": "31 C.F.R. § 1020.320(b)(3)",
                "authority_type": "LAW",
                "requirement_text": (
                    "Continuing activity SAR must be filed within 120 calendar days of the "
                    "previously related SAR (90-day review + 30-day file window). Elective "
                    "path per FinCEN Oct-2025 SAR FAQs; alternative is to file within 30 "
                    "days of identifying continuing activity."
                ),
                "rule_type": "continuing_activity",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify system triggers continuing-activity SAR review and enforces the 120-day filing deadline",
                "parameters": {
                    "continuing_sar_deadline_days": 120,
                    "continuing_sar_review_days":    90,
                    "continuing_sar_is_elective":  True,
                    "separate_review_required":   False,
                    "auto_reminder": True,
                    "early_warning_days": [100, 110, 115],
                },
            },
            # --- Required SAR form fields (key fields) ---
            {
                "requirement_id": "FINCEN-SAR-FIELD-001",
                "regulation_id": "FINCEN-SAR-001",
                "cfr_citation": "31 C.F.R. § 1020.320(b)(2)",
                "requirement_text": "SAR must include subject name, TIN/SSN, suspicious activity date, and dollar amount",
                "rule_type": "required_fields",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify SAR form validation rejects submission missing required fields",
                "parameters": {
                    "required_fields": [
                        {"field": "subject_name", "type": "text", "max_length": 150},
                        {"field": "subject_tin_ssn", "type": "text", "format": "XXX-XX-XXXX or XX-XXXXXXX"},
                        {"field": "suspicious_activity_date", "type": "date"},
                        {"field": "total_dollar_amount", "type": "numeric", "min": 0, "max": 999999999.99},
                        {"field": "narrative", "type": "text", "min_length": 1},
                    ],
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        return [
            {
                "kpi_id": "FINCEN-SAR-KPI-001",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-DEADLINE-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(3)",
                "kpi_name": "SAR Filing Timeliness",
                "kpi_formula": "(SARs filed within 30 days / Total SARs due) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 95.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "FINCEN-SAR-KPI-002",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-FIELD-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(2)",
                "kpi_name": "SAR Quality Score",
                "kpi_formula": "Average completeness score of filed SARs (all required fields populated)",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 90.0,
                "criticality": "high",
            },
            {
                "kpi_id": "FINCEN-SAR-KPI-003",
                "regulation_id": "FINCEN-SAR-002",
                "parent_requirement_id": "FINCEN-SAR-CONT-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(3)",
                "kpi_name": "Continuing Activity SAR Filing Timeliness",
                "kpi_formula": "(Continuing-activity SARs filed within 120 days of prior SAR / Total continuing-activity SARs due) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-013",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-TRIG-002",
                "regulator": "FinCEN",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(3)",
                "scenario_name": "SAR filing triggered for $5,500 suspicious wire transfer",
                "priority": "high",
                "given": "Customer sends wire transfer $5,500 to high-risk jurisdiction",
                "when_step": "AML monitoring system flags transaction as suspicious",
                "then_step": "SAR auto-created with 30-day filing deadline; form pre-populated with transaction details",
            },
            {
                "scenario_id": "TC-014",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-DEADLINE-001",
                "regulator": "FinCEN",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(3)",
                "scenario_name": "SAR filing deadline enforcement — 30 days",
                "priority": "high",
                "given": "Suspicious activity detected on Jan 1, 2026",
                "when_step": "Current date is Jan 28, 2026 (day 27) and SAR not yet filed",
                "then_step": "System generates urgent alert: 'SAR filing due in 3 days'; escalation to BSA Officer",
            },
            {
                "scenario_id": "TC-015",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-FIELD-001",
                "regulator": "FinCEN",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(2)",
                "scenario_name": "SAR rejected for missing required fields",
                "priority": "high",
                "given": "Compliance officer prepares SAR submission",
                "when_step": "Officer submits SAR without narrative description",
                "then_step": "System rejects: 'Narrative is required'; SAR remains in draft status",
            },
            {
                "scenario_id": "TC-016",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-TRIG-001",
                "regulator": "FinCEN",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(a)",
                "scenario_name": "Insider abuse triggers SAR regardless of amount",
                "priority": "critical",
                "given": "Bank employee accesses accounts they are not authorized to view",
                "when_step": "Unauthorized access detected for $200 account",
                "then_step": "SAR triggered for insider abuse ($0 threshold); filing deadline 30 days from detection",
            },
            {
                "scenario_id": "TC-018",
                "regulation_id": "FINCEN-SAR-001",
                "parent_requirement_id": "FINCEN-SAR-TRIG-003",
                "regulator": "FinCEN",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(3)",
                "scenario_name": "$25K unidentified suspect SAR trigger",
                "priority": "high",
                "given": "Multiple wire transfers totaling $30,000 from unidentified source",
                "when_step": "AML system aggregates transactions and flags suspicious pattern",
                "then_step": "SAR triggered per $25K unidentified-suspect rule; suspect info marked 'Unknown'",
            },
            # Rewritten to reflect 120-day continuing-activity deadline (was 90d)
            {
                "scenario_id": "TC-024",
                "regulation_id": "FINCEN-SAR-002",
                "parent_requirement_id": "FINCEN-SAR-CONT-001",
                "regulator": "FinCEN",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.320(b)(3)",
                "scenario_name": "120-day continuing-activity SAR deadline alert",
                "priority": "medium",
                "given": "Initial SAR filed Jan 1, 2026 for ongoing suspicious pattern",
                "when_step": "Day 110 reached, activity continues, continuing SAR not yet filed",
                "then_step": "System alerts BSA team: 'Continuing activity SAR due in 10 days (120-day deadline from prior SAR)'",
            },
        ]
