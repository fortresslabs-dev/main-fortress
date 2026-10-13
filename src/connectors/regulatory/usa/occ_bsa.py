"""
OCC BSA/AML Connector — Office of the Comptroller of the Currency
Extracts BSA/AML compliance rules, wire transfer requirements, and KPIs.

Source: OCC BSA/AML (MVP1 — static structured rules)
Priority: P0 CRITICAL — 8 test cases (23% of total)
"""
from datetime import datetime
from typing import Dict, List, Any, Optional
import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

OCC_BSA_SOURCE = "OCC BSA/AML"

# Default URLs — overridable via config/connectors/usa.yaml → source_urls.occ_bsa
_DEFAULT_URLS = {
    "manual": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/bsa/index-bsa.html",
    "bsa_guidance": "https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html",
    "bulletins": "https://www.occ.gov/news-issuances/bulletins/index-bulletins.html",
    "laws_and_regulations": "https://www.occ.gov/topics/laws-and-regulations/index-laws-and-regulations.html",
    "comptrollers_handbook": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/index-comptrollers-handbook.html",
    "bank_supervision_pdf": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/bank-supervision-process/pub-ch-bank-supervision-process.pdf",
    "large_bank_supervision_pdf": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/large-bank-supervision/pub-ch-large-bank-supervision.pdf",
    # REQ-015: Interpretive guidance
    "interpretive_letters": "https://www.occ.gov/topics/laws-and-regulations/interpretations-and-actions/index-interpretive-letters.html",
    "no_objection_letters": "https://www.occ.gov/topics/laws-and-regulations/interpretations-and-actions/index-no-objection-letters.html",
}


class OccBsaConnector(RegulatoryConnector):
    """OCC BSA/AML regulatory connector.

    Provides structured compliance rules for:
      - Travel Rule — transmittor/recipient info for transmittals >= $3K
        (31 C.F.R. § 1010.410(f))
      - Originator's-bank recordkeeping — 5-yr retention for payment orders
        >= $3K (31 C.F.R. § 1020.410(a))
      - Wire dual control — OCC Comptroller's Handbook INTERNAL_CONTROL
        (not codified in federal regulation)
      - CTR filing — $10K cash threshold (31 C.F.R. § 1010.311)
      - Customer Due Diligence — 25% beneficial-ownership prong + control
        prong (31 C.F.R. § 1010.230(d)(1))
    """

    connector_name = "occ_bsa"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("occ_bsa_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        """Return structured BSA/AML compliance data (static baseline + live scraped)."""
        logger.info("occ_bsa_extraction_started")

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
            logger.warning("occ_bsa_live_scrape_failed_using_static", error=str(exc))

        logger.info(
            "occ_bsa_extraction_completed",
            regulations=len(regulations),
            requirements=len(requirements),
            kpis=len(kpis),
            test_scenarios=len(test_scenarios),
            scraped_documents=len(result["scraped_documents"]),
        )
        return result

    def _infer_regulation_id(self, doc: dict) -> str:
        """Infer a regulation_id from document title/section for enrich_document."""
        title = (doc.get("section_title", "") + " " + doc.get("document_title", "") +
                 " " + doc.get("title", "")).lower()
        if "travel rule" in title or "1010.410" in title:
            return "BSA-TRAVEL-001"
        if "recordkeeping" in title or "1020.410" in title or "record retention" in title:
            return "BSA-RECKEEP-001"
        if "wire" in title or "dual approval" in title or "dual control" in title:
            # Wire-desk internal-control matter; kept for legacy doc routing.
            return "BSA-DUALCTRL-001"
        if "ctr" in title or "currency transaction" in title:
            return "OCC-BSA-CTR-001"
        if "sar" in title or "suspicious activity" in title:
            return "OCC-BSA-SAR-001"
        if "cdd" in title or "beneficial ownership" in title or "due diligence" in title:
            return "OCC-BSA-CDD-001"
        return "OCC-BSA"

    def _scrape_live(self) -> List[Dict]:
        """Scrape OCC BSA/AML pages, handbook PDFs, and sitemap-discovered content."""
        from src.connectors.regulatory.usa._scraper_utils import (
            check_rss_feed,
            discover_compliance_urls,
            enrich_document,
            make_session,
            scrape_discovered_urls,
            scrape_html_page,
            scrape_pdf_url,
        )

        session = make_session()
        docs: List[Dict] = []

        # 1. BSA/AML guidance HTML
        docs.extend(
            scrape_html_page(
                session,
                self.urls["bsa_guidance"],
                source="OCC",
                category="BSA/AML Guidance",
            )
        )

        # 2. Recent bulletins page
        docs.extend(
            scrape_html_page(
                session,
                self.urls["bulletins"],
                source="OCC",
                category="OCC Bulletins",
            )
        )

        # 3. OCC Regulations (12 CFR Parts)
        docs.extend(
            scrape_html_page(
                session,
                self.urls["laws_and_regulations"],
                source="OCC",
                category="Laws and Regulations",
            )
        )

        # 4. Comptroller's Handbook index
        docs.extend(
            scrape_html_page(
                session,
                self.urls["comptrollers_handbook"],
                source="OCC",
                category="Comptrollers Handbook",
            )
        )

        # 5. BSA/AML Comptroller's Handbook PDF — Bank Supervision Process
        docs.extend(
            scrape_pdf_url(
                session,
                self.urls["bank_supervision_pdf"],
                source="OCC",
                category="Bank Supervision Process",
            )
        )

        # 6. Large Bank Supervision PDF
        docs.extend(
            scrape_pdf_url(
                session,
                self.urls["large_bank_supervision_pdf"],
                source="OCC",
                category="Large Bank Supervision",
            )
        )

        # 7. Interpretive letters (doc_type=interpretive) — REQ-015
        docs.extend(
            scrape_html_page(
                session,
                self.urls["interpretive_letters"],
                source="OCC",
                category="Interpretive Letters",
            )
        )
        docs.extend(
            scrape_html_page(
                session,
                self.urls["no_objection_letters"],
                source="OCC",
                category="No-Objection Letters",
            )
        )

        # 8. Sitemap-driven discovery — find compliance pages not in hardcoded URLs
        try:
            existing = set(self.urls.values())
            discovered = discover_compliance_urls(
                session, agency="occ",
                sitemap_url=self.urls.get("sitemap_url"),
                existing_urls=existing,
            )
            if discovered.get("new_urls"):
                logger.info("occ_sitemap_discovery", new_urls=len(discovered["new_urls"]))
                docs.extend(
                    scrape_discovered_urls(
                        session, discovered, source="OCC",
                        category="Discovered Compliance Content",
                        max_html_pages=50, max_pdfs=10,
                    )
                )
        except Exception as exc:
            logger.warning("occ_sitemap_discovery_failed", error=str(exc))

        # Enrich all docs with obligation metadata, CFR citations, and regulation IDs
        enriched = []
        for doc in docs:
            doc_type = "reg_text"
            cat = doc.get("category", "").lower()
            if "interpretive" in cat or "letter" in cat:
                doc_type = "interpretive"
            elif "guidance" in cat or "bulletin" in cat:
                doc_type = "guidance"
            enriched.append(enrich_document(
                doc,
                regulation_id=self._infer_regulation_id(doc),
                state_code=None,
                doc_type=doc_type,
            ))
        return enriched

    def check_updates(self) -> List[Dict]:
        """Check OCC bulletins page for recent changes (hash-based, no RSS)."""
        from src.connectors.regulatory.usa._scraper_utils import (
            make_session,
            scrape_html_page,
        )

        session = make_session()
        return scrape_html_page(
            session,
            self.urls["bulletins"],
            source="OCC",
            category="OCC Bulletins",
        )

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        manual_url = self.urls["manual"]
        return [
            # ── Travel Rule (LAW) — 31 C.F.R. § 1010.410(f) — $3,000 ──────
            # Replaces the fabricated "OCC-BSA-WIRE-001 dual approval at
            # $10,000" record. No federal dual-approval requirement exists;
            # the citation previously used (1010.410(f)) is the Travel Rule
            # at $3,000, not a $10,000 approval mandate.
            {
                "regulation_id": "BSA-TRAVEL-001",
                "regulation_name": "BSA Travel Rule — Transmittal Order Information",
                "cfr_citation": "31 C.F.R. § 1010.410(f)",
                "authority_type": "LAW",
                "rule_text": (
                    "For transmittals of funds of $3,000 or more, the transmittor's "
                    "financial institution must include in the transmittal order: "
                    "transmittor name and address, transmittor account number (if ordered "
                    "from an account), amount, date, identity of the recipient's financial "
                    "institution, and as many of the recipient's name, address, and account "
                    "number as are received. Intermediary FIs must pass on all information. "
                    "31 C.F.R. § 1010.410(f)."
                ),
                "category": "BSA/AML",
                "subcategory": "Wire Transfers",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,   # per §11.3 — populated only from FR effective_on
                "exam_frequency": "quarterly",
                "source_url": "https://www.ecfr.gov/current/title-31/section-1010.410",
            },
            # ── Recordkeeping (LAW) — 31 C.F.R. § 1020.410(a) — $3,000 ────
            {
                "regulation_id": "BSA-RECKEEP-001",
                "regulation_name": "Originator's-Bank Recordkeeping",
                "cfr_citation": "31 C.F.R. § 1020.410(a)",
                "authority_type": "LAW",
                "rule_text": (
                    "For each payment order of $3,000 or more accepted as originator's bank, "
                    "retain originator name/address, amount, date, payment instructions, and "
                    "beneficiary bank identity. Retrievable by originator name (and account "
                    "number for established customers). 5-year retention. "
                    "31 C.F.R. § 1020.410(a)."
                ),
                "category": "BSA/AML",
                "subcategory": "Wire Recordkeeping",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": "https://www.ecfr.gov/current/title-31/section-1020.410",
            },
            # ── Dual control (INTERNAL_CONTROL) — not law ─────────────────
            # OCC Comptroller's Handbook supervisory expectation. Kept as
            # advisory so the dashboard still surfaces it and Test Factory
            # still generates internal-control tests — but NOT gated as
            # a legal requirement, per hard gate (§11.3).
            {
                "regulation_id": "BSA-DUALCTRL-001",
                "regulation_name": "Wire Dual Control (Internal Control)",
                "cfr_citation": None,
                "authority_type": "INTERNAL_CONTROL",
                "authority_source": (
                    "OCC Comptroller's Handbook — Wire Transfer; safety-and-soundness "
                    "supervisory expectation. NOT mandated by any federal regulation."
                ),
                "rule_text": (
                    "Segregation of duties and dual control over wire initiation and release. "
                    "A supervisory expectation and common internal control. NOT mandated by "
                    "any federal regulation — do not cite a CFR section."
                ),
                "category": "BSA/AML",
                "subcategory": "Wire Transfers",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": manual_url,
                "bank_configurable_threshold": True,
            },
            {
                "regulation_id": "OCC-BSA-CTR-001",
                "regulation_name": "Currency Transaction Report Filing",
                "cfr_citation": "31 C.F.R. § 1010.311",
                "authority_type": "LAW",
                "rule_text": (
                    "Financial institutions must file a Currency Transaction Report (CTR) "
                    "for each transaction in currency of more than $10,000, or multiple "
                    "transactions that aggregate to more than $10,000 in a single business day "
                    "by or on behalf of the same person. CTR must be filed within 15 calendar "
                    "days. 31 C.F.R. § 1010.311."
                ),
                "category": "BSA/AML",
                "subcategory": "CTR",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": manual_url,
            },
            # ── CDD (LAW) — 31 C.F.R. § 1010.230(d)(1) — 25% ─────────────
            # Prior text had TWO defects: 20% (wrong — 25%) and a
            # fabricated "FinCEN amendment effective April 11, 2026". No
            # such amendment exists. Verified against eCFR 2026-07-09.
            {
                "regulation_id": "OCC-BSA-CDD-001",
                "regulation_name": "Customer Due Diligence Requirements",
                "cfr_citation": "31 C.F.R. § 1010.230",
                "authority_type": "LAW",
                "rule_text": (
                    "Covered financial institutions must establish and maintain written "
                    "customer due diligence procedures. For each legal entity customer, the "
                    "institution must identify and verify: (1) each individual who directly "
                    "or indirectly owns 25 percent or more of the equity interests (ownership "
                    "prong, up to 4 individuals) AND (2) one individual with significant "
                    "responsibility to control, manage, or direct the entity (control prong). "
                    "Ongoing monitoring of customer activity is required to detect and report "
                    "suspicious transactions. 31 C.F.R. § 1010.230(d)(1)."
                ),
                "category": "BSA/AML",
                "subcategory": "CDD/KYC",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": manual_url,
            },
            # ── Account-opening exceptive relief (GUIDANCE) ──────────────
            # Distinct from the 20%->25% fix above: this is a separate
            # FinCEN administrative ruling that softens *when* BO must be
            # re-verified, not the ownership threshold itself. Emitted as
            # GUIDANCE with no CFR cite.
            {
                "regulation_id": "BSA-CDD-002",
                "regulation_name": "CDD — Account-Opening Exceptive Relief",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": (
                    "FinCEN Administrative Ruling FIN-2026-R001 (2026-02-13)"
                ),
                "rule_text": (
                    "Account-Opening Exceptive Relief. Covered FIs may limit "
                    "identification/verification of beneficial owners to: (1) first account "
                    "opening; (2) when the institution has knowledge of facts calling prior "
                    "BO information into question; (3) as needed under risk-based ongoing "
                    "CDD — rather than at every new account opening."
                ),
                "category": "BSA/AML",
                "subcategory": "CDD/KYC",
                "regulator": "FinCEN",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "quarterly",
                "source_url": "https://www.fincen.gov/resources/statutes-regulations/administrative-rulings",
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        return [
            # --- Travel Rule (LAW) — replaces fabricated OCC-BSA-WIRE-001-R* ---
            {
                "requirement_id": "BSA-TRAVEL-001-R1",
                "regulation_id": "BSA-TRAVEL-001",
                "cfr_citation": "31 C.F.R. § 1010.410(f)",
                "authority_type": "LAW",
                "requirement_text": (
                    "For transmittals of funds of $3,000 or more the transmittor's FI must "
                    "include transmittor name/address/account, amount, date, recipient FI, "
                    "and as many recipient identifiers as are received."
                ),
                "rule_type": "travel_rule",
                "threshold_amount": 3000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify system enforces Travel Rule fields on every transmittal >= $3,000",
                "parameters": {
                    "required_fields": [
                        "transmittor_name", "transmittor_address",
                        "transmittor_account_number", "amount", "date",
                        "recipient_fi", "recipient_name",
                        "recipient_address", "recipient_account_number",
                    ],
                },
            },
            # --- Originator's-bank recordkeeping (LAW) ---
            {
                "requirement_id": "BSA-RECKEEP-001-R1",
                "regulation_id": "BSA-RECKEEP-001",
                "cfr_citation": "31 C.F.R. § 1020.410(a)",
                "authority_type": "LAW",
                "requirement_text": (
                    "For each payment order of $3,000 or more accepted as originator's bank, "
                    "retain originator name/address, amount, date, payment instructions, and "
                    "beneficiary bank identity for 5 years."
                ),
                "rule_type": "wire_recordkeeping",
                "threshold_amount": 3000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify wire audit trail stored for 5+ years, retrievable by originator name",
                "parameters": {
                    "retention_period_years": 5,
                    "retrievable_by": ["originator_name", "originator_account_number"],
                },
            },
            # --- Dual control (INTERNAL_CONTROL — not law) ---
            # Kept so the Test Factory still generates the internal-control
            # test, but authority_type=INTERNAL_CONTROL prevents the hard
            # gate (§11.3) from citing a CFR section.
            {
                "requirement_id": "BSA-DUALCTRL-001-R1",
                "regulation_id": "BSA-DUALCTRL-001",
                "cfr_citation": None,
                "authority_type": "INTERNAL_CONTROL",
                "authority_source": "OCC Comptroller's Handbook — Wire Transfer",
                "requirement_text": (
                    "Wire initiation and release should be segregated between two authorized "
                    "employees. Bank-configurable threshold. Not federally mandated."
                ),
                "rule_type": "dual_approval_limit",
                "threshold_amount": 10000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify system supports dual-control policy for wire transfers per bank threshold",
                "parameters": {
                    "threshold_type": "per_transaction",
                    "approval_count_required": 2,
                    "separation_of_duties": True,
                    "bank_configurable_threshold": True,
                },
            },
            {
                "requirement_id": "BSA-DUALCTRL-001-R2",
                "regulation_id": "BSA-DUALCTRL-001",
                "cfr_citation": None,
                "authority_type": "INTERNAL_CONTROL",
                "authority_source": "OCC Comptroller's Handbook — Wire Transfer",
                "requirement_text": "Initiator and approver must be different individuals (dual control).",
                "rule_type": "separation_of_duties",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify system blocks same user ID from approving twice",
                "parameters": {
                    "block_same_approver": True,
                },
            },
            # --- CTR filing ---
            {
                "requirement_id": "OCC-BSA-CTR-001-R1",
                "regulation_id": "OCC-BSA-CTR-001",
                "cfr_citation": "31 C.F.R. § 1010.311",
                "requirement_text": "Currency Transaction Report must be filed for cash transactions exceeding $10,000",
                "rule_type": "ctr_filing",
                "threshold_amount": 10000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify CTR auto-generated when cash transaction >= $10,000",
                "parameters": {
                    "filing_deadline_days": 15,
                    "aggregation_rule": "same_customer_same_day",
                },
            },
            {
                "requirement_id": "OCC-BSA-CTR-001-R2",
                "regulation_id": "OCC-BSA-CTR-001",
                "cfr_citation": "31 C.F.R. § 1010.311",
                "requirement_text": "Structuring detection: multiple transactions below $10K that aggregate above $10K on same day",
                "rule_type": "structuring_detection",
                "threshold_amount": 10000.00,
                "threshold_currency": "USD",
                "test_requirement": "Verify system detects structuring when same customer makes multiple cash deposits aggregating > $10K",
                "parameters": {
                    "aggregation_window": "24_hours",
                    "alert_action": "generate_sar_referral",
                },
            },
            # --- CDD / Beneficial Ownership (LAW) ---
            # 25% per 31 C.F.R. § 1010.230(d)(1). Prior text asserted 20%
            # under a fabricated "April 11 2026" FinCEN amendment — no
            # such amendment exists. Verified against eCFR 2026-07-09.
            {
                "requirement_id": "OCC-BSA-CDD-001-R1",
                "regulation_id": "OCC-BSA-CDD-001",
                "cfr_citation": "31 C.F.R. § 1010.230(d)(1)",
                "authority_type": "LAW",
                "requirement_text": (
                    "For each legal entity customer, identify and verify: (1) each individual "
                    "who directly or indirectly owns 25% or more of the equity interests "
                    "(ownership prong — up to 4 individuals); AND (2) one individual with "
                    "significant responsibility to control, manage, or direct the entity "
                    "(control prong)."
                ),
                "rule_type": "beneficial_ownership",
                "threshold_amount": 25.0,
                "threshold_currency": None,
                "test_requirement": "Verify system collects both ownership-prong (>=25%, up to 4) and control-prong owners for all legal entity customers",
                "parameters": {
                    "ownership_threshold_percent": 25,
                    "ownership_prong_max_individuals": 4,
                    "control_prong_required": True,
                    "applies_to": "legal_entity_customers",
                    "verification_required": True,
                },
            },
            {
                "requirement_id": "OCC-BSA-CDD-001-R2",
                "regulation_id": "OCC-BSA-CDD-001",
                "cfr_citation": "31 C.F.R. § 1010.230",
                "requirement_text": "Ongoing monitoring of customer activity for unusual transactions",
                "rule_type": "ongoing_monitoring",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify system flags transactions deviating significantly from customer profile",
                "parameters": {
                    "monitoring_type": "risk_based",
                    "high_risk_review_frequency": "quarterly",
                    "medium_risk_review_frequency": "annually",
                },
            },
            {
                "requirement_id": "OCC-BSA-CDD-001-R3",
                "regulation_id": "OCC-BSA-CDD-001",
                "cfr_citation": "31 C.F.R. § 1010.230",
                "requirement_text": "Customer risk rating must be assigned at account opening and updated at trigger events",
                "rule_type": "risk_rating",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify risk rating assigned at onboarding and re-evaluated at trigger events",
                "parameters": {
                    "risk_levels": ["low", "medium", "high"],
                    "trigger_events": [
                        "account_opening",
                        "large_transaction",
                        "address_change",
                        "negative_news_alert",
                    ],
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        return [
            # Internal-control KPI (re-parented from the fabricated
            # OCC-BSA-WIRE-001 to BSA-DUALCTRL-001). Kept as an
            # internal-control metric, NOT a regulatory KPI.
            {
                "kpi_id": "OCC-BSA-KPI-001",
                "regulation_id": "BSA-DUALCTRL-001",
                "parent_requirement_id": "BSA-DUALCTRL-001-R1",
                "authority_type": "INTERNAL_CONTROL",
                "kpi_name": "Wire Transfer Dual Approval Compliance Rate (Internal Control)",
                "kpi_formula": "(Count of wires >= dual-control threshold with 2 approvals / Total wires >= threshold) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 99.0,
                "criticality": "high",
            },
            {
                "kpi_id": "OCC-BSA-KPI-002",
                "regulation_id": "BSA-DUALCTRL-001",
                "parent_requirement_id": "BSA-DUALCTRL-001-R2",
                "authority_type": "INTERNAL_CONTROL",
                "kpi_name": "Same-Person Approval Violations (Internal Control)",
                "kpi_formula": "Count of wires with approver1_id = approver2_id",
                "target_value": 0,
                "target_unit": "count",
                "monitoring_frequency": "real-time",
                "alert_threshold": 1,
                "criticality": "critical",
            },
            # NEW — Travel Rule Field Completeness (LAW). The real
            # regulatory KPI for wire compliance; measures the $3K band the
            # prior corpus was silently skipping.
            {
                "kpi_id": "BSA-TRAVEL-KPI-001",
                "regulation_id": "BSA-TRAVEL-001",
                "parent_requirement_id": "BSA-TRAVEL-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.410(f)",
                "kpi_name": "Travel Rule Field Completeness",
                "kpi_formula": "(transmittals >= $3,000 with all required fields / total transmittals >= $3,000) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 99.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "OCC-BSA-KPI-003",
                "regulation_id": "OCC-BSA-CTR-001",
                "parent_requirement_id": "OCC-BSA-CTR-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.311",
                "kpi_name": "CTR Filing Timeliness",
                "kpi_formula": "(CTRs filed within 15 days / Total CTRs due) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "daily",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
            {
                "kpi_id": "OCC-BSA-KPI-004",
                "regulation_id": "OCC-BSA-CTR-001",
                "parent_requirement_id": "OCC-BSA-CTR-001-R2",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.311",
                "kpi_name": "Structuring Alerts Generated",
                "kpi_formula": "Count of structuring pattern detections per month",
                "target_value": None,
                "target_unit": "count",
                "monitoring_frequency": "monthly",
                "alert_threshold": None,
                "criticality": "medium",
            },
            {
                "kpi_id": "OCC-BSA-KPI-005",
                "regulation_id": "OCC-BSA-CDD-001",
                "parent_requirement_id": "OCC-BSA-CDD-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.230(d)(1)",
                "kpi_name": "Beneficial Ownership Compliance Rate",
                "kpi_formula": "(Legal entity accounts with 25%+ BO on file / Total legal entity accounts) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 98.0,
                "criticality": "high",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            # ── Wire dual-control (INTERNAL_CONTROL — advisory) ──
            {
                "scenario_id": "TC-001",
                "regulation_id": "BSA-DUALCTRL-001",
                "parent_requirement_id": "BSA-DUALCTRL-001-R1",
                "authority_type": "INTERNAL_CONTROL",
                "scenario_name": "Wire transfer at bank threshold requires dual approval (internal control)",
                "priority": "critical",
                "given": "User is authorized wire transfer initiator; bank dual-control threshold configured at $10,000",
                "when_step": "User initiates wire transfer of $10,001 to external account",
                "then_step": "System requires second approval per bank policy; status 'Pending Second Approval'. Note: internal-control expectation, not a federal requirement.",
            },
            {
                "scenario_id": "TC-002",
                "regulation_id": "BSA-DUALCTRL-001",
                "parent_requirement_id": "BSA-DUALCTRL-001-R2",
                "authority_type": "INTERNAL_CONTROL",
                "scenario_name": "Same person cannot approve wire twice (internal control)",
                "priority": "critical",
                "given": "Wire transfer $15,000 initiated by User A",
                "when_step": "User A attempts to also approve the same wire transfer",
                "then_step": "System rejects approval: 'Same user cannot initiate and approve'; transaction stays Pending",
            },
            {
                "scenario_id": "TC-003",
                "regulation_id": "BSA-DUALCTRL-001",
                "parent_requirement_id": "BSA-DUALCTRL-001-R1",
                "authority_type": "INTERNAL_CONTROL",
                "scenario_name": "Wire under dual-control threshold — single approval sufficient",
                "priority": "high",
                "given": "User is authorized wire transfer initiator; dual-control threshold $10,000",
                "when_step": "User initiates wire transfer of $9,999",
                "then_step": "System processes with single approval; no second approval required by internal control policy",
            },
            # ── Travel Rule (LAW) — NEW ──
            {
                "scenario_id": "TC-TRAVEL-001",
                "regulation_id": "BSA-TRAVEL-001",
                "parent_requirement_id": "BSA-TRAVEL-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.410(f)",
                "scenario_name": "$3,000 transmittal missing recipient address is rejected",
                "priority": "critical",
                "given": "Wire transfer initiated for $3,500 lacking recipient address",
                "when_step": "System evaluates Travel Rule fields on the transmittal order",
                "then_step": "Order rejected: 'Travel Rule requires recipient address on transmittals >= $3,000 (31 CFR 1010.410(f))'",
            },
            # ── Recordkeeping (LAW) — retentions ──
            {
                "scenario_id": "TC-012",
                "regulation_id": "BSA-RECKEEP-001",
                "parent_requirement_id": "BSA-RECKEEP-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1020.410(a)",
                "scenario_name": "Wire transfer audit trail retained 5 years",
                "priority": "high",
                "given": "Wire transfer >= $3,000 processed 4 years ago",
                "when_step": "Compliance officer searches for historical wire transfer",
                "then_step": "Full audit trail available including originator/beneficiary, timestamps, amounts (5-year retention per 31 CFR 1020.410(a))",
            },
            # ── CTR (LAW) ──
            {
                "scenario_id": "TC-004",
                "regulation_id": "OCC-BSA-CTR-001",
                "parent_requirement_id": "OCC-BSA-CTR-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.311",
                "scenario_name": "CTR auto-generated for $10,000+ cash deposit",
                "priority": "critical",
                "given": "Customer makes cash deposit at branch",
                "when_step": "Teller processes $12,000 cash deposit",
                "then_step": "System auto-generates CTR with customer info; filing deadline set to 15 calendar days",
            },
            {
                "scenario_id": "TC-005",
                "regulation_id": "OCC-BSA-CTR-001",
                "parent_requirement_id": "OCC-BSA-CTR-001-R2",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.311",
                "scenario_name": "Structuring detection — multiple deposits same day",
                "priority": "critical",
                "given": "Customer deposits $6,000 cash at 10 AM",
                "when_step": "Same customer deposits $5,000 cash at 3 PM (same day, aggregate $11,000)",
                "then_step": "System flags structuring alert; SAR referral auto-generated",
            },
            # ── CDD (LAW) — 25% now, not 20% ──
            {
                "scenario_id": "TC-006",
                "regulation_id": "OCC-BSA-CDD-001",
                "parent_requirement_id": "OCC-BSA-CDD-001-R1",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.230(d)(1)",
                "scenario_name": "Beneficial ownership collected for new LLC account (25% + control prong)",
                "priority": "high",
                "given": "New LLC opens business checking account",
                "when_step": "Account application submitted without beneficial ownership information",
                "then_step": "System blocks account opening: 'Ownership prong (>=25%, up to 4 individuals) and control prong required for legal entities'",
            },
            {
                "scenario_id": "TC-017",
                "regulation_id": "OCC-BSA-CDD-001",
                "parent_requirement_id": "OCC-BSA-CDD-001-R2",
                "authority_type": "LAW",
                "citation": "31 C.F.R. § 1010.230",
                "scenario_name": "Risk rating updated on negative news",
                "priority": "medium",
                "given": "Customer 'Acme Corp' has risk rating 'low'",
                "when_step": "Negative news alert received for 'Acme Corp' (fraud allegation)",
                "then_step": "Risk rating auto-escalated to 'high'; enhanced monitoring initiated",
            },
        ]
