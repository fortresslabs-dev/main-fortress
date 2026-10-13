"""
CECL Connector — Current Expected Credit Loss / Credit Risk Model Governance (REQ-006)
Covers OCC 2011-12 / SR 11-7 model risk management and FASB ASC 326 implementation.

Sources:
  https://www.occ.gov/news-issuances/bulletins/2011/bulletin-2011-12.html
  https://www.occ.gov/news-issuances/bulletins/2020/bulletin-2020-51.html
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

_DEFAULT_URLS = {
    "occ_model_risk": "https://www.occ.gov/news-issuances/bulletins/2011/bulletin-2011-12.html",
    "occ_acl_policy": "https://www.occ.gov/news-issuances/bulletins/2020/bulletin-2020-51.html",
    "occ_cecl_guide": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/credit-risk/index-credit-risk.html",
    "fdic_cecl": "https://www.fdic.gov/regulations/examinations/credit-risk/cecl.html",
    # FRB retired /supervisionreg/topics/cecl.htm in the 2024 supervision-pages
    # reorg; CECL guidance now lives on the accounting topic page.
    "frb_cecl": "https://www.federalreserve.gov/supervisionreg/topics/accounting.htm",
}


class CeclConnector(RegulatoryConnector):
    """CECL / Credit Risk Model Governance connector.

    Covers:
      - Model inventory and documentation (OCC 2011-12 / SR 11-7)
      - Independent model validation (annual frequency)
      - CECL governance: ALLL → ACL transition, model backtesting
      - Loss estimation methodology documentation
      - Board / ALCO oversight requirements
    """

    connector_name = "cecl"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("cecl_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        return True

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        result: Dict[str, List[Dict]] = {
            "regulations": self._build_regulations(),
            "requirements": self._build_requirements(),
            "kpis": self._build_kpis(),
            "test_scenarios": self._build_test_scenarios(),
            "scraped_documents": [],
        }
        try:
            result["scraped_documents"] = self._scrape_live()
        except Exception as exc:
            logger.warning("cecl_live_scrape_failed", error=str(exc))
        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import make_session, scrape_html_page
        session = make_session()
        docs: List[Dict] = []
        docs.extend(scrape_html_page(session, self.urls["occ_model_risk"], source="OCC", category="Model Risk Management"))
        docs.extend(scrape_html_page(session, self.urls["occ_acl_policy"], source="OCC", category="ACL Policy Statement"))
        docs.extend(scrape_html_page(session, self.urls["occ_cecl_guide"], source="OCC", category="CECL Credit Risk Handbook"))
        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        # Item 11 — every row is GUIDANCE: SR 11-7, OCC Bulletin 2011-12,
        # OCC 2020-51, and FASB ASC 326 are supervisory / accounting-standard
        # references, NOT CFR sections. authority_source populated;
        # cfr_citation=None so migration 046's ck_cr_guidance_forbids_citation
        # check accepts the row. effective_date nulled where the prior value
        # was a bulletin-publish rather than a rule-effective date.
        now = datetime.now(timezone.utc).isoformat()
        return [
            {
                "regulation_id": "CECL-MODEL-001",
                "regulation_name": "Model Risk Management — Inventory and Documentation",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12; SR 11-7",
                "rule_text": "Banks are expected to maintain a comprehensive model inventory with "
                             "documentation covering purpose, inputs, assumptions, limitations, and "
                             "validation status for each model used in credit loss estimation.",
                "category": "CECL",
                "subcategory": "Model Governance",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_model_risk"],
                "last_updated": now,
            },
            {
                "regulation_id": "CECL-VALIDATION-001",
                "regulation_name": "Independent Model Validation — Annual Expectation",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12; SR 11-7",
                "rule_text": "All credit loss models, including CECL allowance models, are expected "
                             "to undergo independent validation at least annually by qualified staff "
                             "not involved in model development or use. Supervisory expectation.",
                "category": "CECL",
                "subcategory": "Model Validation",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_model_risk"],
                "last_updated": now,
            },
            {
                "regulation_id": "CECL-ACL-001",
                "regulation_name": "Allowance for Credit Losses — FASB ASC 326 Implementation",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FASB ASC 326; Interagency Policy Statement 2020 on the ACL",
                "rule_text": "Banks measure the allowance for credit losses (ACL) using the current "
                             "expected credit loss methodology under FASB ASC 326, estimating "
                             "lifetime expected credit losses on financial assets held at amortized "
                             "cost. FASB standard, not a CFR section.",
                "category": "CECL",
                "subcategory": "ACL Methodology",
                "regulator": "FDIC",
                "geography": "usa",
                # 2023-01-01 is the FASB ASC 326 phase-in date for smaller
                # reporting companies — a real effective date, kept.
                "effective_date": "2023-01-01",
                "exam_frequency": "annual",
                "source_url": self.urls["occ_acl_policy"],
                "last_updated": now,
            },
            {
                "regulation_id": "CECL-GOVERNANCE-001",
                "regulation_name": "Board and Senior Management Oversight of CECL",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2020-51; OCC Bulletin 2011-12",
                "rule_text": "Board of directors and senior management are expected to actively "
                             "oversee CECL model governance, including approving CECL methodology, "
                             "reviewing validation results, and ensuring adequate resources for "
                             "model risk management.",
                "category": "CECL",
                "subcategory": "Board Oversight",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_acl_policy"],
                "last_updated": now,
            },
            {
                "regulation_id": "CECL-BACKTESTING-001",
                "regulation_name": "CECL Model Backtesting and Benchmarking",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "SR 11-7; Interagency Policy Statement 2020",
                "rule_text": "Banks are expected to perform periodic backtesting comparing CECL "
                             "model predictions to actual credit loss outcomes, and to benchmark "
                             "model results against peer institutions and alternative estimation "
                             "approaches.",
                "category": "CECL",
                "subcategory": "Model Validation",
                "regulator": "FRB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["frb_cecl"],
                "last_updated": now,
            },
        ]

    def _build_requirements(self) -> List[Dict[str, Any]]:
        # All requirements are GUIDANCE-derived. Bulletin/policy references
        # live in authority_source; cfr_citation stays None.
        return [
            {
                "requirement_id": "CECL-MODEL-001-R1",
                "regulation_id": "CECL-MODEL-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 Section II.A",
                "requirement_text": "Bank should maintain a complete model inventory listing each "
                                    "model used in credit loss estimation with unique identifiers, "
                                    "owner, purpose, and current validation status.",
                "rule_type": "model_validation",
                "threshold_amount": None,
                "test_requirement": "Verify model inventory exists, is current, and covers all CECL models",
                "parameters": {"update_frequency": "at_least_annual", "includes_third_party_models": True},
            },
            {
                "requirement_id": "CECL-VALIDATION-001-R1",
                "regulation_id": "CECL-VALIDATION-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 Section III",
                "requirement_text": "Bank should conduct independent validation of CECL allowance "
                                    "model at least annually, covering conceptual soundness, data "
                                    "integrity, outcomes analysis, and sensitivity testing.",
                "rule_type": "model_validation",
                "threshold_amount": 1.0,
                "test_requirement": "Verify annual independent validation report exists and covers all components",
                "parameters": {"frequency_years": 1, "validator_independence_required": True},
            },
            {
                "requirement_id": "CECL-ACL-001-R1",
                "regulation_id": "CECL-ACL-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FASB ASC 326-20-30",
                "requirement_text": "Bank estimates the allowance for credit losses (ACL) using "
                                    "reasonable and supportable forecasts over the contractual life "
                                    "of each financial asset, reverting to historical loss rates "
                                    "beyond the forecast horizon.",
                "rule_type": "cecl_governance",
                "threshold_amount": None,
                "test_requirement": "Verify CECL methodology uses lifetime loss estimation with documented forecast horizon",
                "parameters": {"methodology": "lifetime_expected_loss", "reversion_required": True},
            },
            {
                "requirement_id": "CECL-GOVERNANCE-001-R1",
                "regulation_id": "CECL-GOVERNANCE-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2020-51 Section IV",
                "requirement_text": "Board should annually review and approve CECL methodology, "
                                    "model validation results, and ACL adequacy assessment, with "
                                    "minutes documenting the review.",
                "rule_type": "cecl_governance",
                "threshold_amount": None,
                "test_requirement": "Verify board minutes include annual CECL methodology review",
                "parameters": {"review_frequency": "annual", "board_approval_required": True},
            },
            {
                "requirement_id": "CECL-BACKTESTING-001-R1",
                "regulation_id": "CECL-BACKTESTING-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "SR 11-7 Section IV.C",
                "requirement_text": "Bank should perform CECL model backtesting comparing predicted "
                                    "loss rates to realized outcomes at least annually, documenting "
                                    "significant deviations and remediation actions.",
                "rule_type": "model_validation",
                "threshold_amount": None,
                "test_requirement": "Verify annual backtesting documentation with deviation analysis",
                "parameters": {"backtesting_frequency": "annual", "deviation_threshold_pct": 10},
            },
        ]

    def _build_kpis(self) -> List[Dict[str, Any]]:
        # C-11 — KPIs carry parent_requirement_id + authority_source.
        return [
            {
                "kpi_id": "CECL-KPI-001",
                "regulation_id": "CECL-VALIDATION-001",
                "parent_requirement_id": "CECL-VALIDATION-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 Section III",
                "kpi_name": "Model Validation Coverage",
                "description": "Percentage of CECL models with current (≤12 months) independent validation",
                "threshold": 100.0,
                "threshold_type": "minimum_percent",
                "unit": "percent",
            },
            {
                "kpi_id": "CECL-KPI-002",
                "regulation_id": "CECL-MODEL-001",
                "parent_requirement_id": "CECL-MODEL-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 Section II.A",
                "kpi_name": "Model Inventory Completeness",
                "description": "All models in production listed in model inventory with current status",
                "threshold": 100.0,
                "threshold_type": "minimum_percent",
                "unit": "percent",
            },
            {
                "kpi_id": "CECL-KPI-003",
                "regulation_id": "CECL-BACKTESTING-001",
                "parent_requirement_id": "CECL-BACKTESTING-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "SR 11-7 Section IV.C",
                "kpi_name": "ACL Backtesting Deviation",
                "description": "Maximum acceptable deviation between predicted and realized loss rates",
                "threshold": 10.0,
                "threshold_type": "maximum_percent",
                "unit": "percent",
            },
        ]

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        # C-11/C-12 — TCs carry parent + regulator + authority_source.
        return [
            {
                "scenario_id": "TC-CECL-001",
                "regulation_id": "CECL-VALIDATION-001",
                "parent_requirement_id": "CECL-VALIDATION-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 Section III",
                "scenario_name": "CECL model validation report current and complete",
                "priority": "critical",
                "given": "Bank uses CECL model for allowance estimation",
                "when_step": "OCC examiner requests model validation documentation",
                "then_step": "Bank produces independent validation report dated within 12 months "
                             "covering conceptual soundness, data integrity, and outcomes analysis",
            },
            {
                "scenario_id": "TC-CECL-002",
                "regulation_id": "CECL-MODEL-001",
                "parent_requirement_id": "CECL-MODEL-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2011-12 Section II.A",
                "scenario_name": "Model inventory includes all CECL-related models",
                "priority": "high",
                "given": "Bank operates multiple credit risk models",
                "when_step": "Examiner requests model inventory",
                "then_step": "Inventory lists all models with unique IDs, owners, validation dates, "
                             "and no orphaned models in production",
            },
            {
                "scenario_id": "TC-CECL-003",
                "regulation_id": "CECL-ACL-001",
                "parent_requirement_id": "CECL-ACL-001-R1",
                "regulator": "FDIC",
                "authority_type": "GUIDANCE",
                "authority_source": "FASB ASC 326-20-30",
                "scenario_name": "CECL methodology uses lifetime loss estimation",
                "priority": "critical",
                "given": "Bank has transitioned from incurred-loss to CECL",
                "when_step": "Examiner reviews ACL methodology documentation",
                "then_step": "Documentation demonstrates lifetime expected credit loss estimation "
                             "with documented forecast horizon and historical reversion approach",
            },
            {
                "scenario_id": "TC-CECL-004",
                "regulation_id": "CECL-GOVERNANCE-001",
                "parent_requirement_id": "CECL-GOVERNANCE-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Bulletin 2020-51 Section IV",
                "scenario_name": "Board minutes reflect annual CECL oversight",
                "priority": "high",
                "given": "Board meets quarterly",
                "when_step": "Examiner reviews board minutes for CECL governance evidence",
                "then_step": "At least one annual board meeting documents CECL methodology review, "
                             "validation result review, and ACL adequacy approval",
            },
        ]
