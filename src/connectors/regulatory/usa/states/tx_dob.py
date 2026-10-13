"""
TX DOB Connector — Texas Department of Banking.
Covers Texas Finance Code, TX Money Services Act, TX consumer lending requirements.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector
from src.connectors.regulatory.usa._scraper_utils import stamp_state_metadata

logger = structlog.get_logger(__name__)

_STATE_CODE = "TX"
_REGULATOR = "TX-DOB"

_DEFAULT_URLS = {
    "dob_main": "https://www.dob.texas.gov/",
    "bank_supervision": "https://www.dob.texas.gov/industry/banks/",
    "money_services": "https://www.dob.texas.gov/industry/money-services-businesses/",
    "consumer_lending": "https://www.dob.texas.gov/industry/consumer-lenders/",
    "enforcement": "https://www.dob.texas.gov/about/news/enforcement-actions/",
    "guidance": "https://www.dob.texas.gov/industry/banks/guidance/",
}


class TxDobConnector(RegulatoryConnector):
    """Texas Department of Banking regulatory connector.

    Covers:
      - Texas state-chartered bank supervision
      - Texas Money Services Act (MSA) — money transmission licensing
      - Texas Consumer Lending Act
      - TX-specific BSA/AML requirements for state-chartered banks
      - DOB examination policies and procedures
    """

    connector_name = "tx_dob"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("tx_dob_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("tx_dob_extraction_started")
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
            logger.warning("tx_dob_live_scrape_failed", error=str(exc))

        logger.info(
            "tx_dob_extraction_completed",
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

        session = make_session(self.urls["dob_main"])
        docs: List[Dict] = []

        for url, category in [
            (self.urls["bank_supervision"], "TX Bank Supervision"),
            (self.urls["money_services"], "TX Money Services"),
            (self.urls["consumer_lending"], "TX Consumer Lending"),
            (self.urls["guidance"], "TX DOB Guidance"),
        ]:
            docs.extend(scrape_html_page(session, url, source="TX-DOB", category=category))

        for doc in docs:
            enrich_document(doc, regulation_id="TX-DOB", state_code=_STATE_CODE, doc_type="reg_text")

        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "TX-BANK-001",
                "regulation_name": "Texas Finance Code — State-Chartered Bank Requirements",
                "category": "State Banking",
                "subcategory": "Bank Charter",
                "regulator": "TX-DOB",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — page-publish date, not rule-effective. Null pending FR backfill.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["bank_supervision"],
                "last_updated": now,
            },
            {
                "regulation_id": "TX-MSA-001",
                "regulation_name": "Texas Money Services Act — Money Transmission Licensing",
                "category": "Money Services",
                "subcategory": "State Licensing",
                "regulator": "TX-DOB",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-13 — page-publish date, not rule-effective. Null pending FR backfill.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["money_services"],
                "last_updated": now,
            },
        ]

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "TX-MSA-LIC-001",
                "regulation_id": "TX-MSA-001",
                "requirement_text": (
                    "Any entity engaging in money transmission business in Texas "
                    "must obtain a license from the Texas Department of Banking. "
                    "Net worth requirement: minimum $100,000 for initial license. "
                    "Annual renewal required; surety bond or permissible investments required."
                ),
                "rule_type": "licensing",
                "cfr_citation": "TX Finance Code § 151.301",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": 100_000,
                "threshold_currency": "USD",
                "test_requirement": "Verify money transmission license active; net worth ≥$100K; surety bond in place",
                "parameters": {
                    "min_net_worth_usd": 100_000,
                    "surety_bond_required": True,
                    "renewal_frequency": "annual",
                },
            },
            {
                "requirement_id": "TX-BANK-EXAM-001",
                "regulation_id": "TX-BANK-001",
                "requirement_text": (
                    "Texas state-chartered banks are subject to examination by the "
                    "Texas Department of Banking at least once every 18 months "
                    "(or more frequently for problem banks). "
                    "Exam results shared with primary federal regulator."
                ),
                "rule_type": "examination",
                "cfr_citation": "TX Finance Code § 31.102",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify exam preparation materials current; last exam within 18 months",
                "parameters": {"max_exam_interval_months": 18, "coordinated_with": "federal_regulator"},
            },
        ]

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited citation on every KPI.
        return [
            {
                "kpi_id": "TX-DOB-KPI-001",
                "regulation_id": "TX-MSA-001",
                "parent_requirement_id": "TX-MSA-LIC-001",
                "authority_type": "STATUTE",
                "citation": "TX Finance Code § 151.301",
                "kpi_name": "TX Money Services License Status",
                "kpi_formula": "License active and not expired: 1=Yes, 0=No",
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
                "scenario_id": "TC-TX-001",
                "regulation_id": "TX-MSA-001",
                "parent_requirement_id": "TX-MSA-LIC-001",
                "authority_type": "STATUTE",
                "citation": "TX Finance Code § 151.301",
                "scenario_name": "Money transmission without TX license",
                "priority": "critical",
                "given": "Fintech begins processing remittances for TX residents",
                "when_step": "First TX remittance processed without DOB money services license",
                "then_step": "System flags: 'TX Money Services Act license required; cease TX operations until licensed'",
            },
        ]
