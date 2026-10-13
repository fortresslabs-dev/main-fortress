"""
FFIEC Call Report Connector — REQ-002
Covers FFIEC 031 (state member banks), 041 (non-member state banks), 051 (small bank).

Critical — quarterly filing requirement for all FDIC-insured institutions.

LIVE SCRAPE STATUS — hardcoded-only for FFIEC + CDR hosts.
www.ffiec.gov and cdr.ffiec.gov are CF-gated (verified 2026-05-17). The
Wayback fallback in _fetch_html may return content for occasional snapshot
hits, but the form instruction PDFs and CDR portal are not reliably
archived. _build_regulations() / _build_requirements() encode the FFIEC
031/041/051 filing structure, schedules, and 30-day deadline directly.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "ffiec_forms": "https://www.ffiec.gov/nicpubweb/content/FFIEC_Forms_052_031.htm",
    "call_report_main": "https://www.ffiec.gov/nicpubweb/nicweb/SearchForm.aspx",
    "fdic_call_reports": "https://www.fdic.gov/bank/statistical/guide/2024/",
    "frb_y9c": "https://www.federalreserve.gov/apps/reportforms/moreinfo.aspx?pCode=FR_Y-9C",
    "ffiec_031_instructions": "https://www.ffiec.gov/PDF/FFIEC_forms/FFIEC031_20240630_i.pdf",
    "ffiec_041_instructions": "https://www.ffiec.gov/PDF/FFIEC_forms/FFIEC041_20240630_i.pdf",
    "ffiec_051_instructions": "https://www.ffiec.gov/PDF/FFIEC_forms/FFIEC051_20240630_i.pdf",
    "cdrh_submission": "https://cdr.ffiec.gov/public/",
    "fdic_statistics": "https://www.fdic.gov/bank/statistical/",
}


class FfiecCallReportConnector(RegulatoryConnector):
    """FFIEC Call Report regulatory connector.

    Covers:
      - FFIEC 031: State member banks with foreign offices
      - FFIEC 041: All other commercial banks and state savings associations
      - FFIEC 051: Eligible small institutions (assets < $5B)
      - Filing deadlines: 30 calendar days after quarter-end
      - Key schedules: RC (balance sheet), RI (income), RC-N (nonperforming)
      - XBRL submission via CDR portal
    """

    connector_name = "ffiec_call_report"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("ffiec_call_report_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("ffiec_call_report_extraction_started")
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
            logger.warning("ffiec_call_report_live_scrape_failed", error=str(exc))

        logger.info(
            "ffiec_call_report_extraction_completed",
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

        session = make_session(self.urls["ffiec_forms"])
        docs: List[Dict] = []

        for url, source, category in [
            (self.urls["ffiec_forms"], "FFIEC", "Call Report Forms"),
            (self.urls["fdic_statistics"], "FDIC", "Call Report Statistics"),
        ]:
            docs.extend(scrape_html_page(session, url, source=source, category=category))

        for pdf_key, source, category in [
            ("ffiec_031_instructions", "FFIEC", "FFIEC 031 Instructions"),
            ("ffiec_041_instructions", "FFIEC", "FFIEC 041 Instructions"),
            ("ffiec_051_instructions", "FFIEC", "FFIEC 051 Instructions"),
        ]:
            docs.extend(
                scrape_pdf_url(session, self.urls[pdf_key], source=source, category=category)
            )

        for doc in docs:
            enrich_document(doc, regulation_id="FFIEC-CALL-REPORT", doc_type="reg_text")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        # §9 audit — Part 304 alone is ambiguous (Subpart C is the 36-hour
        # Computer-Security Incident Notification rule, not Call Report).
        # Call Report is a Federal Financial Institutions Examination Council
        # instruction; the statutory hook is 12 U.S.C. § 1817(a) (Reports of
        # Condition), not a CFR section. Cite the statute; keep FFIEC form
        # references in authority_source. C-13 — 2024-06-30 was the *form*
        # version date, not a rule-effective date → null pending FR backfill.
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "FFIEC-CALL-031",
                "regulation_name": "FFIEC 031 — Call Report (State Member Banks with Foreign Offices)",
                "category": "Call Report",
                "subcategory": "Quarterly Filing",
                "regulator": "FFIEC",
                "geography": "usa",
                "authority_type": "STATUTE",
                "cfr_citation": "12 U.S.C. § 1817(a)",
                "authority_source": "FFIEC 031 Instructions",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": self.urls["ffiec_031_instructions"],
                "last_updated": now,
            },
            {
                "regulation_id": "FFIEC-CALL-041",
                "regulation_name": "FFIEC 041 — Call Report (Commercial Banks & State Savings Assoc.)",
                "category": "Call Report",
                "subcategory": "Quarterly Filing",
                "regulator": "FFIEC",
                "geography": "usa",
                "authority_type": "STATUTE",
                "cfr_citation": "12 U.S.C. § 1817(a)",
                "authority_source": "FFIEC 041 Instructions",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": self.urls["ffiec_041_instructions"],
                "last_updated": now,
            },
            {
                "regulation_id": "FFIEC-CALL-051",
                "regulation_name": "FFIEC 051 — Call Report (Eligible Small Institutions, assets <$5B)",
                "category": "Call Report",
                "subcategory": "Quarterly Filing",
                "regulator": "FFIEC",
                "geography": "usa",
                "authority_type": "STATUTE",
                "cfr_citation": "12 U.S.C. § 1817(a)",
                "authority_source": "FFIEC 051 Instructions",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": self.urls["ffiec_051_instructions"],
                "last_updated": now,
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        # §9 audit — the 30-day filing deadline derives from 12 U.S.C.
        # § 1817(a)(3) and FFIEC instructions, not 12 C.F.R. § 304.3.
        # CDR/XBRL submission is an FFIEC instruction, not a CFR mandate.
        # Schedule RC / RC-N / form selection are FFIEC form instructions
        # → authority_type = GUIDANCE.
        return [
            {
                "requirement_id": "FFIEC-CALL-DEADLINE-001",
                "regulation_id": "FFIEC-CALL-041",
                "requirement_text": (
                    "Call Report must be submitted within 30 calendar days after the "
                    "end of the reporting period (quarter-end). Late submissions require "
                    "notification to the institution's primary federal regulator."
                ),
                "rule_type": "filing_deadline",
                "authority_type": "STATUTE",
                "cfr_citation": "12 U.S.C. § 1817(a)(3)",
                "authority_source": "FFIEC Call Report General Instructions",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify Call Report submission logged ≤ 30 days after quarter-end",
                "parameters": {
                    "deadline_days_after_quarter": 30,
                    "quarter_ends": ["03-31", "06-30", "09-30", "12-31"],
                    "form_options": ["FFIEC031", "FFIEC041", "FFIEC051"],
                },
            },
            {
                "requirement_id": "FFIEC-CALL-XBRL-001",
                "regulation_id": "FFIEC-CALL-041",
                "requirement_text": (
                    "Call Report data must be submitted via XBRL taxonomy through "
                    "the Central Data Repository (CDR) at cdr.ffiec.gov. "
                    "Submissions must pass all FFIEC validation rules before acceptance."
                ),
                "rule_type": "submission_format",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Central Data Repository (CDR) submission instructions",
                "cfr_citation": None,
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CDR submission passes all FFIEC edit checks before final submission",
                "parameters": {
                    "submission_method": "XBRL_via_CDR",
                    "portal": "https://cdr.ffiec.gov",
                    "validation_required": True,
                },
            },
            {
                "requirement_id": "FFIEC-CALL-RC-001",
                "regulation_id": "FFIEC-CALL-041",
                "requirement_text": (
                    "Schedule RC (Consolidated Balance Sheet) must report total assets, "
                    "total deposits, total loans and leases, total securities, "
                    "and total equity capital as of quarter-end."
                ),
                "rule_type": "required_schedule",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 041 Schedule RC Instructions",
                "cfr_citation": None,
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify RC schedule fields populated and balance sheet balances (assets = liabilities + equity)",
                "parameters": {
                    "schedule": "RC",
                    "required_line_items": [
                        "total_assets",
                        "total_deposits",
                        "total_loans_leases",
                        "total_securities",
                        "total_equity_capital",
                    ],
                    "balance_check": "total_assets = total_liabilities + total_equity",
                },
            },
            {
                "requirement_id": "FFIEC-CALL-RCN-001",
                "regulation_id": "FFIEC-CALL-041",
                "requirement_text": (
                    "Schedule RC-N (Past Due and Nonaccrual Loans) must report "
                    "30-89 days past due, 90+ days past due, and nonaccrual status "
                    "for all loan categories including residential real estate, "
                    "commercial real estate, C&I, consumer, and agricultural loans."
                ),
                "rule_type": "required_schedule",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 041 Schedule RC-N Instructions",
                "cfr_citation": None,
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify RC-N captures all nonperforming loan categories with correct aging buckets",
                "parameters": {
                    "schedule": "RC-N",
                    "aging_buckets": ["30_89_days", "90_plus_days", "nonaccrual"],
                    "loan_categories": [
                        "residential_re",
                        "commercial_re",
                        "ci_loans",
                        "consumer",
                        "agricultural",
                    ],
                },
            },
            {
                "requirement_id": "FFIEC-CALL-FORM-SELECT-001",
                "regulation_id": "FFIEC-CALL-051",
                "requirement_text": (
                    "FFIEC 051 is available only to insured institutions with total assets "
                    "less than $5 billion that are not required to file the FFIEC 031. "
                    "Institutions exceeding the threshold must transition to FFIEC 041 by "
                    "the following quarter."
                ),
                "rule_type": "form_eligibility",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 051 General Instructions",
                "cfr_citation": None,
                "threshold_amount": 5_000_000_000,
                "threshold_currency": "USD",
                "test_requirement": "Verify form selection logic routes banks >$5B assets to FFIEC 041",
                "parameters": {
                    "asset_threshold_usd": 5_000_000_000,
                    "eligible_form": "FFIEC051",
                    "fallback_form": "FFIEC041",
                    "transition_period": "next_quarter",
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — parent_requirement_id + inherited authority on every KPI.
        return [
            {
                "kpi_id": "FFIEC-CALL-KPI-001",
                "regulation_id": "FFIEC-CALL-041",
                "parent_requirement_id": "FFIEC-CALL-DEADLINE-001",
                "authority_type": "STATUTE",
                "citation": "12 U.S.C. § 1817(a)(3)",
                "kpi_name": "Call Report Submission Timeliness",
                "kpi_formula": "Days between quarter-end and CDR submission date (target ≤30)",
                "target_value": 30.0,
                "target_unit": "days",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 25.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "FFIEC-CALL-KPI-002",
                "regulation_id": "FFIEC-CALL-041",
                "parent_requirement_id": "FFIEC-CALL-XBRL-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC CDR submission instructions",
                "citation": None,
                "kpi_name": "CDR Edit Check Pass Rate",
                "kpi_formula": "(Validation checks passing / Total validation checks) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 100.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "FFIEC-CALL-KPI-003",
                "regulation_id": "FFIEC-CALL-041",
                "parent_requirement_id": "FFIEC-CALL-RC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 041 Schedule RC Instructions",
                "citation": None,
                "kpi_name": "Balance Sheet Balancing (RC Schedule)",
                "kpi_formula": "ABS(Total Assets - (Total Liabilities + Total Equity)) — target $0",
                "target_value": 0.0,
                "target_unit": "usd",
                "monitoring_frequency": "quarterly",
                "alert_threshold": 1.0,
                "criticality": "critical",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # regulator=FFIEC on every TC (was defaulting to OCC downstream, per
        # C-12). parent_requirement_id + inherited authority on every row.
        return [
            {
                "scenario_id": "TC-CALL-001",
                "regulation_id": "FFIEC-CALL-041",
                "parent_requirement_id": "FFIEC-CALL-DEADLINE-001",
                "regulator": "FFIEC",
                "authority_type": "STATUTE",
                "citation": "12 U.S.C. § 1817(a)(3)",
                "scenario_name": "Call Report submitted on day 31 — late filing triggered",
                "priority": "critical",
                "given": "Quarter ended March 31; Call Report not submitted",
                "when_step": "Day 31 (April 30) arrives without CDR submission",
                "then_step": "System triggers late-filing alert; notification sent to primary regulator contact",
            },
            {
                "scenario_id": "TC-CALL-002",
                "regulation_id": "FFIEC-CALL-041",
                "parent_requirement_id": "FFIEC-CALL-RC-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 041 Schedule RC Instructions",
                "citation": None,
                "scenario_name": "RC schedule balance sheet does not balance",
                "priority": "critical",
                "given": "Bank generates RC schedule for Q3",
                "when_step": "Total assets ≠ total liabilities + equity by $50,000",
                "then_step": "CDR submission blocked with error: 'Schedule RC does not balance'; requires correction",
            },
            {
                "scenario_id": "TC-CALL-003",
                "regulation_id": "FFIEC-CALL-051",
                "parent_requirement_id": "FFIEC-CALL-FORM-SELECT-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 051 General Instructions",
                "citation": None,
                "scenario_name": "Bank exceeds $5B asset threshold — must upgrade to FFIEC 041",
                "priority": "high",
                "given": "Community bank has been filing FFIEC 051",
                "when_step": "Total assets reach $5.2B at Q4 year-end",
                "then_step": "System flags: 'Threshold exceeded — transition to FFIEC 041 required by Q1 next year'",
            },
            {
                "scenario_id": "TC-CALL-004",
                "regulation_id": "FFIEC-CALL-041",
                "parent_requirement_id": "FFIEC-CALL-RCN-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC 041 Schedule RC-N Instructions",
                "citation": None,
                "scenario_name": "RC-N aging buckets validated correctly",
                "priority": "high",
                "given": "Commercial loan past due 95 days",
                "when_step": "RC-N schedule populated for quarter",
                "then_step": "Loan reported in '90+ days past due' bucket, not '30-89 days past due'",
            },
        ]
