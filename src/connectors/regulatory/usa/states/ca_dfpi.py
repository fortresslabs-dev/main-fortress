"""
CA DFPI Connector — California Department of Financial Protection and Innovation.
Covers California Financing Law, CCPA/CPRA financial data obligations,
Digital Financial Assets Law, and CA Rosenthal Act.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector
from src.connectors.regulatory.usa._scraper_utils import stamp_state_metadata

logger = structlog.get_logger(__name__)

_STATE_CODE = "CA"
_REGULATOR = "CA-DFPI"

_DEFAULT_URLS = {
    "dfpi_main": "https://dfpi.ca.gov/",
    "licensing": "https://dfpi.ca.gov/consumers-investors/industry-guidance/licensing/",
    "enforcement": "https://dfpi.ca.gov/newsroom/enforcement-actions/",
    "cfl_guidance": "https://dfpi.ca.gov/consumers-investors/loans/california-financing-law/",
    "ccpa_guidance": "https://cppa.ca.gov/regulations/",
    "dfal_guidance": "https://dfpi.ca.gov/digital-financial-assets/",
    "bulletins": "https://dfpi.ca.gov/industry-guidance/bulletins-letters/",
}


class CaDfpiConnector(RegulatoryConnector):
    """CA DFPI regulatory connector.

    Covers:
      - California Financing Law (CFL) — lending license requirements
      - CCPA/CPRA — consumer financial data privacy obligations
      - Digital Financial Assets Law (DFAL) — crypto/stablecoin licensing
      - Rosenthal Fair Debt Collection Practices Act (CA)
      - DFPI examination and enforcement actions
    """

    connector_name = "ca_dfpi"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("ca_dfpi_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("ca_dfpi_extraction_started")
        regulations = stamp_state_metadata(self._build_regulations(), _REGULATOR, _STATE_CODE)
        requirements = stamp_state_metadata(self._build_requirements(), _REGULATOR, _STATE_CODE)
        kpis = stamp_state_metadata(self._build_kpis(), _REGULATOR, _STATE_CODE)
        test_scenarios = stamp_state_metadata(self._build_test_scenarios(), _REGULATOR, _STATE_CODE)

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
            logger.warning("ca_dfpi_live_scrape_failed", error=str(exc))

        logger.info(
            "ca_dfpi_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
        )
        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            enrich_document,
            make_session,
            scrape_html_page,
        )

        session = make_session(self.urls["dfpi_main"])
        docs: List[Dict] = []

        for url, category in [
            (self.urls["dfpi_main"], "CA DFPI Overview"),
            (self.urls["cfl_guidance"], "California Financing Law"),
            (self.urls["bulletins"], "DFPI Bulletins"),
            (self.urls["enforcement"], "DFPI Enforcement"),
        ]:
            docs.extend(scrape_html_page(session, url, source="CA-DFPI", category=category))

        for doc in docs:
            enrich_document(doc, regulation_id="CA-DFPI", state_code=_STATE_CODE, doc_type="reg_text")

        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "CA-CFL-001",
                "regulation_name": "California Financing Law — Licensed Lender Requirements",
                "category": "Consumer Lending",
                "subcategory": "State Licensing",
                "regulator": "CA-DFPI",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — prior 2020-01-01 was page-publish, not rule-effective.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["cfl_guidance"],
                "last_updated": now,
            },
            {
                "regulation_id": "CA-CCPA-001",
                "regulation_name": "CCPA/CPRA — Consumer Financial Data Privacy",
                "category": "Data Privacy",
                "subcategory": "Consumer Rights",
                "regulator": "CA-DFPI",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — 2023-01-01 was page-publish. Also per §9: CCPA is
                # enforced by CPPA / AG (not DFPI), and GLBA-covered data is
                # largely exempt — scope narrower than the corpus implies.
                "verify_before_display": True,
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["ccpa_guidance"],
                "last_updated": now,
            },
            {
                "regulation_id": "CA-DFAL-001",
                "regulation_name": "California Digital Financial Assets Law (DFAL)",
                "category": "Digital Assets",
                "subcategory": "Crypto Licensing",
                "regulator": "CA-DFPI",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — 2025-07-01 was page-publish (also, DFAL enforcement
                # was delayed to 2026-07-01 by AB 1934). Null until a real
                # effective date is sourced.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["dfal_guidance"],
                "last_updated": now,
            },
        ]

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "CA-CFL-LIC-001",
                "regulation_id": "CA-CFL-001",
                "requirement_text": (
                    "Any person or entity engaged in the business of making consumer loans "
                    "in California must obtain a California Financing Law license from DFPI. "
                    "License renewal required annually by December 31."
                ),
                "rule_type": "licensing",
                "cfr_citation": "Cal. Fin. Code § 22100",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CFL license active; renewal filed before December 31",
                "parameters": {"renewal_deadline": "December 31", "regulator": "CA-DFPI"},
            },
            {
                "requirement_id": "CA-CCPA-OPT-OUT-001",
                "regulation_id": "CA-CCPA-001",
                "requirement_text": (
                    "Financial institutions subject to CCPA must honor consumer opt-out "
                    "requests for sale or sharing of personal information within 15 business days. "
                    "Opt-out must persist for 12 months before re-authorization request permitted."
                ),
                "rule_type": "consumer_rights",
                "cfr_citation": "Cal. Civ. Code § 1798.120",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify opt-out requests processed within 15 business days; 12-month persistence enforced",
                "parameters": {
                    "opt_out_response_days": 15,
                    "opt_out_persistence_months": 12,
                },
            },
            {
                "requirement_id": "CA-DFAL-LIC-001",
                "regulation_id": "CA-DFAL-001",
                "requirement_text": (
                    "Any person engaging in digital financial asset business activity "
                    "in California or with California residents must obtain a DFPI license "
                    "under the Digital Financial Assets Law. Original effective date was "
                    "July 1, 2025; enforcement was delayed to July 1, 2026 by AB 1934."
                ),
                "rule_type": "licensing",
                "authority_type": "STATUTE",
                "cfr_citation": "Cal. Fin. Code § 3200",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "verify_before_display": True,
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify DFAL license obtained before offering digital asset services to CA residents",
                # C-13 — DFAL enforcement was delayed to 2026-07-01 by AB 1934.
                "parameters": {"enforcement_date": "2026-07-01", "applies_to": "digital_financial_asset_businesses"},
            },
        ]

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited citation on every KPI.
        # CCPA is enforced by CPPA/AG (not DFPI); its authority is verified but
        # the applicability scope carries verify_before_display on the parent.
        return [
            {
                "kpi_id": "CA-DFPI-KPI-001",
                "regulation_id": "CA-CFL-001",
                "parent_requirement_id": "CA-CFL-LIC-001",
                "authority_type": "STATUTE",
                "citation": "Cal. Fin. Code § 22100",
                "kpi_name": "CFL License Renewal Timeliness",
                "kpi_formula": "License renewed before December 31: 1=Yes, 0=No",
                "target_value": 1,
                "target_unit": "boolean",
                "monitoring_frequency": "annual",
                "alert_threshold": 1,
                "criticality": "critical",
            },
            {
                "kpi_id": "CA-DFPI-KPI-002",
                "regulation_id": "CA-CCPA-001",
                "parent_requirement_id": "CA-CCPA-OPT-OUT-001",
                "authority_type": "STATUTE",
                "citation": "Cal. Civ. Code § 1798.120",
                "kpi_name": "CCPA Opt-Out Request Response Time",
                "kpi_formula": "Avg business days to process opt-out requests (target ≤15)",
                "target_value": 15.0,
                "target_unit": "business_days",
                "monitoring_frequency": "monthly",
                "alert_threshold": 12.0,
                "criticality": "high",
            },
        ]

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-CA-001",
                "regulation_id": "CA-CFL-001",
                "parent_requirement_id": "CA-CFL-LIC-001",
                "authority_type": "STATUTE",
                "citation": "Cal. Fin. Code § 22100",
                "scenario_name": "Lending activity without CFL license",
                "priority": "critical",
                "given": "Fintech company begins offering consumer loans to CA residents",
                "when_step": "First CA consumer loan originated without CFL license",
                "then_step": "Compliance system flags: 'CFL license required; unlicensed lending in California is a criminal violation'",
            },
            {
                "scenario_id": "TC-CA-002",
                "regulation_id": "CA-CCPA-001",
                "parent_requirement_id": "CA-CCPA-OPT-OUT-001",
                "authority_type": "STATUTE",
                "citation": "Cal. Civ. Code § 1798.120",
                "scenario_name": "CCPA opt-out not processed within 15 days",
                "priority": "high",
                "given": "Consumer submits opt-out request on March 1",
                "when_step": "March 17 — opt-out not yet processed (day 16)",
                "then_step": "Alert: 'CCPA opt-out SLA breach: 16 days elapsed vs 15-day limit'; escalated to privacy officer",
            },
        ]
