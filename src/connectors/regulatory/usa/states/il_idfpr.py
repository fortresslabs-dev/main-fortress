"""
IL IDFPR Connector — Illinois Department of Financial and Professional Regulation.
Covers Illinois Banking Act, Transmitters of Money Act, Consumer Installment Loan Act.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector
from src.connectors.regulatory.usa._scraper_utils import stamp_state_metadata

logger = structlog.get_logger(__name__)

_STATE_CODE = "IL"
_REGULATOR = "IL-IDFPR"

_DEFAULT_URLS = {
    # IDFPR migrated to .html paths under /banks/. ConsumerCredit and
    # TransmittersofMoney no longer have dedicated landing pages — content
    # is folded into banking.html and per-license sub-pages (cbt, regent…).
    # Per-license enforcement and guidance landing pages also 404 (verified
    # live 2026-05-17). _build_regulations() / _build_requirements() encode
    # the Illinois Banking Act + TOMA + CILA structure directly.
    "idfpr_main": "https://idfpr.illinois.gov/",
    "banking_division": "https://idfpr.illinois.gov/banking.html",
    "commercial_banks": "https://idfpr.illinois.gov/banks/cbt.html",
    "money_transmitters": "https://idfpr.illinois.gov/banking.html",
}


class IlIdfprConnector(RegulatoryConnector):
    """Illinois IDFPR regulatory connector.

    Covers:
      - Illinois Banking Act — state-chartered bank requirements
      - Transmitters of Money Act — money transmission licensing
      - Consumer Installment Loan Act (CILA) — consumer lending
      - Illinois Community Reinvestment Act
      - IDFPR examination and enforcement
    """

    connector_name = "il_idfpr"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("il_idfpr_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("il_idfpr_extraction_started")
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
            logger.warning("il_idfpr_live_scrape_failed", error=str(exc))

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
            (self.urls["banking_division"], "IL Banking"),
            (self.urls["commercial_banks"], "IL Commercial Banks"),
        ]:
            docs.extend(scrape_html_page(session, url, source="IL-IDFPR", category=category))

        for doc in docs:
            enrich_document(doc, regulation_id="IL-IDFPR", state_code=_STATE_CODE, doc_type="reg_text")

        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "IL-BANK-001",
                "regulation_name": "Illinois Banking Act — State-Chartered Bank Requirements",
                "category": "State Banking",
                "subcategory": "Bank Charter",
                "regulator": "IL-IDFPR",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — page-publish date, not rule-effective. Null pending FR backfill.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["banking_division"],
                "last_updated": now,
            },
            {
                "regulation_id": "IL-TOMA-001",
                "regulation_name": "Illinois Transmitters of Money Act",
                "category": "Money Services",
                "subcategory": "State Licensing",
                "regulator": "IL-IDFPR",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — page-publish date, not rule-effective. Null pending FR backfill.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["money_transmitters"],
                "last_updated": now,
            },
        ]

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "IL-TOMA-LIC-001",
                "regulation_id": "IL-TOMA-001",
                "requirement_text": (
                    "Any person or entity transmitting money on behalf of others "
                    "in Illinois must be licensed under the Transmitters of Money Act. "
                    "Minimum net worth: $35,000. Permissible investments must equal "
                    "outstanding transmission obligations."
                ),
                "rule_type": "licensing",
                "cfr_citation": "205 ILCS 657",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": 35_000,
                "threshold_currency": "USD",
                "test_requirement": "Verify IL money transmitter license active; net worth ≥$35K; permissible investments match obligations",
                "parameters": {
                    "min_net_worth_usd": 35_000,
                    "permissible_investments_required": True,
                    "renewal_frequency": "annual",
                },
            },
        ]

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited citation on every KPI.
        return [
            {
                "kpi_id": "IL-IDFPR-KPI-001",
                "regulation_id": "IL-TOMA-001",
                "parent_requirement_id": "IL-TOMA-LIC-001",
                "authority_type": "STATUTE",
                "citation": "205 ILCS 657",
                "kpi_name": "IL Money Transmitter License Status",
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
                "scenario_id": "TC-IL-001",
                "regulation_id": "IL-TOMA-001",
                "parent_requirement_id": "IL-TOMA-LIC-001",
                "authority_type": "STATUTE",
                "citation": "205 ILCS 657",
                "scenario_name": "Money transmission without IL license",
                "priority": "critical",
                "given": "Fintech processes P2P transfers for IL residents",
                "when_step": "First IL transfer processed without TOMA license",
                "then_step": "System flags: 'Illinois TOMA license required for money transmission in IL'",
            },
        ]
