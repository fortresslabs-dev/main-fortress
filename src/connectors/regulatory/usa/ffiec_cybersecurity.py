"""
FFIEC Cybersecurity Connector — REQ-003
FFIEC Cybersecurity Assessment Tool (CAT) + IT Examination Handbook.

Sources: ffiec.gov/cybersecurity.htm, ffiec.gov/examinations/it_examination_handbooks.htm

LIVE SCRAPE STATUS — GUIDANCE-only, no eCFR path.
The IT Handbook host is CF-gated with a JS challenge and has no archive.org
snapshots (verified 2026-05-17); _fetch_html short-circuits via
_NO_SCRAPE_HOSTS. www.ffiec.gov pages may resolve via the Wayback Machine
fallback when a snapshot exists; otherwise the connector relies on
_build_regulations() / _build_requirements() for CAT domains, IT booklet
structure, and NIST CSF mappings.

Item 6 of docs/correction_smb.md (§11.4) called for replacing the
hardcoded fallbacks with eCFR fetches. That applies to ffiec_bsa.py,
which anchors LAW rows to 31 CFR Part 1010/1020. It does NOT apply
here: every row this connector emits is authority_type=GUIDANCE
(CAT, IT Handbook booklets, MFA guidance) with cfr_citation=None. The
underlying enforceable law — Interagency Guidelines Establishing
Information Security Standards, 12 CFR 30 App B / 364 App B / 208 App
D-2 — is not yet in the corpus; it is tracked as new_rule_addition.md
§GLBA-SEC-001 and belongs in its own connector. Adding an eCFR fetch
here would fabricate authority for GUIDANCE rows, which is exactly the
C-1 / C-6 class the §11.3 hard gate is meant to prevent.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "ffiec_cyber_main": "https://www.ffiec.gov/cybersecurity.htm",
    "ffiec_cat_pdf": "https://www.ffiec.gov/pdf/cybersecurity/FFIEC_CAT_May_2017.pdf",
    "ffiec_it_handbook": "https://ithandbook.ffiec.gov/",
    "ffiec_it_information_security": "https://ithandbook.ffiec.gov/it-booklets/information-security.aspx",
    "ffiec_it_business_continuity": "https://ithandbook.ffiec.gov/it-booklets/business-continuity-management.aspx",
    "ffiec_it_management": "https://ithandbook.ffiec.gov/it-booklets/management.aspx",
    "ffiec_it_operations": "https://ithandbook.ffiec.gov/it-booklets/operations.aspx",
    "ffiec_it_development": "https://ithandbook.ffiec.gov/it-booklets/development-acquisition.aspx",
    "nist_csf": "https://www.nist.gov/cyberframework",
    "occ_cyber_bulletins": "https://www.occ.gov/topics/supervision-and-examination/bank-operations/cybersecurity/index-cybersecurity.html",
}


class FfiecCybersecurityConnector(RegulatoryConnector):
    """FFIEC Cybersecurity Assessment Tool (CAT) and IT Handbook connector.

    Covers:
      - FFIEC CAT: Inherent Risk Profile + Cybersecurity Maturity (5 domains × 5 levels)
      - IT Booklets: Information Security, Business Continuity, Management
      - NIST CSF mapping to FFIEC domains
      - OCC cybersecurity bulletins (vendor risk, ransomware, etc.)
      - Exam procedures for cyber risk assessment
    """

    connector_name = "ffiec_cybersecurity"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("ffiec_cybersecurity_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("ffiec_cybersecurity_extraction_started")
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
            logger.warning("ffiec_cybersecurity_live_scrape_failed", error=str(exc))

        logger.info(
            "ffiec_cybersecurity_extraction_completed",
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

        session = make_session(self.urls["ffiec_cyber_main"])
        docs: List[Dict] = []

        for url, category in [
            (self.urls["ffiec_cyber_main"], "FFIEC Cybersecurity"),
            (self.urls["ffiec_it_information_security"], "Information Security Booklet"),
            (self.urls["ffiec_it_business_continuity"], "Business Continuity Booklet"),
            (self.urls["ffiec_it_management"], "IT Management Booklet"),
            (self.urls["occ_cyber_bulletins"], "OCC Cybersecurity Bulletins"),
        ]:
            docs.extend(scrape_html_page(session, url, source="FFIEC", category=category))

        docs.extend(
            scrape_pdf_url(
                session, self.urls["ffiec_cat_pdf"],
                source="FFIEC", category="Cybersecurity Assessment Tool",
            )
        )

        for doc in docs:
            enrich_document(doc, regulation_id="FFIEC-CYBER", doc_type="examination")

        return docs

    # ------------------------------------------------------------------ #
    # Regulations
    # ------------------------------------------------------------------ #

    def _build_regulations(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        # ALL rows in this connector are GUIDANCE, not law. FFIEC IT
        # Handbook booklets and the CAT are supervisory tools; the
        # enforceable law lives at 12 CFR 30 App B / 364 App B / 208 App
        # D-2 (Interagency Guidelines Establishing Information Security
        # Standards, GLBA §501(b)). See new_rule_addition.md §GLBA-SEC-001.
        # authority_type=GUIDANCE prevents the §11.3 hard gate from
        # attaching a fabricated CFR cite in the Test Factory / examiner
        # package.
        return [
            {
                "regulation_id": "FFIEC-CAT-001",
                "regulation_name": "FFIEC Cybersecurity Assessment Tool (CAT)",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Cybersecurity Assessment Tool (May 2017)",
                # FFIEC sunset the CAT in Aug 2024, pointing institutions
                # to NIST CSF / CRI Profile. Kept for legacy visibility
                # with verify_before_display so the UI surfaces a
                # sunset warning before an examiner sees it.
                "verify_before_display": True,
                "category": "Cybersecurity",
                "subcategory": "Risk Assessment",
                "regulator": "FFIEC",
                "geography": "usa",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["ffiec_cat_pdf"],
                "last_updated": now,
            },
            {
                "regulation_id": "FFIEC-ITSEC-001",
                "regulation_name": "FFIEC IT Handbook — Information Security Booklet",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Examination Handbook — Information Security Booklet",
                "category": "Cybersecurity",
                "subcategory": "Information Security",
                "regulator": "FFIEC",
                "geography": "usa",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["ffiec_it_information_security"],
                "last_updated": now,
            },
            {
                "regulation_id": "FFIEC-BCM-001",
                "regulation_name": "FFIEC IT Handbook — Business Continuity Management",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Examination Handbook — Business Continuity Management Booklet",
                "category": "Cybersecurity",
                "subcategory": "Business Continuity",
                "regulator": "FFIEC",
                "geography": "usa",
                "jurisdiction_level": "federal",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["ffiec_it_business_continuity"],
                "last_updated": now,
            },
        ]

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #

    def _build_requirements(self) -> List[Dict[str, Any]]:
        # All requirements below are GUIDANCE. The specific patch SLAs
        # (30/90 day) and RTO figures (≤ 4h) are illustrative — not
        # codified deadlines. authority_type=GUIDANCE and citation=None
        # keep the §11.3 hard gate happy without erasing the controls.
        return [
            {
                "requirement_id": "FFIEC-CAT-IRP-001",
                "regulation_id": "FFIEC-CAT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Cybersecurity Assessment Tool — Inherent Risk Profile",
                "requirement_text": (
                    "Institution should complete an Inherent Risk Profile (IRP) assessing "
                    "five categories: Technologies & Connection Types, Delivery Channels, "
                    "Online/Mobile Products & Technology Services, Organizational Characteristics, "
                    "External Threats. Result: Least / Minimal / Moderate / Significant / Most inherent risk."
                ),
                "rule_type": "risk_assessment",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify IRP completed annually; all five risk categories scored",
                "parameters": {
                    "irp_categories": [
                        "technologies_connection_types",
                        "delivery_channels",
                        "online_mobile_products",
                        "organizational_characteristics",
                        "external_threats",
                    ],
                    "risk_levels": ["Least", "Minimal", "Moderate", "Significant", "Most"],
                    "review_frequency": "annual",
                },
            },
            {
                "requirement_id": "FFIEC-CAT-MATURITY-001",
                "regulation_id": "FFIEC-CAT-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Cybersecurity Assessment Tool — Maturity Levels",
                "requirement_text": (
                    "Cybersecurity Maturity assessed across five domains: "
                    "Cyber Risk Management & Oversight, Threat Intelligence & Collaboration, "
                    "Cybersecurity Controls, External Dependency Management, "
                    "Cyber Incident Management & Resilience. "
                    "Maturity levels: Baseline | Evolving | Intermediate | Advanced | Innovative."
                ),
                "rule_type": "maturity_assessment",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify CAT maturity assessment covers all 5 domains; maturity ≥ inherent risk level",
                "parameters": {
                    "domains": [
                        "cyber_risk_management_oversight",
                        "threat_intelligence_collaboration",
                        "cybersecurity_controls",
                        "external_dependency_management",
                        "cyber_incident_management_resilience",
                    ],
                    "maturity_levels": ["Baseline", "Evolving", "Intermediate", "Advanced", "Innovative"],
                    "minimum_requirement": "maturity_level >= inherent_risk_category",
                },
            },
            {
                "requirement_id": "FFIEC-ITSEC-PATCH-001",
                "regulation_id": "FFIEC-ITSEC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Information Security Booklet (patch management)",
                "requirement_text": (
                    "Institution should maintain a patch management program that identifies, "
                    "prioritizes, tests, and deploys security patches. Reference SLAs from the "
                    "IT Handbook: critical patches within ~30 days, high-severity within ~90 days. "
                    "These are illustrative timeframes, not codified deadlines."
                ),
                "rule_type": "patch_management",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify patch management SLA: critical=30 days, high=90 days from vendor release",
                "parameters": {
                    "critical_patch_days": 30,
                    "high_patch_days": 90,
                    "medium_patch_days": 180,
                    "tracking_required": True,
                },
            },
            {
                "requirement_id": "FFIEC-BCM-RTO-001",
                "regulation_id": "FFIEC-BCM-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Business Continuity Management Booklet",
                "requirement_text": (
                    "Institution should establish and test Recovery Time Objectives (RTO) "
                    "for critical systems. Reference figure from the BCM Booklet: core banking "
                    "and payment systems commonly target RTO ≤ 4 hours. This is illustrative, "
                    "not a codified regulatory limit. Annual BCP testing is a supervisory "
                    "expectation."
                ),
                "rule_type": "business_continuity",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify RTO defined for all critical systems; annual BCP test documented",
                "parameters": {
                    "critical_systems_rto_hours": 4,
                    "bcp_test_frequency": "annual",
                    "test_types": ["tabletop", "parallel", "full_failover"],
                    "documentation_required": True,
                },
            },
            {
                "requirement_id": "FFIEC-ITSEC-MFA-001",
                "regulation_id": "FFIEC-ITSEC-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Supplement to Authentication in an Internet Banking Environment",
                "requirement_text": (
                    "Multi-factor authentication (MFA) expected for privileged user access to "
                    "critical systems, remote access, and customer-facing online banking. "
                    "Single-factor authentication deemed insufficient for high-risk transactions "
                    "per FFIEC Supplement to Authentication Guidance."
                ),
                "rule_type": "access_control",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify MFA enforced for privileged access, remote access, and online banking",
                "parameters": {
                    "mfa_required_for": [
                        "privileged_user_access",
                        "remote_access",
                        "online_banking_high_risk",
                    ],
                    "acceptable_factors": ["password", "hardware_token", "biometric", "push_notification"],
                    "minimum_factors": 2,
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # KPIs
    # ------------------------------------------------------------------ #

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — every KPI carries parent_requirement_id + inherited
        # authority. All FFIEC cyber content is GUIDANCE (see §8 audit) —
        # citation stays None; authority_source names the manual.
        return [
            {
                "kpi_id": "FFIEC-CYBER-KPI-001",
                "regulation_id": "FFIEC-CAT-001",
                "parent_requirement_id": "FFIEC-CAT-MATURITY-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Cybersecurity Assessment Tool (CAT) — sunset Aug-2024",
                "citation": None,
                "kpi_name": "CAT Maturity vs Inherent Risk Gap",
                "kpi_formula": "Count of domains where maturity_level < inherent_risk_category (target 0)",
                "target_value": 0,
                "target_unit": "count",
                "monitoring_frequency": "annual",
                "alert_threshold": 1,
                "criticality": "critical",
            },
            {
                "kpi_id": "FFIEC-CYBER-KPI-002",
                "regulation_id": "FFIEC-ITSEC-001",
                "parent_requirement_id": "FFIEC-ITSEC-PATCH-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Information Security",
                "citation": None,
                "kpi_name": "Critical Patch Compliance Rate",
                "kpi_formula": "(Critical patches applied within 30 days / Total critical patches) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 95.0,
                "criticality": "high",
            },
            {
                "kpi_id": "FFIEC-CYBER-KPI-003",
                "regulation_id": "FFIEC-BCM-001",
                "parent_requirement_id": "FFIEC-BCM-RTO-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Business Continuity Management",
                "citation": None,
                "kpi_name": "BCP Test Completion",
                "kpi_formula": "Annual BCP test completed: 1=Yes, 0=No",
                "target_value": 1,
                "target_unit": "boolean",
                "monitoring_frequency": "annual",
                "alert_threshold": 1,
                "criticality": "high",
            },
            {
                "kpi_id": "FFIEC-CYBER-KPI-004",
                "regulation_id": "FFIEC-ITSEC-001",
                "parent_requirement_id": "FFIEC-ITSEC-MFA-001",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Information Security (MFA)",
                "citation": None,
                "kpi_name": "MFA Coverage Rate",
                "kpi_formula": "(Systems with MFA enforced / Total systems requiring MFA) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "monthly",
                "alert_threshold": 100.0,
                "criticality": "critical",
            },
        ]

    # ------------------------------------------------------------------ #
    # Test Scenarios
    # ------------------------------------------------------------------ #

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # regulator=FFIEC everywhere (was OCC in the prior corpus per C-12).
        # authority_type=GUIDANCE — these are supervisory expectations, not
        # codified law; TC docstrings must not cite a CFR section.
        return [
            {
                "scenario_id": "TC-CYBER-001",
                "regulation_id": "FFIEC-CAT-001",
                "parent_requirement_id": "FFIEC-CAT-MATURITY-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Cybersecurity Assessment Tool",
                "scenario_name": "CAT maturity below inherent risk — examiner finding",
                "priority": "critical",
                "given": "Bank has 'Significant' inherent risk in Threat Intelligence domain",
                "when_step": "CAT maturity assessment shows 'Baseline' for Threat Intelligence domain",
                "then_step": "Examiner finding: 'Maturity (Baseline) insufficient for Significant inherent risk; gap remediation required'",
            },
            {
                "scenario_id": "TC-CYBER-002",
                "regulation_id": "FFIEC-ITSEC-001",
                "parent_requirement_id": "FFIEC-ITSEC-PATCH-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Information Security Booklet",
                "scenario_name": "Critical patch undeployed past internal SLA",
                "priority": "critical",
                "given": "Vendor releases critical security patch for core banking software; bank patch SLA is 30 days",
                "when_step": "Day 31 passes without patch deployment",
                "then_step": "Automated alert: 'Critical patch internal SLA breach'; CISO and IT Security notified. Internal-control expectation, not a codified deadline.",
            },
            {
                "scenario_id": "TC-CYBER-003",
                "regulation_id": "FFIEC-ITSEC-001",
                "parent_requirement_id": "FFIEC-ITSEC-MFA-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC Supplement to Authentication in an Internet Banking Environment",
                "scenario_name": "Privileged access without MFA — access blocked",
                "priority": "critical",
                "given": "Database administrator attempts access to production DB from remote location",
                "when_step": "Login attempted with username/password only (single factor)",
                "then_step": "Access blocked: 'MFA required for privileged remote access'; second factor prompt displayed",
            },
            {
                "scenario_id": "TC-CYBER-004",
                "regulation_id": "FFIEC-BCM-001",
                "parent_requirement_id": "FFIEC-BCM-RTO-001",
                "regulator": "FFIEC",
                "authority_type": "GUIDANCE",
                "authority_source": "FFIEC IT Handbook — Business Continuity Management Booklet",
                "scenario_name": "No BCP test in 14 months — compliance gap",
                "priority": "high",
                "given": "Last BCP test completed 14 months ago",
                "when_step": "Compliance dashboard reviewed",
                "then_step": "Dashboard shows 'BCP test overdue by 2 months'; exam finding risk escalated",
            },
        ]
