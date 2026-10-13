"""
NYDFS Connector — New York Department of Financial Services (REQ-014).
Covers NY Banking Law, Part 504 BSA/AML, Part 500 Cybersecurity Regulation.

NY is the most extensive state banking regulator; compliance is de-facto
national standard for any institution with NY operations.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector
from src.connectors.regulatory.usa._scraper_utils import stamp_state_metadata

logger = structlog.get_logger(__name__)

_STATE_CODE = "NY"
_REGULATOR = "NYDFS"

_DEFAULT_URLS = {
    # NYDFS migrated to underscore-style under /industry_guidance/. The
    # hyphen-style AML, virtual-currency, licensing, and enforcement pages
    # no longer have 1:1 replacements (verified live 2026-05-17) — they
    # have been removed here rather than left as 404 noise. _build_regulations()
    # + _build_requirements() encode Part 500 / 504 directly.
    "nydfs_main": "https://www.dfs.ny.gov/",
    "part_500_cyber": "https://www.dfs.ny.gov/industry_guidance/cybersecurity",
    "part_500_text": "https://www.dfs.ny.gov/docs/legal/regulations/adoptions/dfsrf500txt.pdf",
    "part_504_bsa": "https://www.dfs.ny.gov/industry_guidance/banking_div_industry_letters",
    "circulars": "https://www.dfs.ny.gov/industry_guidance/circular_letters",
    "industry_letters": "https://www.dfs.ny.gov/industry_guidance/industry_letters",
}


class NydfsConnector(RegulatoryConnector):
    """NYDFS state banking regulatory connector.

    Covers:
      - Part 500: Cybersecurity Requirements for Financial Services Companies
        (72-hour breach notification, CISO requirement, annual certification)
      - Part 504: BSA/AML Transaction Monitoring and Filtering Programs
        (annual board certification, annual validation)
      - NY Banking Law licensing requirements
      - NYDFS Consumer Protection regulations
    """

    connector_name = "nydfs"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("nydfs_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("nydfs_extraction_started")
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
            logger.warning("nydfs_live_scrape_failed", error=str(exc))

        logger.info(
            "nydfs_extraction_completed",
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

        session = make_session(self.urls["nydfs_main"])
        docs: List[Dict] = []

        for url, category in [
            (self.urls["nydfs_main"], "NY Banking"),
            (self.urls["part_500_cyber"], "Part 500 Cybersecurity"),
            (self.urls["circulars"], "NYDFS Circulars"),
            (self.urls["industry_letters"], "NYDFS Industry Letters"),
        ]:
            docs.extend(scrape_html_page(session, url, source="NYDFS", category=category))

        docs.extend(
            scrape_pdf_url(session, self.urls["part_500_text"], source="NYDFS", category="Part 500 Full Text")
        )

        for doc in docs:
            enrich_document(doc, regulation_id="NYDFS", state_code=_STATE_CODE, doc_type="reg_text")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "NYDFS-500-001",
                "regulation_name": "NYDFS Part 500 — Cybersecurity Requirements for Financial Services Companies",
                "category": "Cybersecurity",
                "subcategory": "State Cybersecurity",
                "regulator": "NYDFS",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "cfr_citation": "23 N.Y.C.R.R. Part 500",
                "authority_type": "LAW",
                # C-13 — 2017-03-01 is the *original* Part 500 effective date;
                # Part 500 was amended in 2023 with phased compliance dates
                # running into 2025 (MFA-everywhere, asset inventory, expanded
                # CISO reporting). Corpus still encodes the 2017 rule → flag
                # verify_before_display so UI surfaces the pending re-scrape.
                "verify_before_display": True,
                "effective_date": "2017-03-01",
                "exam_frequency": "annual",
                "source_url": self.urls["part_500_cyber"],
                "last_updated": now,
            },
            {
                "regulation_id": "NYDFS-504-001",
                "regulation_name": "NYDFS Part 504 — BSA/AML Transaction Monitoring and Filtering",
                "category": "BSA/AML",
                "subcategory": "State BSA",
                "regulator": "NYDFS",
                "geography": "usa",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                # C-cite — prior cite "3 NYCRR Part 116" was inconsistent
                # with the rule name (Part 504). NYDFS Transaction Monitoring
                # and Filtering lives at 3 NYCRR Part 504, not Part 116.
                "cfr_citation": "3 N.Y.C.R.R. Part 504",
                "authority_type": "LAW",
                # C-13 — 2017-01-01 is the rule's *effective* date; kept.
                "effective_date": "2017-01-01",
                "exam_frequency": "annual",
                "source_url": self.urls["part_504_bsa"],
                "last_updated": now,
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "NYDFS-500-CISO-001",
                "regulation_id": "NYDFS-500-001",
                "requirement_text": (
                    "Each Covered Entity must designate a qualified individual "
                    "to serve as Chief Information Security Officer (CISO). "
                    "CISO must report at least annually to the board on the "
                    "cybersecurity program and material risks (23 NYCRR § 500.4)."
                ),
                "rule_type": "governance",
                "cfr_citation": "23 N.Y.C.R.R. § 500.4",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CISO designated; annual board report on cybersecurity documented",
                "parameters": {
                    "ciso_required": True,
                    "board_report_frequency": "annual",
                    "report_contents": ["material_cybersecurity_risks", "program_assessment"],
                },
            },
            {
                "requirement_id": "NYDFS-500-BREACH-001",
                "regulation_id": "NYDFS-500-001",
                "requirement_text": (
                    "Covered Entity must notify NYDFS of a Cybersecurity Event "
                    "within 72 hours of determination that a reportable event has occurred. "
                    "Reportable events include unauthorized access to information systems "
                    "or nonpublic information (23 NYCRR § 500.17)."
                ),
                "rule_type": "breach_notification",
                "cfr_citation": "23 N.Y.C.R.R. § 500.17",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify breach notification workflow: NYDFS notified within 72 hours of determination",
                "parameters": {
                    "notification_hours": 72,
                    "notification_recipient": "NYDFS",
                    "reportable_triggers": ["unauthorized_access", "nonpublic_info_exposure"],
                },
            },
            {
                "requirement_id": "NYDFS-500-CERT-001",
                "regulation_id": "NYDFS-500-001",
                "requirement_text": (
                    "Each Covered Entity must submit an annual certification of compliance "
                    "with Part 500 to NYDFS by April 15 of each calendar year. "
                    "The certification must be signed by a Senior Officer or the CISO."
                ),
                "rule_type": "annual_certification",
                "cfr_citation": "23 N.Y.C.R.R. § 500.17(b)",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify Part 500 certification submitted to NYDFS by April 15",
                "parameters": {
                    "deadline": "April 15",
                    "signatory": ["Senior Officer", "CISO"],
                    "submission_portal": "NYDFS Portal",
                },
            },
            {
                "requirement_id": "NYDFS-504-CERT-001",
                "regulation_id": "NYDFS-504-001",
                "requirement_text": (
                    "Senior management must annually certify that the institution's "
                    "Transaction Monitoring and Filtering programs comply with Part 504 "
                    "requirements and are reasonably designed to detect suspicious activity. "
                    "Certification due by April 15."
                ),
                "rule_type": "annual_certification",
                "cfr_citation": "3 N.Y.C.R.R. § 504.4",
                "state_code": _STATE_CODE,
                "jurisdiction_level": "state",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify Part 504 annual certification filed by April 15; management signatory recorded",
                "parameters": {
                    "deadline": "April 15",
                    "covers": ["transaction_monitoring_program", "watchlist_filtering_program"],
                    "validation_required": "annual",
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited citation on every KPI.
        return [
            {
                "kpi_id": "NYDFS-KPI-001",
                "regulation_id": "NYDFS-500-001",
                "parent_requirement_id": "NYDFS-500-CERT-001",
                "authority_type": "LAW",
                "citation": "23 N.Y.C.R.R. § 500.17(b)",
                "kpi_name": "Part 500 Annual Certification Submission",
                "kpi_formula": "Certification submitted by April 15: 1=Yes, 0=No",
                "target_value": 1,
                "target_unit": "boolean",
                "monitoring_frequency": "annual",
                "alert_threshold": 1,
                "criticality": "critical",
            },
            {
                "kpi_id": "NYDFS-KPI-002",
                "regulation_id": "NYDFS-500-001",
                "parent_requirement_id": "NYDFS-500-BREACH-001",
                "authority_type": "LAW",
                "citation": "23 N.Y.C.R.R. § 500.17",
                "kpi_name": "Cybersecurity Breach Notification Timeliness",
                "kpi_formula": "Hours from determination to NYDFS notification (target ≤72)",
                "target_value": 72.0,
                "target_unit": "hours",
                "monitoring_frequency": "per_incident",
                "alert_threshold": 48.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "NYDFS-KPI-003",
                "regulation_id": "NYDFS-504-001",
                "parent_requirement_id": "NYDFS-504-CERT-001",
                "authority_type": "LAW",
                "citation": "3 N.Y.C.R.R. § 504.4",
                "kpi_name": "Part 504 Annual Certification Submission",
                "kpi_formula": "Part 504 certification submitted by April 15: 1=Yes, 0=No",
                "target_value": 1,
                "target_unit": "boolean",
                "monitoring_frequency": "annual",
                "alert_threshold": 1,
                "criticality": "critical",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-NYDFS-001",
                "regulation_id": "NYDFS-500-001",
                "parent_requirement_id": "NYDFS-500-BREACH-001",
                "authority_type": "LAW",
                "citation": "23 N.Y.C.R.R. § 500.17",
                "scenario_name": "Cyber breach notification sent 80 hours after determination",
                "priority": "critical",
                "given": "Ransomware attack discovered; determination made at T+0",
                "when_step": "NYDFS notification sent at T+80 hours",
                "then_step": "System flags: 'NYDFS Part 500 breach notification late by 8 hours'; CISO and legal alerted",
            },
            {
                "scenario_id": "TC-NYDFS-002",
                "regulation_id": "NYDFS-500-001",
                "parent_requirement_id": "NYDFS-500-CERT-001",
                "authority_type": "LAW",
                "citation": "23 N.Y.C.R.R. § 500.17(b)",
                "scenario_name": "Part 500 certification missed April 15 deadline",
                "priority": "critical",
                "given": "Part 500 annual certification due April 15",
                "when_step": "April 16 — certification not submitted",
                "then_step": "Compliance calendar alert: 'NYDFS Part 500 certification overdue'; enforcement risk elevated",
            },
            {
                "scenario_id": "TC-NYDFS-003",
                "regulation_id": "NYDFS-504-001",
                "parent_requirement_id": "NYDFS-504-CERT-001",
                "authority_type": "LAW",
                "citation": "3 N.Y.C.R.R. § 504.4",
                "scenario_name": "Part 504 TM program not validated annually",
                "priority": "high",
                "given": "Transaction monitoring program last validated 14 months ago",
                "when_step": "April 15 certification deadline approached",
                "then_step": "System blocks certification: 'TM program validation overdue; annual validation required before certification'",
            },
        ]
