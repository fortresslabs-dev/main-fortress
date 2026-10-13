"""
Liquidity Risk Connector — REQ-007
Covers FDIC / OCC / FRB liquidity risk management guidance.

Sources:
  https://www.fdic.gov/resources/supervision-and-examinations/examination-policies-manual/section-6-1.pdf
  https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/liquidity/index-liquidity.html
  Interagency Policy Statement on Funding and Liquidity Risk (2010)
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "occ_liquidity": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/liquidity/index-liquidity.html",
    "fdic_liquidity": "https://www.fdic.gov/resources/supervision-and-examinations/examination-policies-manual/section-6-1.pdf",
    # FRB renamed /supervisionreg/topics/supervision_liquidity.htm to
    # /supervisionreg/topics/liquidity_risk.htm in the 2024 reorg; the old
    # path 404s but the underscore-form replacement is canonical.
    "frb_liquidity": "https://www.federalreserve.gov/supervisionreg/topics/liquidity_risk.htm",
    "interagency_2010": "https://www.fdic.gov/regulations/applications/ifr/2010/2010-12-ifr.pdf",
    "occ_post_svb": "https://www.occ.gov/news-issuances/bulletins/2023/bulletin-2023-13.html",
}


class LiquidityRiskConnector(RegulatoryConnector):
    """Liquidity Risk Management connector.

    Covers:
      - Contingency Funding Plan (CFP) — minimum 3 stress scenarios
      - Liquidity buffer adequacy (30-day survival horizon)
      - Uninsured deposit concentration threshold (50% of total deposits)
      - Intraday liquidity monitoring
      - Board-approved liquidity risk tolerance
      - Collateral management and encumbrance tracking
    """

    connector_name = "liquidity_risk"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("liquidity_risk_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        result: Dict[str, List[Dict]] = {
            "regulations": self._build_regulations(),
            "requirements": self._build_requirements(),
            "kpis": self._build_kpis(),
            "test_scenarios": self._build_test_scenarios(),
            "scraped_documents": [],
        }
        try:
            result["scraped_documents"] = self._scrape_live()
        except Exception as exc:
            logger.warning("liquidity_risk_live_scrape_failed", error=str(exc))
        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            make_session, scrape_html_page, scrape_pdf_url,
        )
        session = make_session()
        docs: List[Dict] = []
        docs.extend(scrape_html_page(session, self.urls["occ_liquidity"], source="OCC", category="Liquidity Handbook"))
        docs.extend(scrape_html_page(session, self.urls["frb_liquidity"], source="FRB", category="Liquidity Supervision"))
        docs.extend(scrape_html_page(session, self.urls["occ_post_svb"], source="OCC", category="Liquidity Post-SVB Guidance"))
        docs.extend(scrape_pdf_url(session, self.urls["fdic_liquidity"], source="FDIC", category="FDIC Liquidity Exam Manual"))
        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        # Item 11 — all rows are GUIDANCE. Interagency Policy Statement (2010),
        # OCC Comptroller's Handbook, and FDIC FILs are supervisory guidance,
        # not CFR sections. authority_source populated; cfr_citation=None.
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "LIQ-CFP-001",
                "regulation_name": "Contingency Funding Plan — Minimum Stress Scenarios",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement on Funding and Liquidity Risk (March 2010)",
                "rule_text": "Institutions are expected to maintain a Contingency Funding Plan (CFP) "
                             "with at least three distinct stress scenarios (mild, moderate, severe) "
                             "covering institution-specific, market-wide, and combined crises, "
                             "updated annually. Supervisory expectation.",
                "category": "Liquidity Risk",
                "subcategory": "Contingency Funding",
                "regulator": "FDIC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["fdic_liquidity"],
                "last_updated": now,
            },
            {
                "regulation_id": "LIQ-BUFFER-001",
                "regulation_name": "Liquidity Buffer — 30-Day Survival Horizon (supervisory reference)",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Comptroller's Handbook — Liquidity",
                "rule_text": "Institutions are expected to maintain a liquidity buffer of high-quality "
                             "liquid assets sufficient to cover net cash outflows over a supervisory "
                             "reference 30-day stress horizon without relying on new wholesale funding.",
                "category": "Liquidity Risk",
                "subcategory": "Liquidity Buffer",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_liquidity"],
                "last_updated": now,
            },
            {
                "regulation_id": "LIQ-UNINSURED-001",
                "regulation_name": "Uninsured Deposit Concentration — Enhanced-Monitoring Trigger",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2023-13; post-SVB interagency guidance on liquidity risk",
                "rule_text": "For institutions with uninsured deposits exceeding a supervisory "
                             "reference threshold of ~50% of total deposits, enhanced liquidity "
                             "monitoring, CFP documentation of concentration risk, and monthly ALCO "
                             "reporting are supervisory expectations. Not a codified regulatory "
                             "limit.",
                "category": "Liquidity Risk",
                "subcategory": "Deposit Concentration",
                "regulator": "FDIC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": self.urls["occ_post_svb"],
                "last_updated": now,
            },
            {
                "regulation_id": "LIQ-GOVERNANCE-001",
                "regulation_name": "Board-Approved Liquidity Risk Tolerance",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement on Funding and Liquidity Risk (2010) Section III",
                "rule_text": "Board of directors is expected to establish and annually review a "
                             "formal liquidity risk tolerance statement, with limits cascaded to "
                             "ALCO and monitored via key risk indicators.",
                "category": "Liquidity Risk",
                "subcategory": "Governance",
                "regulator": "FRB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["frb_liquidity"],
                "last_updated": now,
            },
            {
                "regulation_id": "LIQ-COLLATERAL-001",
                "regulation_name": "Collateral Encumbrance and Availability Tracking",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Comptroller's Handbook — Liquidity Section IV",
                "rule_text": "Institutions are expected to track collateral encumbrance daily, "
                             "maintain a complete inventory of unencumbered assets available for "
                             "pledging, and model collateral availability under each CFP stress "
                             "scenario.",
                "category": "Liquidity Risk",
                "subcategory": "Collateral Management",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_liquidity"],
                "last_updated": now,
            },
        ]

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "LIQ-CFP-001-R1",
                "regulation_id": "LIQ-CFP-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement 2010 — Section IV.B",
                "requirement_text": "Institution should maintain a written CFP with at least three "
                                    "stress scenarios (institution-specific, systemic, combined), "
                                    "updated at minimum annually and tested via simulation.",
                "rule_type": "cfp_requirement",
                "threshold_amount": 3.0,
                "test_requirement": "Verify CFP documents minimum 3 scenarios with cash flow projections",
                "parameters": {"min_scenarios": 3, "update_frequency": "annual", "test_frequency": "annual"},
            },
            {
                "requirement_id": "LIQ-BUFFER-001-R1",
                "regulation_id": "LIQ-BUFFER-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Liquidity Handbook Section III",
                "requirement_text": "Institution should maintain HQLA buffer covering projected "
                                    "30-day net outflows under stress, measured daily by treasury "
                                    "and reported weekly to ALCO.",
                "rule_type": "liquidity_policy",
                "threshold_amount": 30.0,
                "test_requirement": "Verify HQLA buffer calculation methodology and ALCO reporting",
                "parameters": {"survival_horizon_days": 30, "reporting_frequency": "weekly"},
            },
            {
                "requirement_id": "LIQ-UNINSURED-001-R1",
                "regulation_id": "LIQ-UNINSURED-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2023-13; post-SVB interagency guidance",
                "requirement_text": "When uninsured deposits exceed a supervisory reference of 50% "
                                    "of total deposits, enhanced daily liquidity monitoring and CFP "
                                    "documentation of the concentration risk are supervisory "
                                    "expectations.",
                "rule_type": "liquidity_policy",
                "threshold_amount": 50.0,
                "test_requirement": "Verify enhanced monitoring triggers when uninsured deposits exceed 50%",
                "parameters": {
                    "threshold_pct": 50,
                    "threshold_currency": "PCT_DEPOSITS",
                    "enhanced_monitoring_frequency": "daily",
                },
            },
            {
                "requirement_id": "LIQ-GOVERNANCE-001-R1",
                "regulation_id": "LIQ-GOVERNANCE-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement 2010 — Section III.A",
                "requirement_text": "Board should formally approve the liquidity risk tolerance "
                                    "statement annually, with evidence of review in board minutes.",
                "rule_type": "liquidity_policy",
                "threshold_amount": None,
                "test_requirement": "Verify board minutes contain annual liquidity risk tolerance approval",
                "parameters": {"approval_frequency": "annual"},
            },
        ]

    def _build_kpis(self) -> List[Dict[str, Any]]:
        return [
            {
                "kpi_id": "LIQ-KPI-001",
                "regulation_id": "LIQ-CFP-001",
                "parent_requirement_id": "LIQ-CFP-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement 2010 — Section IV.B",
                "kpi_name": "CFP Stress Scenario Count",
                "description": "Number of distinct CFP stress scenarios documented (supervisory reference: >=3)",
                "threshold": 3.0,
                "threshold_type": "minimum_count",
                "unit": "count",
            },
            {
                "kpi_id": "LIQ-KPI-002",
                "regulation_id": "LIQ-BUFFER-001",
                "parent_requirement_id": "LIQ-BUFFER-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Liquidity Handbook Section III",
                "kpi_name": "Liquidity Coverage Ratio (internal reference)",
                "description": "HQLA / net 30-day outflows — internal reference target 100%",
                "threshold": 100.0,
                "threshold_type": "minimum_percent",
                "unit": "percent",
            },
            {
                "kpi_id": "LIQ-KPI-003",
                "regulation_id": "LIQ-UNINSURED-001",
                "parent_requirement_id": "LIQ-UNINSURED-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2023-13",
                "kpi_name": "Uninsured Deposit Concentration",
                "description": "Uninsured deposits as percentage of total deposits — supervisory reference alert > 50%",
                "threshold": 50.0,
                "threshold_type": "alert_threshold_percent",
                "unit": "percent",
            },
            {
                "kpi_id": "LIQ-KPI-004",
                "regulation_id": "LIQ-COLLATERAL-001",
                "parent_requirement_id": "LIQ-GOVERNANCE-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Comptroller's Handbook — Liquidity Section IV",
                "kpi_name": "Unencumbered Collateral Coverage",
                "description": "Unencumbered HQLA as percentage of total assets — monitored daily",
                "threshold": 5.0,
                "threshold_type": "minimum_percent",
                "unit": "percent",
            },
        ]

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-LIQ-001",
                "regulation_id": "LIQ-CFP-001",
                "parent_requirement_id": "LIQ-CFP-001-R1",
                "regulator": "FDIC",
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement 2010 — Section IV.B",
                "scenario_name": "CFP documents all three supervisory reference stress scenarios",
                "priority": "critical",
                "given": "Bank has a written Contingency Funding Plan",
                "when_step": "FDIC examiner requests CFP documentation",
                "then_step": "CFP contains institution-specific, market-wide, and combined stress "
                             "scenarios with cash flow projections and funding sources identified for each",
            },
            {
                "scenario_id": "TC-LIQ-002",
                "regulation_id": "LIQ-UNINSURED-001",
                "parent_requirement_id": "LIQ-UNINSURED-001-R1",
                "regulator": "FDIC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2023-13",
                "scenario_name": "Enhanced monitoring triggers at 50% uninsured deposit concentration",
                "priority": "critical",
                "given": "Bank's uninsured deposit ratio is monitored monthly",
                "when_step": "Uninsured deposits rise above the 50% supervisory reference",
                "then_step": "System automatically escalates monitoring to daily frequency and "
                             "generates ALCO alert within one business day",
            },
            {
                "scenario_id": "TC-LIQ-003",
                "regulation_id": "LIQ-BUFFER-001",
                "parent_requirement_id": "LIQ-BUFFER-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Liquidity Handbook Section III",
                "scenario_name": "30-day HQLA buffer covers net outflows under stress",
                "priority": "high",
                "given": "Bank models 30-day liquidity stress",
                "when_step": "Treasury calculates net cash outflows under moderate stress scenario",
                "then_step": "HQLA buffer exceeds projected 30-day net outflows with documented "
                             "calculation methodology",
            },
            {
                "scenario_id": "TC-LIQ-004",
                "regulation_id": "LIQ-GOVERNANCE-001",
                "parent_requirement_id": "LIQ-GOVERNANCE-001-R1",
                "regulator": "FRB",
                "authority_type": "GUIDANCE",
                "authority_source": "Interagency Policy Statement 2010 — Section III.A",
                "scenario_name": "Board liquidity risk tolerance reviewed annually",
                "priority": "high",
                "given": "Board holds quarterly meetings",
                "when_step": "Examiner reviews board minutes for current year",
                "then_step": "Minutes document formal approval of liquidity risk tolerance "
                             "statement with specific quantitative limits",
            },
        ]
