"""
CRE Concentration Connector — Commercial Real Estate Concentration Risk (REQ-005)
Covers OCC/FRB/FDIC Joint Guidance (December 2006), SR 07-1.

High priority — many community banks face supervisory scrutiny for CRE concentration.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "joint_guidance_2006": "https://www.fdic.gov/news/financial-institution-letters/2007/fil07006.html",
    "occ_cre_guidance": "https://www.occ.gov/news-issuances/bulletins/2007/bulletin-2007-1.html",
    "fdic_cre_supervisory": "https://www.fdic.gov/credit/commercial-real-estate-lending",
    "frb_sr_07_1": "https://www.federalreserve.gov/boarddocs/srletters/2007/SR0701.htm",
    "fdic_cre_manual": "https://www.fdic.gov/regulations/examinations/supervisory/insights/siwin06/si_win06.pdf",
    "occ_cre_page": "https://www.occ.gov/topics/supervision-and-examination/credit-and-market-risk/commercial-real-estate/index-commercial-real-estate.html",
}


class CreConcentrationConnector(RegulatoryConnector):
    """Commercial Real Estate Concentration Risk regulatory connector.

    Covers:
      - Construction & Land Development: > 100% of risk-based capital threshold
      - Total CRE: > 300% of risk-based capital threshold
      - 36-month growth threshold: > 50% CRE growth triggers heightened scrutiny
      - Enhanced risk management standards for concentrations
      - Board and management oversight requirements
    """

    connector_name = "cre_concentration"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("cre_concentration_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("cre_concentration_extraction_started")
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
            logger.warning("cre_concentration_live_scrape_failed", error=str(exc))

        logger.info(
            "cre_concentration_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            scraped_documents=len(result["scraped_documents"]),
        )
        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            enrich_document,
            make_session,
            scrape_html_page,
            scrape_pdf_url,
        )

        session = make_session()
        docs: List[Dict] = []

        for url, source, category in [
            (self.urls["joint_guidance_2006"], "FDIC", "CRE Joint Guidance 2006"),
            (self.urls["occ_cre_guidance"], "OCC", "CRE OCC Bulletin 2007-1"),
            (self.urls["fdic_cre_supervisory"], "FDIC", "CRE Supervisory Guidance"),
            (self.urls["occ_cre_page"], "OCC", "CRE Supervision"),
        ]:
            docs.extend(scrape_html_page(session, url, source=source, category=category))

        docs.extend(
            scrape_pdf_url(
                session, self.urls["fdic_cre_manual"],
                source="FDIC", category="CRE Supervisory Insights",
            )
        )

        for doc in docs:
            enrich_document(doc, regulation_id="CRE-CONCENTRATION", doc_type="guidance")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        # CRE Joint Guidance is GUIDANCE, not law. The 100% construction /
        # 300% total-CRE thresholds are SUPERVISORY SCREENING criteria —
        # exceeding them triggers heightened scrutiny; it is NOT a
        # regulatory limit and does NOT constitute a violation.
        return [
            {
                "regulation_id": "CRE-JOINT-001",
                "regulation_name": "CRE Concentration Risk Joint Guidance (OCC/FRB/FDIC Dec 2006)",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance — FIL-104-2006 / OCC 2006-46 / SR 07-1",
                "category": "Commercial Real Estate",
                "subcategory": "Concentration Risk",
                "regulator": "FFIEC",
                "geography": "usa",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["joint_guidance_2006"],
                "last_updated": now,
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        # C-10 — all thresholds below are SUPERVISORY SCREENING criteria,
        # not codified limits. Exceeding them triggers heightened scrutiny
        # and warrants enhanced risk management practices — it is NOT a
        # violation. Reworded to make that distinction unambiguous.
        return [
            {
                "requirement_id": "CRE-REQ-CONSTR-001",
                "regulation_id": "CRE-JOINT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "requirement_text": (
                    "Construction and land development loans exceeding 100% of total "
                    "risk-based capital meet the supervisory SCREENING criterion for CRE "
                    "concentration and warrant heightened risk-management practices. This "
                    "is a screening threshold, not a regulatory limit; exceeding it is not "
                    "a violation."
                ),
                "rule_type": "concentration_threshold",
                "threshold_amount": 1.0,
                "threshold_currency": None,
                "test_requirement": "Verify system flags when construction/land loans > 100% of risk-based capital",
                "parameters": {
                    "threshold_pct_of_capital": 100.0,
                    "loan_types": ["construction", "land_development"],
                    "capital_measure": "total_risk_based_capital",
                    "alert_action": "enhanced_risk_management_required",
                },
            },
            {
                "requirement_id": "CRE-REQ-TOTAL-001",
                "regulation_id": "CRE-JOINT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "requirement_text": (
                    "Total commercial real estate loans exceeding 300% of total risk-based "
                    "capital meet the supervisory SCREENING criterion for CRE concentration "
                    "and warrant heightened risk-management practices. Total CRE includes "
                    "non-owner-occupied CRE, multifamily, and construction loans. Screening "
                    "threshold, not a regulatory limit; exceeding it is not a violation."
                ),
                "rule_type": "concentration_threshold",
                "threshold_amount": 3.0,
                "threshold_currency": None,
                "test_requirement": "Verify system flags when total CRE > 300% of risk-based capital",
                "parameters": {
                    "threshold_pct_of_capital": 300.0,
                    "loan_types": ["non_owner_occupied_cre", "multifamily", "construction"],
                    "capital_measure": "total_risk_based_capital",
                    "alert_action": "enhanced_risk_management_required",
                },
            },
            {
                "requirement_id": "CRE-REQ-GROWTH-001",
                "regulation_id": "CRE-JOINT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "requirement_text": (
                    "CRE portfolio growth exceeding 50% over the preceding 36-month period, "
                    "combined with either CRE concentration screening criterion being "
                    "exceeded, is a supervisory attention factor. Management is expected to "
                    "demonstrate awareness and risk controls. Screening factor, not a "
                    "regulatory limit."
                ),
                "rule_type": "growth_threshold",
                "threshold_amount": 50.0,
                "threshold_currency": None,
                "test_requirement": "Verify 36-month CRE growth computed; alert when growth >50% AND concentration threshold exceeded",
                "parameters": {
                    "growth_threshold_pct": 50.0,
                    "growth_window_months": 36,
                    "trigger_condition": "growth_threshold AND (construction_threshold OR total_cre_threshold)",
                },
            },
            {
                "requirement_id": "CRE-REQ-STRESS-001",
                "regulation_id": "CRE-JOINT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance — stress testing expectations",
                "requirement_text": (
                    "Institutions with significant CRE concentrations are expected to conduct "
                    "CRE portfolio stress tests at least annually. Supervisory reference "
                    "scenario: 20% decline in CRE collateral values evaluated for impact on "
                    "credit losses, capital adequacy, and profitability. Reference scenario, "
                    "not a codified regulatory requirement."
                ),
                "rule_type": "stress_test",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify annual CRE stress test conducted; 20% collateral decline scenario included",
                "parameters": {
                    "stress_scenario": "20_pct_collateral_decline",
                    "frequency": "annual",
                    "outputs": ["credit_loss_estimate", "capital_ratio_post_stress", "profitability_impact"],
                    "board_reporting_required": True,
                },
            },
            {
                "requirement_id": "CRE-REQ-POLICY-001",
                "regulation_id": "CRE-JOINT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance — governance expectations",
                "requirement_text": (
                    "Institutions with CRE concentrations are expected to maintain written "
                    "CRE concentration policies establishing internal limits by property "
                    "type, geography, and borrower. Board is expected to review and approve "
                    "CRE concentration limits annually. Supervisory expectation, not codified."
                ),
                "rule_type": "policy_requirement",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CRE concentration policy exists; limits set by property type; board-approved within 12 months",
                "parameters": {
                    "policy_elements": [
                        "limits_by_property_type",
                        "limits_by_geography",
                        "limits_by_borrower",
                    ],
                    "board_approval_frequency": "annual",
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — all KPIs are supervisory-screening metrics (GUIDANCE), not
        # regulatory limits. parent_requirement_id + authority_source keep
        # the chain-back to the 2006 Interagency Guidance intact.
        return [
            {
                "kpi_id": "CRE-KPI-001",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-CONSTR-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "kpi_name": "Construction Loans as % of Risk-Based Capital",
                "kpi_formula": "Construction & Land Development Loans / Total Risk-Based Capital * 100 (supervisory SCREENING criterion: 100%)",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 90.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "CRE-KPI-002",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-TOTAL-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "kpi_name": "Total CRE as % of Risk-Based Capital",
                "kpi_formula": "Total CRE Loans / Total Risk-Based Capital * 100 (supervisory SCREENING criterion: 300%)",
                "target_value": 300.0,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 275.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "CRE-KPI-003",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-GROWTH-001",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "kpi_name": "36-Month CRE Portfolio Growth Rate",
                "kpi_formula": "(Current CRE Balance - CRE Balance 36 months ago) / CRE Balance 36 months ago * 100 (supervisory attention factor at 50%)",
                "target_value": 50.0,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 40.0,
                "criticality": "high",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # C-11/C-12 — regulator=FFIEC (parent CRE-JOINT-001), authority_type=
        # GUIDANCE, authority_source. All wording softened to "screening
        # criterion exceeded" rather than "threshold exceeded".
        return [
            {
                "scenario_id": "TC-CRE-001",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-CONSTR-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "scenario_name": "Construction loans meet 100% capital screening criterion",
                "priority": "critical",
                "given": "Bank risk-based capital = $50M; construction/land loans = $52M",
                "when_step": "Quarterly CRE concentration ratio computed",
                "then_step": "System alerts: 'Construction loans = 104% of capital; supervisory screening criterion (100%) met; enhanced risk management warranted'",
            },
            {
                "scenario_id": "TC-CRE-002",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-TOTAL-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "scenario_name": "Total CRE meets 300% capital screening criterion",
                "priority": "critical",
                "given": "Bank risk-based capital = $80M; total CRE = $248M",
                "when_step": "Quarterly total CRE ratio computed",
                "then_step": "System alerts: 'Total CRE = 310% of capital; supervisory screening criterion (300%) met; heightened examiner attention expected'",
            },
            {
                "scenario_id": "TC-CRE-003",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-STRESS-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance — stress testing expectations",
                "scenario_name": "CRE stress test not performed — supervisory expectation gap",
                "priority": "high",
                "given": "Bank has construction loans meeting the 100% screening criterion",
                "when_step": "14 months elapsed since last CRE stress test",
                "then_step": "Compliance dashboard: 'CRE stress test overdue by 2 months against annual expectation'; exam finding risk elevated",
            },
            {
                "scenario_id": "TC-CRE-004",
                "regulation_id": "CRE-JOINT-001",
                "parent_requirement_id": "CRE-REQ-GROWTH-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "2006 Interagency CRE Guidance",
                "scenario_name": "36-month CRE growth at 55% combined with 300% concentration",
                "priority": "high",
                "given": "CRE portfolio grew 55% in 36 months AND total CRE > 300% capital",
                "when_step": "Quarterly risk metrics computed",
                "then_step": "Dual alert: 'Growth (55%) + Concentration (310%) supervisory attention factors both met; heightened scrutiny expected'",
            },
        ]
