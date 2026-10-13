"""
IRR Connector — Interest Rate Risk Program (REQ-004)
Covers OCC Bulletin 2010-1, FFIEC IRR Advisory, FRB SR 96-13.

High priority — critical for any asset-sensitive bank in rising-rate environment.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "occ_irr_bulletin": "https://www.occ.gov/news-issuances/bulletins/2010/bulletin-2010-1.html",
    "occ_irr_handbook": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/interest-rate-risk/pub-ch-interest-rate-risk.pdf",
    "ffiec_irr_advisory": "https://www.ffiec.gov/press/pr010614.htm",
    "fdic_irr_guidance": "https://www.fdic.gov/capital-markets/interest-rate-risk",
    "frb_sr96_13": "https://www.federalreserve.gov/boarddocs/srletters/1996/SR9613.htm",
    "occ_irr_page": "https://www.occ.gov/topics/supervision-and-examination/capital-and-liquidity/interest-rate-risk/index-interest-rate-risk.html",
}


class IrrConnector(RegulatoryConnector):
    """Interest Rate Risk (IRR) regulatory connector.

    Covers:
      - EVE (Economic Value of Equity) shock scenarios (±100/200/300/400 bps)
      - NII (Net Interest Income) sensitivity analysis
      - IRR policy and board oversight requirements
      - Stress testing under adverse rate environments
      - ALCO (Asset/Liability Committee) governance
      - Duration gap and repricing gap analysis
    """

    connector_name = "irr"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("irr_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("irr_extraction_started")
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
            logger.warning("irr_live_scrape_failed", error=str(exc))

        logger.info(
            "irr_extraction_completed",
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
            (self.urls["occ_irr_bulletin"], "OCC", "IRR Bulletin 2010-1"),
            (self.urls["occ_irr_page"], "OCC", "IRR Supervision"),
            (self.urls["fdic_irr_guidance"], "FDIC", "IRR Guidance"),
        ]:
            docs.extend(scrape_html_page(session, url, source=source, category=category))

        docs.extend(
            scrape_pdf_url(
                session, self.urls["occ_irr_handbook"],
                source="OCC", category="IRR Comptrollers Handbook",
            )
        )

        for doc in docs:
            enrich_document(doc, regulation_id="IRR", doc_type="guidance")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        # Both rows are GUIDANCE, not law. The enforceable safety-and-
        # soundness hook lives at 12 CFR 30 App A (OCC), but that section
        # does NOT prescribe ±100–400 bps shock scenarios or a -20% EVE
        # limit; those figures are supervisory reference points.
        return [
            {
                "regulation_id": "IRR-OCC-001",
                "regulation_name": "OCC Bulletin 2010-1 — Interest Rate Risk Guidance",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 (Interest Rate Risk); enforceable hook: 12 C.F.R. Part 30 App A (safety-and-soundness)",
                "category": "Interest Rate Risk",
                "subcategory": "IRR Management",
                "regulator": "OCC",
                "geography": "usa",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_irr_bulletin"],
                "last_updated": now,
            },
            {
                "regulation_id": "IRR-FFIEC-001",
                "regulation_name": "FFIEC Advisory on Interest Rate Risk Management (2010)",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "2010 FFIEC Advisory on Interest Rate Risk Management",
                "category": "Interest Rate Risk",
                "subcategory": "Supervisory Advisory",
                "regulator": "FFIEC",
                "geography": "usa",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["ffiec_irr_advisory"],
                "last_updated": now,
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        # All rows are GUIDANCE. The ±100-400bps shocks and the "-20% EVE"
        # figure are supervisory reference scenarios, not codified limits.
        return [
            {
                "requirement_id": "IRR-REQ-POLICY-001",
                "regulation_id": "IRR-OCC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1; enforceable safety-and-soundness hook 12 C.F.R. Part 30 App A",
                "requirement_text": (
                    "Board of Directors is expected to approve a written IRR policy that "
                    "establishes risk limits (EVE and NII), measurement approaches, and "
                    "reporting requirements. Policy should be reviewed at least annually. "
                    "Supervisory expectation, not codified in a CFR section."
                ),
                "rule_type": "governance",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify board-approved IRR policy exists; contains EVE/NII limits; reviewed within 12 months",
                "parameters": {
                    "policy_elements": ["risk_limits", "measurement_approaches", "reporting_requirements"],
                    "board_approval_required": True,
                    "review_frequency": "annual",
                },
            },
            {
                "requirement_id": "IRR-REQ-EVE-001",
                "regulation_id": "IRR-OCC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — supervisory reference scenarios",
                "requirement_text": (
                    "Institutions typically measure Economic Value of Equity (EVE) sensitivity "
                    "under reference rate shock scenarios of ±100, ±200, ±300, ±400 basis points. "
                    "EVE decline in the neighbourhood of 20% of capital under a +200 bps shock "
                    "is a supervisory reference point that triggers heightened scrutiny — it is "
                    "not a codified regulatory limit."
                ),
                "rule_type": "stress_test",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify EVE computed for all 8 rate shock scenarios; alert when EVE decline >20% capital",
                "parameters": {
                    "rate_shocks_bps": [-400, -300, -200, -100, 100, 200, 300, 400],
                    "eve_decline_alert_pct": 20.0,
                    "measurement_frequency": "quarterly",
                },
            },
            {
                "requirement_id": "IRR-REQ-NII-001",
                "regulation_id": "IRR-OCC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — NII sensitivity reference",
                "requirement_text": (
                    "Net Interest Income (NII) sensitivity is typically measured and reported "
                    "quarterly. Supervisory reference: NII projections for ±100 and ±200 bps "
                    "scenarios over a 12-month horizon. Significant NII sensitivity should be "
                    "disclosed in ALCO reports to the board."
                ),
                "rule_type": "stress_test",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify NII sensitivity report generated quarterly; board ALCO reporting documented",
                "parameters": {
                    "rate_shocks_bps": [-200, -100, 100, 200],
                    "projection_horizon_months": 12,
                    "reporting_frequency": "quarterly",
                    "board_reporting": "ALCO",
                },
            },
            {
                "requirement_id": "IRR-REQ-ALCO-001",
                "regulation_id": "IRR-OCC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — ALCO governance",
                "requirement_text": (
                    "Asset/Liability Committee (ALCO) is expected to meet at least quarterly "
                    "to review IRR position, rate sensitivity metrics, and compliance with "
                    "board-approved risk limits. Meeting minutes should be maintained."
                ),
                "rule_type": "governance",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify ALCO meeting frequency ≥ quarterly; minutes document limit compliance review",
                "parameters": {
                    "meeting_frequency": "quarterly",
                    "minimum_meetings_per_year": 4,
                    "documentation_required": ["meeting_minutes", "irr_position_report"],
                },
            },
            {
                "requirement_id": "IRR-REQ-MODEL-001",
                "regulation_id": "IRR-OCC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 (Model Risk Management) cross-reference; OCC Bulletin 2010-1",
                "requirement_text": (
                    "IRR models used for EVE and NII analysis should undergo independent "
                    "model validation on a supervisory reference cadence (typically every 3 "
                    "years or on material model change). Validation typically assesses "
                    "conceptual soundness, data integrity, and assumptions."
                ),
                "rule_type": "model_validation",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify IRR model validation completed within 3 years; validation report on file",
                "parameters": {
                    "validation_frequency_years": 3,
                    "trigger": "material_model_change",
                    "validation_components": ["conceptual_soundness", "data_integrity", "assumptions"],
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — KPIs carry parent_requirement_id + authority_type +
        # authority_source. All GUIDANCE (see §8) — citation intentionally
        # omitted (the §11.3 hard gate rejects GUIDANCE with a CFR cite).
        return [
            {
                "kpi_id": "IRR-KPI-001",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-EVE-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — supervisory reference scenarios",
                "kpi_name": "EVE Sensitivity (±200 bps shock)",
                "kpi_formula": "(EVE_shocked - EVE_base) / EVE_base * 100 (supervisory reference: -20%)",
                "target_value": -20.0,
                "target_unit": "percent_change",
                "monitoring_frequency": "quarterly",
                "alert_threshold": -15.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "IRR-KPI-002",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-NII-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — NII sensitivity reference",
                "kpi_name": "NII Sensitivity (12-month, +200 bps)",
                "kpi_formula": "(NII_plus200 - NII_base) / NII_base * 100",
                "target_value": None,
                "target_unit": "percent_change",
                "monitoring_frequency": "quarterly",
                "alert_threshold": None,
                "criticality": "high",
            },
            {
                "kpi_id": "IRR-KPI-003",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-ALCO-001",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — ALCO governance",
                "kpi_name": "ALCO Meeting Frequency",
                "kpi_formula": "Count of ALCO meetings in trailing 12 months (supervisory reference: ≥ 4)",
                "target_value": 4.0,
                "target_unit": "count",
                "monitoring_frequency": "annual",
                "alert_threshold": 4.0,
                "criticality": "medium",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # C-11/C-12 — regulator=OCC (parent of IRR-OCC-001), authority_type=
        # GUIDANCE, authority_source for chain-back. TC docstrings must not
        # cite a CFR section — the §11.3 hard gate re-routes to authority_source.
        return [
            {
                "scenario_id": "TC-IRR-001",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-EVE-001",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — supervisory reference scenarios",
                "scenario_name": "EVE declines 25% under +200 bps shock — breach of internal limit",
                "priority": "critical",
                "given": "Bank's IRR model shows base EVE of $100M; board policy limit set at -20%",
                "when_step": "+200 bps rate shock applied to asset/liability model",
                "then_step": "EVE declines to $75M (-25%); system alerts 'Internal policy limit breach: -25% exceeds -20% reference'; ALCO notified",
            },
            {
                "scenario_id": "TC-IRR-002",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-ALCO-001",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1 — ALCO governance",
                "scenario_name": "ALCO meeting skipped — compliance gap after 5 months",
                "priority": "high",
                "given": "Last ALCO meeting held 5 months ago",
                "when_step": "Month 5 without a scheduled ALCO meeting",
                "then_step": "System flags: 'ALCO meeting overdue; last meeting 5 months ago; policy requires quarterly'",
            },
            {
                "scenario_id": "TC-IRR-003",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-MODEL-001",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 (Model Risk Management)",
                "scenario_name": "IRR model not validated in 4 years",
                "priority": "high",
                "given": "IRR model last validated January 2022",
                "when_step": "February 2026 — 4 years elapsed",
                "then_step": "Compliance dashboard flags: 'IRR model validation overdue by 1 year'; exam finding risk elevated",
            },
            {
                "scenario_id": "TC-IRR-004",
                "regulation_id": "IRR-OCC-001",
                "parent_requirement_id": "IRR-REQ-POLICY-001",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2010-1",
                "scenario_name": "Board IRR policy not reviewed — 14 months elapsed",
                "priority": "high",
                "given": "IRR policy last board-approved 14 months ago",
                "when_step": "Annual review not scheduled",
                "then_step": "System generates: 'IRR policy annual review overdue'; board agenda item created",
            },
        ]
