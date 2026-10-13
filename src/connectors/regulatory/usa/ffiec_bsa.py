"""
FFIEC BSA/AML Connector — FFIEC Joint Interagency BSA/AML Examination Manual.
Complements occ_bsa.py with FFIEC-level joint guidance.

Sources: https://bsaaml.ffiec.gov/ (FFIEC BSA/AML InfoBase)

LIVE SCRAPE STATUS — eCFR-backed for LAW rows.
bsaaml.ffiec.gov sits behind Cloudflare Bot Management with a JS challenge
that no UA/header/JA3 trick defeats (verified 2026-05-17), so the manual
prose remains unreachable. That was fine as an examination-manual gap,
but the previous _build_regulations() also hardcoded the underlying CFR
text — which correction_smb.md §11.4 (Item 6) flags as the least
trustworthy content in the corpus. The SAR-90-day and CDD-20% defects
lived here.

_build_regulations() now fetches primary text from eCFR via
``src.services.ecfr_client``. Each row carries source_url, source_content_hash,
scraped_at, and citation_verified=True — populating the §11 provenance
fields so the authority gate can class this content as LAW. On eCFR
failure the row is emitted with citation_verified=False; the gate then
routes it to the advisory rail rather than asserting it as codified law.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
import structlog

from src.connectors.base import RegulatoryConnector
from src.services.ecfr_client import ECFRError, ECFRSection, fetch_section

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "ffiec_bsa_main": "https://bsaaml.ffiec.gov/manual",
    "ffiec_bsa_overview": "https://bsaaml.ffiec.gov/manual/Introduction/01",
    "ffiec_bsa_risk_assessment": "https://bsaaml.ffiec.gov/manual/BSAAMLComplianceProgram/01",
    "ffiec_bsa_cdd": "https://bsaaml.ffiec.gov/manual/CustomerIdentificationProgram/01",
    "ffiec_bsa_beneficial": "https://bsaaml.ffiec.gov/manual/RegulatoryRequirements/01",
    "ffiec_bsa_products": "https://bsaaml.ffiec.gov/manual/ProductsServiceRisks/01",
    "ffiec_bsa_sar": "https://bsaaml.ffiec.gov/manual/SuspiciousActivityReporting/01",
    "ffiec_bsa_ctr": "https://bsaaml.ffiec.gov/manual/CurrencyTransactionReporting/01",
    "ffiec_bsa_info_sharing": "https://bsaaml.ffiec.gov/manual/Section314/01",
    "ffiec_bsa_pdf": "https://bsaaml.ffiec.gov/docs/FFIEC_BSA_AML_ExamManual.pdf",
}


class FfiecBsaConnector(RegulatoryConnector):
    """FFIEC BSA/AML Examination Manual connector.

    Covers joint interagency BSA/AML examination guidance including:
      - BSA/AML Compliance Program (Four Pillars)
      - Customer Identification Program (CIP)
      - Enhanced Due Diligence for higher-risk customers
      - Section 314(a) and 314(b) information sharing
      - SAR filing requirements and safe harbor
      - CTR aggregation and exemptions
      - OFAC sanctions integration with BSA
    """

    connector_name = "ffiec_bsa"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("ffiec_bsa_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("ffiec_bsa_extraction_started")
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
            logger.warning("ffiec_bsa_live_scrape_failed", error=str(exc))

        logger.info(
            "ffiec_bsa_extraction_completed",
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
            scrape_linked_pages,
            scrape_pdf_url,
        )

        session = make_session(self.urls["ffiec_bsa_main"])
        docs: List[Dict] = []

        for url, category in [
            (self.urls["ffiec_bsa_overview"], "BSA Overview"),
            (self.urls["ffiec_bsa_risk_assessment"], "BSA Risk Assessment"),
            (self.urls["ffiec_bsa_cdd"], "CIP/CDD"),
            (self.urls["ffiec_bsa_sar"], "SAR Filing"),
            (self.urls["ffiec_bsa_ctr"], "CTR Reporting"),
            (self.urls["ffiec_bsa_info_sharing"], "Section 314"),
        ]:
            docs.extend(
                scrape_html_page(session, url, source="FFIEC", category=category)
            )

        docs.extend(
            scrape_pdf_url(
                session, self.urls["ffiec_bsa_pdf"],
                source="FFIEC", category="BSA/AML Examination Manual",
            )
        )

        for doc in docs:
            enrich_document(doc, regulation_id="FFIEC-BSA", doc_type="examination")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    # (title, section) tuples backing each rule row. The 31 CFR Part 1010
    # and Part 1020 sections here are the primary CFR that the FFIEC
    # BSA/AML manual describes — the manual itself is guidance, but the
    # underlying obligations are law. The 314(a) rule spans multiple
    # adjacent sections (1010.520 / .530 / .540); we fetch .520 as the
    # anchor and leave the range citation in the emitted row.
    _ECFR_TARGETS: Dict[str, Tuple[int, str]] = {
        "FFIEC-BSA-PROG-001": (31, "1020.210"),
        "FFIEC-BSA-CIP-001":  (31, "1020.220"),
        "FFIEC-BSA-314-001":  (31, "1010.520"),
    }

    _STATIC_METADATA: Dict[str, Dict[str, Any]] = {
        "FFIEC-BSA-PROG-001": {
            "regulation_name": "FFIEC BSA/AML Compliance Program — Four Pillars",
            "category": "BSA/AML",
            "subcategory": "Compliance Program",
            "cfr_citation": "31 C.F.R. § 1020.210",
            "authority_type": "LAW",
            "source_manual_url_key": "ffiec_bsa_main",
        },
        "FFIEC-BSA-CIP-001": {
            "regulation_name": "FFIEC BSA/AML — Customer Identification Program (CIP)",
            "category": "BSA/AML",
            "subcategory": "CIP/KYC",
            "cfr_citation": "31 C.F.R. § 1020.220",
            "authority_type": "LAW",
            "source_manual_url_key": "ffiec_bsa_cdd",
        },
        "FFIEC-BSA-314-001": {
            "regulation_name": (
                "FFIEC BSA/AML — Section 314(a) and 314(b) Information Sharing"
            ),
            "category": "BSA/AML",
            "subcategory": "Information Sharing",
            "cfr_citation": "31 C.F.R. §§ 1010.520-1010.540",
            "authority_type": "LAW",
            "source_manual_url_key": "ffiec_bsa_info_sharing",
        },
    }

    def _build_regulations(self) -> List[Dict[str, Any]]:
        """Emit LAW rows for BSA program / CIP / 314(a), with primary text
        pulled live from eCFR.

        The dict shape preserves the fields the scraper_scheduler already
        persists (``regulation_id``, ``cfr_citation``, ``source_url``,
        etc.) and adds the §11 provenance fields (``authority_type``,
        ``citation_verified``, ``source_content_hash``, ``scraped_at``)
        so the authority gate can class these rows as LAW rather than
        falling through to advisory.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        session = requests.Session()
        rows: List[Dict[str, Any]] = []

        for regulation_id, (title, section) in self._ECFR_TARGETS.items():
            meta = self._STATIC_METADATA[regulation_id]
            manual_url = self.urls[meta["source_manual_url_key"]]

            row: Dict[str, Any] = {
                "regulation_id": regulation_id,
                "regulation_name": meta["regulation_name"],
                "category": meta["category"],
                "subcategory": meta["subcategory"],
                "regulator": "FFIEC",
                "geography": "usa",
                "cfr_citation": meta["cfr_citation"],
                "authority_type": meta["authority_type"],
                "jurisdiction_level": "federal",
                # C-13 — effective_date only from FR `effective_on`.
                # eCFR AMDDATE is a best-effort backfill until the FR
                # connector lands.
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": manual_url,
                "last_updated": now_iso,
            }

            try:
                fetched = fetch_section(title, section, session=session)
                self._apply_ecfr(row, fetched)
                logger.info(
                    "ffiec_bsa_ecfr_ok",
                    regulation_id=regulation_id,
                    citation=fetched.citation,
                    content_hash=fetched.content_hash[:12],
                )
            except ECFRError as exc:
                # Emit the row without provenance so the authority gate
                # routes it to advisory rather than asserting fabricated
                # LAW. Do NOT fall back to hardcoded CFR text — that is
                # exactly the pattern §11.4 replaces.
                row["citation_verified"] = False
                row["authority_source"] = (
                    "eCFR fetch failed — see logs. Row emitted without "
                    "provenance; authority gate will route to advisory."
                )
                logger.warning(
                    "ffiec_bsa_ecfr_failed",
                    regulation_id=regulation_id,
                    section=section,
                    error=str(exc),
                )
            rows.append(row)
        return rows

    @staticmethod
    def _apply_ecfr(row: Dict[str, Any], fetched: ECFRSection) -> None:
        """Splice a fetched eCFR section into a rule row."""
        row["rule_text"] = fetched.text
        row["source_url"] = fetched.source_url
        row["source_content_hash"] = fetched.content_hash
        row["scraped_at"] = fetched.fetched_at.isoformat()
        row["citation_verified"] = True
        row["citation_verified_at"] = fetched.fetched_at.isoformat()
        row["citation_verified_source"] = fetched.verifier_source
        if fetched.effective_date:
            row["effective_date"] = fetched.effective_date.isoformat()
        if fetched.heading:
            row["heading"] = fetched.heading

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            # C-8 — five pillars, not four. The fifth (risk-based CDD/EDD)
            # was added by the 2018 CDD Rule at 31 CFR 1010.230.
            {
                "requirement_id": "FFIEC-BSA-PILLAR-001",
                "regulation_id": "FFIEC-BSA-PROG-001",
                "authority_type": "LAW",
                "requirement_text": (
                    "BSA/AML compliance program must include five pillars: "
                    "(1) internal controls, (2) independent testing, "
                    "(3) a designated BSA Officer, (4) training for appropriate personnel, "
                    "and (5) risk-based customer due diligence (added by the CDD Rule)."
                ),
                "rule_type": "program_requirement",
                "cfr_citation": "31 C.F.R. § 1020.210; 31 C.F.R. § 1010.230",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify BSA program documentation includes all five pillars",
                "parameters": {
                    "pillars": [
                        "internal_controls",
                        "independent_testing",
                        "bsa_officer_designated",
                        "personnel_training",
                        "risk_based_cdd",
                    ]
                },
            },
            # C-7 — primary-key collision fix. The requirement previously
            # used requirement_id = FFIEC-BSA-CIP-001, colliding with the
            # rule of the same id. Renamed to -REQ-001 so joins keyed on
            # rule_id no longer silently pick one row.
            {
                "requirement_id": "FFIEC-BSA-CIP-REQ-001",
                "regulation_id": "FFIEC-BSA-CIP-001",
                "authority_type": "LAW",
                "requirement_text": (
                    "Customer Identification Program must verify identity of each customer "
                    "opening an account using at minimum: name, date of birth (individuals), "
                    "address, and identification number (SSN/ITIN for US persons, "
                    "passport or comparable for non-US persons)."
                ),
                "rule_type": "cip_verification",
                "cfr_citation": "31 C.F.R. § 1020.220(a)(2)",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CIP collects and verifies all required identity fields at account opening",
                "parameters": {
                    "required_fields": ["name", "date_of_birth", "address", "identification_number"],
                    "verification_methods": ["documentary", "non_documentary"],
                    "retention_years": 5,
                },
            },
            # C-9 — EDD cite extended. 1010.610 covers foreign
            # correspondent accounts; 1010.620 covers private banking /
            # senior foreign political figures. Requirement text names
            # both categories.
            {
                "requirement_id": "FFIEC-BSA-EDD-001",
                "regulation_id": "FFIEC-BSA-CIP-001",
                "authority_type": "LAW",
                "requirement_text": (
                    "Enhanced Due Diligence (EDD) required for higher-risk customers "
                    "including foreign correspondent accounts (31 CFR 1010.610), private "
                    "banking accounts for senior foreign political figures (31 CFR 1010.620), "
                    "PEPs, and customers in high-risk jurisdictions. EDD must include source "
                    "of funds and wealth verification."
                ),
                "rule_type": "enhanced_due_diligence",
                "cfr_citation": "31 C.F.R. § 1010.610; 31 C.F.R. § 1010.620",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify EDD triggers for high-risk customer categories; source of funds documented",
                "parameters": {
                    "high_risk_categories": [
                        "foreign_correspondent_accounts",
                        "private_banking_accounts",
                        "senior_foreign_political_figures",
                        "politically_exposed_persons",
                        "high_risk_jurisdiction_customers",
                        "cash_intensive_businesses",
                        "msbs",
                    ],
                    "edd_components": ["source_of_funds", "source_of_wealth", "purpose_of_account"],
                },
            },
            {
                "requirement_id": "FFIEC-BSA-314A-001",
                "regulation_id": "FFIEC-BSA-314-001",
                "requirement_text": (
                    "Upon receipt of a FinCEN Section 314(a) request, institution must "
                    "search records for named subjects within 2 weeks and report matches "
                    "to FinCEN. Institution may not disclose the search to any party."
                ),
                "rule_type": "law_enforcement_cooperation",
                "cfr_citation": "31 C.F.R. § 1010.520",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify 314(a) search executed within 14 days; results reported to FinCEN; tippoff prohibition enforced",
                "parameters": {
                    "response_deadline_days": 14,
                    "tippoff_prohibited": True,
                    "search_scope": ["accounts", "transactions"],
                    "lookback_years": 6,
                },
            },
            {
                "requirement_id": "FFIEC-BSA-SAR-SAFE-001",
                "regulation_id": "FFIEC-BSA-PROG-001",
                "authority_type": "STATUTE",
                "requirement_text": (
                    "SAR safe harbor: institution and its directors, officers, employees, "
                    "and agents are protected from civil liability for filing or not filing "
                    "a SAR. Notification that a SAR has been filed is prohibited."
                ),
                "rule_type": "safe_harbor",
                "cfr_citation": "31 U.S.C. § 5318(g)(3)",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify SAR notification prohibition enforced; no SAR disclosure to subject",
                "parameters": {
                    "tippoff_prohibited": True,
                    "civil_liability_protected": True,
                    "safe_harbor_applies_to": ["directors", "officers", "employees", "agents"],
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — KPIs carry parent_requirement_id + inherited citation +
        # authority_type back to the parent LAW rule.
        return [
            {
                "kpi_id": "FFIEC-BSA-KPI-001",
                "regulation_id": "FFIEC-BSA-PROG-001",
                "parent_requirement_id": "FFIEC-BSA-PILLAR-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.210",
                "kpi_name": "BSA Officer Training Completion Rate",
                "kpi_formula": "(Staff completing annual BSA training / Total required staff) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "annual",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
            {
                "kpi_id": "FFIEC-BSA-KPI-002",
                "regulation_id": "FFIEC-BSA-314-001",
                "parent_requirement_id": "FFIEC-BSA-314A-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.520",
                "kpi_name": "314(a) Response Timeliness",
                "kpi_formula": "(314(a) searches completed within 14 days / Total 314(a) requests) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "per_request",
                "alert_threshold": 100.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "FFIEC-BSA-KPI-003",
                "regulation_id": "FFIEC-BSA-CIP-001",
                "parent_requirement_id": "FFIEC-BSA-CIP-REQ-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.220(a)(2)",
                "kpi_name": "CIP Completion Rate at Account Opening",
                "kpi_formula": "(Accounts with complete CIP / Total new accounts) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 99.0,
                "criticality": "critical",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # C-11/C-12 — TCs carry parent_requirement_id + inherited citation +
        # authority_type + regulator=FFIEC (was implicitly OCC on prior rows).
        return [
            {
                "scenario_id": "TC-FFIEC-BSA-001",
                "regulation_id": "FFIEC-BSA-PROG-001",
                "parent_requirement_id": "FFIEC-BSA-PILLAR-001",
                "regulator": "FFIEC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.210",
                "scenario_name": "BSA program missing independent testing — exam finding",
                "priority": "critical",
                "given": "Bank BSA program documentation submitted for exam",
                "when_step": "Examiner reviews BSA program for four-pillar compliance",
                "then_step": "Finding issued: 'Independent testing (Pillar 2) not performed in last 12 months'",
            },
            {
                "scenario_id": "TC-FFIEC-BSA-002",
                "regulation_id": "FFIEC-BSA-CIP-001",
                "parent_requirement_id": "FFIEC-BSA-CIP-REQ-001",
                "regulator": "FFIEC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.220(a)(2)",
                "scenario_name": "CIP missing date of birth for individual customer",
                "priority": "critical",
                "given": "Individual customer opens checking account",
                "when_step": "Account opened without recording date of birth",
                "then_step": "System blocks account activation: 'CIP incomplete: date_of_birth required for individuals'",
            },
            {
                "scenario_id": "TC-FFIEC-BSA-003",
                "regulation_id": "FFIEC-BSA-314-001",
                "parent_requirement_id": "FFIEC-BSA-314A-001",
                "regulator": "FFIEC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.520",
                "scenario_name": "314(a) response sent on day 15 — late",
                "priority": "critical",
                "given": "FinCEN 314(a) request received on March 1",
                "when_step": "Bank submits search results to FinCEN on March 16 (day 15)",
                "then_step": "System flags: '314(a) response deadline exceeded by 1 day'; escalated to BSA Officer",
            },
            {
                "scenario_id": "TC-FFIEC-BSA-004",
                "regulation_id": "FFIEC-BSA-CIP-001",
                "parent_requirement_id": "FFIEC-BSA-EDD-001",
                "regulator": "FFIEC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.610; 31 C.F.R. § 1010.620",
                "scenario_name": "PEP customer requires EDD — standard CDD insufficient",
                "priority": "high",
                "given": "New customer identified as politically exposed person (foreign official)",
                "when_step": "Standard CDD screening completed",
                "then_step": "EDD workflow triggered: source of wealth and source of funds documentation required",
            },
        ]
