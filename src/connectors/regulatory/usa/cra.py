"""
CRA Connector — Community Reinvestment Act (REQ-001)
Covers OCC (12 CFR Part 25), FRB (12 CFR Part 228), FDIC (12 CFR Part 345).

Critical — all insured depository institutions subject to periodic CRA exams.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "occ_cra": "https://www.occ.gov/topics/supervision-and-examination/cra/index-cra.html",
    "fdic_cra": "https://www.fdic.gov/banker-resource-center/community-reinvestment-act-cra",
    "frb_cra": "https://www.federalreserve.gov/apps/reportforms/moreinfo.aspx?pCode=CRA",
    "ffiec_cra_ratings": "https://www.ffiec.gov/cra/ratings.htm",
    "occ_cra_guidance_pdf": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/cra/pub-ch-cra.pdf",
    "fdic_cra_manual": "https://www.fdic.gov/regulations/cra/index.html",
    "frb_cra_examinations": "https://www.federalreserve.gov/releases/crainfo/",
    "rss_occ": "https://www.occ.gov/tools/subscribe/rss.xml",
}


class CraConnector(RegulatoryConnector):
    """Community Reinvestment Act regulatory connector.

    Covers:
      - CRA ratings: Outstanding | Satisfactory | Needs-to-Improve | Substantial Noncompliance
      - Exam types by asset size: Small Bank (<$600M), Intermediate (<$1.4B), Large (≥$1.4B)
      - Three-part test: Lending | Investment | Service
      - Assessment area definition requirements (12 CFR Part 25)
      - Public file maintenance and comment obligations
    """

    connector_name = "cra"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("cra_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("cra_extraction_started")
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
            logger.warning("cra_live_scrape_failed_using_static", error=str(exc))

        logger.info(
            "cra_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            kpis=len(kpis),
            test_scenarios=len(test_scenarios),
            scraped_documents=len(result["scraped_documents"]),
        )
        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            discover_compliance_urls,
            enrich_document,
            make_session,
            scrape_discovered_urls,
            scrape_html_page,
            scrape_pdf_url,
        )

        session = make_session()
        docs: List[Dict] = []

        for url, source, category in [
            (self.urls["occ_cra"], "OCC", "CRA Supervision"),
            (self.urls["fdic_cra"], "FDIC", "CRA Guidance"),
            (self.urls["ffiec_cra_ratings"], "FFIEC", "CRA Ratings"),
            (self.urls["fdic_cra_manual"], "FDIC", "CRA Manual"),
        ]:
            docs.extend(scrape_html_page(session, url, source=source, category=category))

        docs.extend(
            scrape_pdf_url(
                session,
                self.urls["occ_cra_guidance_pdf"],
                source="OCC",
                category="CRA Comptrollers Handbook",
            )
        )

        try:
            existing = set(self.urls.values())
            for agency in ("occ", "fdic"):
                discovered = discover_compliance_urls(
                    session, agency=agency, existing_urls=existing,
                    keywords=["/cra/", "/community-reinvestment", "/cra-examinations"],
                )
                if discovered.get("new_urls"):
                    docs.extend(
                        scrape_discovered_urls(
                            session, discovered,
                            source=agency.upper(), category="CRA Discovered",
                            max_html_pages=20, max_pdfs=5,
                        )
                    )
        except Exception as exc:
            logger.warning("cra_sitemap_discovery_failed", error=str(exc))

        for doc in docs:
            enrich_document(doc, regulation_id="CRA", doc_type="reg_text")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        # §9 audit — the 2023 interagency CRA final rule was published but
        # implementation has been litigated and phased; asset-size tiers
        # (small/intermediate/large) and section numbering are unsettled →
        # verify_before_display. C-13 — 2023-01-01 is a policy landing date,
        # not an FR `effective_on`; nulled pending FR connector backfill.
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "CRA-OCC-001",
                "regulation_name": "Community Reinvestment Act — OCC (12 CFR Part 25)",
                "category": "CRA",
                "subcategory": "Exam Framework",
                "regulator": "OCC",
                "geography": "usa",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. Part 25",
                "jurisdiction_level": "federal",
                "verify_before_display": True,
                "effective_date": None,
                "exam_frequency": "varies_by_asset_size",
                "source_url": self.urls["occ_cra"],
                "last_updated": now,
            },
            {
                "regulation_id": "CRA-FDIC-001",
                "regulation_name": "Community Reinvestment Act — FDIC (12 CFR Part 345)",
                "category": "CRA",
                "subcategory": "Exam Framework",
                "regulator": "FDIC",
                "geography": "usa",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. Part 345",
                "jurisdiction_level": "federal",
                "verify_before_display": True,
                "effective_date": None,
                "exam_frequency": "varies_by_asset_size",
                "source_url": self.urls["fdic_cra"],
                "last_updated": now,
            },
            {
                "regulation_id": "CRA-FRB-001",
                "regulation_name": "Community Reinvestment Act — FRB (12 CFR Part 228)",
                "category": "CRA",
                "subcategory": "Exam Framework",
                "regulator": "FRB",
                "geography": "usa",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. Part 228",
                "jurisdiction_level": "federal",
                "verify_before_display": True,
                "effective_date": None,
                "exam_frequency": "varies_by_asset_size",
                "source_url": self.urls["frb_cra"],
                "last_updated": now,
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        # §9 audit — 2023 CRA final rule restructured section numbering and
        # added an intermediate-bank tier that is NOT modelled here. Existing
        # cites (§ 25.41, § 25.26, § 25.21-25.28, § 25.43, § 25.28) are the
        # legacy numbering; keep them but flag verify_before_display so the
        # UI surfaces the pending re-scrape.
        return [
            {
                "requirement_id": "CRA-REQ-ASSESS-001",
                "regulation_id": "CRA-OCC-001",
                "requirement_text": (
                    "Institution must delineate one or more assessment areas "
                    "consisting of geographies where it has its main office, "
                    "branches, and deposit-taking ATMs (12 C.F.R. § 25.41)."
                ),
                "rule_type": "assessment_area_definition",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. § 25.41",
                "verify_before_display": True,
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify assessment area includes all branch and ATM geographies",
                "parameters": {
                    "must_include": ["main_office", "branches", "deposit_taking_atms"],
                    "cannot_arbitrarily_exclude": "low_moderate_income_geographies",
                },
            },
            {
                "requirement_id": "CRA-REQ-EXAM-SMALL-001",
                "regulation_id": "CRA-OCC-001",
                "requirement_text": (
                    "Small banks (assets < $600M) evaluated on Lending Test only: "
                    "loan-to-deposit ratio, assessment area loans, borrower income "
                    "distribution, and geographic distribution."
                ),
                "rule_type": "exam_type",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. § 25.26",
                "verify_before_display": True,
                "threshold_amount": 600_000_000,
                "threshold_currency": "USD",
                "test_requirement": "Verify small-bank CRA test criteria applied for banks under $600M assets",
                "parameters": {
                    "bank_tier": "small",
                    "asset_threshold_usd": 600_000_000,
                    "test_components": ["loan_to_deposit_ratio", "geographic_distribution", "borrower_income"],
                },
            },
            {
                "requirement_id": "CRA-REQ-EXAM-LARGE-001",
                "regulation_id": "CRA-OCC-001",
                "requirement_text": (
                    "Large banks (assets ≥ $1.4B) evaluated on three tests: "
                    "Lending Test (weighted 50%), Investment Test (25%), Service Test (25%). "
                    "Must receive at least Satisfactory on Lending to achieve overall Satisfactory. "
                    "NOTE: intermediate-bank tier under the 2023 final rule is not modelled here — "
                    "re-scrape pending."
                ),
                "rule_type": "exam_type",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. § 25.21-25.28",
                "verify_before_display": True,
                "threshold_amount": 1_400_000_000,
                "threshold_currency": "USD",
                "test_requirement": "Verify three-part test weighted scoring applied for banks ≥$1.4B assets",
                "parameters": {
                    "bank_tier": "large",
                    "asset_threshold_usd": 1_400_000_000,
                    "lending_test_weight": 0.50,
                    "investment_test_weight": 0.25,
                    "service_test_weight": 0.25,
                    "lending_test_minimum": "Satisfactory",
                },
            },
            {
                "requirement_id": "CRA-REQ-PUBLIC-001",
                "regulation_id": "CRA-OCC-001",
                "requirement_text": (
                    "Institution must maintain a public CRA file at its main office "
                    "and each branch, available for inspection during business hours. "
                    "File must include CRA notice, most recent CRA exam performance evaluation, "
                    "and all written comments from public."
                ),
                "rule_type": "public_disclosure",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. § 25.43",
                "verify_before_display": True,
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CRA public file maintained and accessible at all branch locations",
                "parameters": {
                    "required_contents": [
                        "cra_public_notice",
                        "latest_performance_evaluation",
                        "written_public_comments",
                    ],
                    "availability": "business_hours",
                    "locations": ["main_office", "all_branches"],
                },
            },
            {
                "requirement_id": "CRA-REQ-RATING-001",
                "regulation_id": "CRA-OCC-001",
                "requirement_text": (
                    "CRA ratings: Outstanding | Satisfactory | Needs-to-Improve | "
                    "Substantial Noncompliance. A rating of Substantial Noncompliance "
                    "will be considered in expansion applications."
                ),
                "rule_type": "rating_system",
                "authority_type": "LAW",
                "cfr_citation": "12 C.F.R. § 25.28",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CRA rating logic maps performance to correct rating category",
                "parameters": {
                    "ratings": ["Outstanding", "Satisfactory", "Needs-to-Improve", "Substantial Noncompliance"],
                    "expansion_impact": "Substantial Noncompliance can block merger/branch applications",
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited authority on every KPI.
        # regulator on each row (C-12) so downstream doesn't fall back to the
        # multi-agency default of "CRA".
        return [
            {
                "kpi_id": "CRA-KPI-001",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-EXAM-SMALL-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.26",
                "kpi_name": "Loan-to-Deposit Ratio (LMI Focus)",
                "kpi_formula": "CRA-eligible loans in assessment area / Total deposits",
                "target_value": None,
                "target_unit": "ratio",
                "monitoring_frequency": "quarterly",
                "alert_threshold": None,
                "criticality": "high",
            },
            {
                "kpi_id": "CRA-KPI-002",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-EXAM-SMALL-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.26",
                "kpi_name": "LMI Borrower Lending Share",
                "kpi_formula": "(Loans to LMI borrowers / Total loans) * 100",
                "target_value": None,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": None,
                "criticality": "high",
            },
            {
                "kpi_id": "CRA-KPI-003",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-ASSESS-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.41",
                "kpi_name": "LMI Geography Lending Share",
                "kpi_formula": "(Loans in LMI census tracts / Total loans in assessment area) * 100",
                "target_value": None,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": None,
                "criticality": "high",
            },
            {
                "kpi_id": "CRA-KPI-004",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-EXAM-LARGE-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.21-25.28",
                "kpi_name": "Community Development Investment Amount",
                "kpi_formula": "Total qualified community development investments YTD",
                "target_value": None,
                "target_unit": "usd",
                "monitoring_frequency": "monthly",
                "alert_threshold": None,
                "criticality": "medium",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-CRA-001",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-ASSESS-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.41",
                "scenario_name": "Assessment area excludes LMI tract — flagged",
                "priority": "critical",
                "given": "Bank has branches in LMI census tract 42101-0001",
                "when_step": "CRA assessment area delineation submitted without tract 42101-0001",
                "then_step": "System flags: 'Assessment area arbitrarily excludes LMI geography'; CRA file updated",
            },
            {
                "scenario_id": "TC-CRA-002",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-EXAM-SMALL-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.26",
                "scenario_name": "Small bank CRA exam — loan-to-deposit ratio check",
                "priority": "high",
                "given": "Community bank with $450M assets (Small Bank CRA tier)",
                "when_step": "CRA exam evaluates loan activity for prior year",
                "then_step": "Exam applies Lending Test only; LTD ratio computed against peer banks in same assessment area",
            },
            {
                "scenario_id": "TC-CRA-003",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-EXAM-LARGE-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.21-25.28",
                "scenario_name": "Large bank fails Lending Test — cannot achieve Satisfactory overall",
                "priority": "critical",
                "given": "Regional bank with $2B assets receives 'Needs-to-Improve' on Lending Test",
                "when_step": "System computes overall CRA rating",
                "then_step": "Overall rating cannot exceed Needs-to-Improve regardless of Investment or Service test scores",
            },
            {
                "scenario_id": "TC-CRA-004",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-PUBLIC-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.43",
                "scenario_name": "CRA public file missing latest exam evaluation",
                "priority": "high",
                "given": "CRA exam completed and rating published",
                "when_step": "Examiner checks public file at main office",
                "then_step": "Public file contains most recent performance evaluation and all public comments",
            },
            {
                "scenario_id": "TC-CRA-005",
                "regulation_id": "CRA-OCC-001",
                "parent_requirement_id": "CRA-REQ-RATING-001",
                "regulator": "OCC",
                "authority_type": "LAW",
                "citation": "12 C.F.R. § 25.28",
                "scenario_name": "Substantial Noncompliance blocks merger application",
                "priority": "critical",
                "given": "Bank received Substantial Noncompliance CRA rating",
                "when_step": "Bank submits application to acquire competitor bank",
                "then_step": "Regulator flags CRA rating as barrier; application processing delayed pending remediation",
            },
        ]
