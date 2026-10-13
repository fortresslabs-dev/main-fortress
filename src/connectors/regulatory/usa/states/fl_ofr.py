"""
FL OFR Connector — Florida Office of Financial Regulation.
Covers Florida Banking Code, FL Money Transmitters Act, FL consumer protection.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector
from src.connectors.regulatory.usa._scraper_utils import stamp_state_metadata

logger = structlog.get_logger(__name__)

_STATE_CODE = "FL"
_REGULATOR = "FL-OFR"

_DEFAULT_URLS = {
    # FL OFR moved from /sitePages/*.htm to /divisions-offices/* and /regulation/*.
    # MSB now lives inside Division of Consumer Finance (no standalone page).
    "ofr_main": "https://www.flofr.gov/",
    "banking": "https://flofr.gov/divisions-offices/division-of-financial-institutions",
    "money_services": "https://flofr.gov/divisions-offices/division-of-consumer-finance",
    "consumer_finance": "https://flofr.gov/divisions-offices/division-of-consumer-finance",
    "enforcement": "https://flofr.gov/enforcement/final-administrative-actions",
    "guidance": "https://flofr.gov/regulation/industry-alerts",
}


class FlOfrConnector(RegulatoryConnector):
    """Florida OFR regulatory connector.

    Covers:
      - Florida Banking Code (Chapter 655) — state bank requirements
      - Florida Money Transmitters Act (Chapter 560) — MSB licensing
      - Florida Consumer Finance Act — small loan licensing
      - FL Office of Financial Regulation examination procedures
    """

    connector_name = "fl_ofr"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("fl_ofr_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("fl_ofr_extraction_started")
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
            logger.warning("fl_ofr_live_scrape_failed", error=str(exc))

        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            enrich_document,
            make_session,
            scrape_html_page,
        )

        session = make_session()
        docs: List[Dict] = []

        for url, category in [
            (self.urls["banking"], "FL Banking"),
            (self.urls["money_services"], "FL Money Services"),
            (self.urls["consumer_finance"], "FL Consumer Finance"),
        ]:
            docs.extend(scrape_html_page(session, url, source="FL-OFR", category=category))

        for doc in docs:
            enrich_document(doc, regulation_id="FL-OFR", state_code=_STATE_CODE, doc_type="reg_text")

        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "FL-BANK-001",
                "regulation_name": "Florida Banking Code (Chapter 655) — State Bank Requirements",
                "category": "State Banking",
                "subcategory": "Bank Charter",
                "regulator": "FL-OFR",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — 2022-07-01 was page-publish, not rule-effective. Null pending FR backfill.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["banking"],
                "last_updated": now,
            },
            {
                "regulation_id": "FL-MSA-001",
                "regulation_name": "Florida Money Transmitters Act (Chapter 560)",
                "category": "Money Services",
                "subcategory": "State Licensing",
                "regulator": "FL-OFR",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — 2022-07-01 was page-publish, not rule-effective. Null pending FR backfill.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["money_services"],
                "last_updated": now,
            },
        ]

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "FL-MSA-LIC-001",
                "regulation_id": "FL-MSA-001",
                "requirement_text": (
                    "Any person engaged in money services business in Florida "
                    "must be licensed under Chapter 560. Minimum net worth: $100,000. "
                    "Surety bond required ranging from $10,000 to $2,000,000 based on volume. "
                    "Annual renewal by December 31."
                ),
                "rule_type": "licensing",
                "cfr_citation": "Fla. Stat. § 560.204",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": 100_000,
                "threshold_currency": "USD",
                "test_requirement": "Verify FL MSB license active; net worth ≥$100K; surety bond on file",
                "parameters": {
                    "min_net_worth_usd": 100_000,
                    "surety_bond_range_usd": [10_000, 2_000_000],
                    "renewal_deadline": "December 31",
                },
            },
        ]

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited citation on every KPI.
        return [
            {
                "kpi_id": "FL-OFR-KPI-001",
                "regulation_id": "FL-MSA-001",
                "parent_requirement_id": "FL-MSA-LIC-001",
                "authority_type": "STATUTE",
                "citation": "Fla. Stat. § 560.204",
                "kpi_name": "FL Money Services License Status",
                "kpi_formula": "License active: 1=Yes, 0=No",
                "target_value": 1,
                "target_unit": "boolean",
                "monitoring_frequency": "monthly",
                "alert_threshold": 1,
                "criticality": "critical",
            },
        ]

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-FL-001",
                "regulation_id": "FL-MSA-001",
                "parent_requirement_id": "FL-MSA-LIC-001",
                "authority_type": "STATUTE",
                "citation": "Fla. Stat. § 560.204",
                "scenario_name": "FL MSB operating without Chapter 560 license",
                "priority": "critical",
                "given": "Payment company begins processing FL transactions",
                "when_step": "First FL transaction processed without OFR license",
                "then_step": "System flags: 'FL Chapter 560 license required; unlicensed MSB activity is a felony'",
            },
        ]
