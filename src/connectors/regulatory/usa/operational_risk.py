"""
Operational Risk Connector — REQ-008
Covers OCC Operational Risk handbook and Basel II event type taxonomy.

Sources:
  https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/operational-risk/index-operational-risk.html
  Basel II event categories (static — defined in OCC/FRB guidance)
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from src.connectors.base import RegulatoryConnector

logger = structlog.get_logger(__name__)

# Seven Basel II operational loss event type categories (OCC/FRB guidance, stable)
_BASEL_EVENT_CATEGORIES = [
    ("INTERNAL_FRAUD",       "Internal Fraud",        "Losses due to acts intended to defraud by internal parties"),
    ("EXTERNAL_FRAUD",       "External Fraud",        "Losses due to acts intended to defraud by third parties"),
    ("EMPLOYMENT_PRACTICES", "Employment Practices and Workplace Safety", "Losses arising from employment law violations"),
    ("CLIENTS_PRODUCTS",     "Clients, Products and Business Practices",  "Losses from unintentional or negligent failure to meet professional obligations"),
    ("DAMAGE_ASSETS",        "Damage to Physical Assets", "Losses from natural disasters or other events damaging physical assets"),
    ("BUSINESS_DISRUPTION",  "Business Disruption and System Failures", "Losses from disruptions to business or system failures"),
    ("EXECUTION_DELIVERY",   "Execution, Delivery and Process Management", "Losses from failed transaction processing or process management"),
]

_DEFAULT_URLS = {
    "occ_op_risk": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/operational-risk/index-operational-risk.html",
    "occ_op_risk_pdf": "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/operational-risk/pub-ch-operational-risk.pdf",
    # FRB folded operational_risk into the Operational Resilience topic page
    # during the 2024 supervision-pages reorg; old path 404s.
    "frb_operational": "https://www.federalreserve.gov/supervisionreg/topics/operational-resilience.htm",
}


class OperationalRiskConnector(RegulatoryConnector):
    """Operational Risk RCSA connector.

    Covers:
      - Seven Basel II event type categories for loss event capture
      - RCSA (Risk and Control Self-Assessment): annual for all LOBs, semi-annual for high-risk
      - Key Risk Indicators (KRI): minimum 5 per business line
      - Loss event capture threshold: $10K
      - Operational risk appetite statement and board oversight
    """

    connector_name = "operational_risk"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}

    def connect(self) -> bool:
        self.connected = True
        logger.info("operational_risk_connector_ready")
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
            logger.warning("operational_risk_live_scrape_failed", error=str(exc))
        return result

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            make_session, scrape_html_page, scrape_pdf_url,
        )
        session = make_session()
        docs: List[Dict] = []
        docs.extend(scrape_html_page(session, self.urls["occ_op_risk"], source="OCC", category="Operational Risk Handbook"))
        docs.extend(scrape_pdf_url(session, self.urls["occ_op_risk_pdf"], source="OCC", category="Operational Risk PDF"))
        return docs

    def _build_regulations(self) -> List[Dict[str, Any]]:
        # Item 11 — all GUIDANCE. OCC Operational Risk Handbook + Basel II
        # taxonomy + FRB SR letters are supervisory references, not CFR.
        now = datetime.now(timezone.utc).isoformat()
        regs = []

        for code, name, description in _BASEL_EVENT_CATEGORIES:
            regs.append({
                "regulation_id": f"OPRISK-{code}-001",
                "regulation_name": f"Operational Loss Event — {name}",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook; Basel II Framework (BIS 2006)",
                "rule_text": f"Institutions are expected to capture, categorize, and report operational "
                             f"loss events classified as '{name}': {description}. Supervisory reference: "
                             f"events at or above $10,000 entered in the loss event database within "
                             f"5 business days. Handbook expectation, not a codified deadline.",
                "category": "Operational Risk",
                "subcategory": f"Basel Event Category: {name}",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_op_risk"],
                "last_updated": now,
            })

        regs.extend([
            {
                "regulation_id": "OPRISK-RCSA-001",
                "regulation_name": "Risk and Control Self-Assessment — Annual Frequency (supervisory reference)",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section III",
                "rule_text": "Institutions are expected to conduct Risk and Control Self-Assessments "
                             "(RCSA) for all business lines annually, with semi-annual RCSA for "
                             "high-risk business lines. Results are reported to senior management "
                             "and board risk committee.",
                "category": "Operational Risk",
                "subcategory": "RCSA",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_op_risk"],
                "last_updated": now,
            },
            {
                "regulation_id": "OPRISK-KRI-001",
                "regulation_name": "Key Risk Indicators — Supervisory Reference Minimum",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section IV",
                "rule_text": "Institutions are expected to establish Key Risk Indicators (KRIs) per "
                             "material business line — supervisory reference is at least 5 per line — "
                             "with defined thresholds triggering escalation to senior management "
                             "when breached. Handbook expectation, not a codified requirement.",
                "category": "Operational Risk",
                "subcategory": "KRI Monitoring",
                "regulator": "OCC",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["occ_op_risk"],
                "last_updated": now,
            },
            {
                "regulation_id": "OPRISK-APPETITE-001",
                "regulation_name": "Operational Risk Appetite Statement",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FRB SR letters on operational risk / operational resilience",
                "rule_text": "Board is expected to approve an operational risk appetite statement "
                             "defining the types and aggregate level of operational risk the bank is "
                             "willing to accept, with quantitative limits aligned to capital allocation.",
                "category": "Operational Risk",
                "subcategory": "Governance",
                "regulator": "FRB",
                "geography": "usa",
                "effective_date": None,
                "exam_frequency": "annual",
                "source_url": self.urls["frb_operational"],
                "last_updated": now,
            },
        ])
        return regs

    def _build_requirements(self) -> List[Dict[str, Any]]:
        reqs = []

        for code, name, _ in _BASEL_EVENT_CATEGORIES:
            reqs.append({
                "requirement_id": f"OPRISK-{code}-001-R1",
                "regulation_id": f"OPRISK-{code}-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook; Basel II Framework",
                "requirement_text": f"Institution should capture all operational loss events classified "
                                    f"as '{name}' with amount at or above the $10,000 supervisory "
                                    f"reference in the loss event database within 5 business days of "
                                    f"discovery, with root cause and recovery amount documented.",
                "rule_type": "loss_event_reporting",
                "threshold_amount": 10_000.0,
                "test_requirement": f"Verify loss event database captures {name} events >= $10K within 5 days",
                "parameters": {
                    "loss_category": code,
                    "threshold_usd": 10_000,
                    "capture_deadline_business_days": 5,
                },
            })

        reqs.extend([
            {
                "requirement_id": "OPRISK-RCSA-001-R1",
                "regulation_id": "OPRISK-RCSA-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section III.A",
                "requirement_text": "Institution should complete RCSA for all material business lines "
                                    "annually, and semi-annually for any business line rated high-risk, "
                                    "with documented action plans for gaps identified.",
                "rule_type": "rcsa_requirement",
                "threshold_amount": None,
                "test_requirement": "Verify RCSA completion records for all business lines with dates",
                "parameters": {
                    "standard_frequency": "annual",
                    "high_risk_frequency": "semi_annual",
                    "action_plan_required": True,
                },
            },
            {
                "requirement_id": "OPRISK-KRI-001-R1",
                "regulation_id": "OPRISK-KRI-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section IV.B",
                "requirement_text": "Institution should establish KRIs per material business line — "
                                    "supervisory reference minimum 5 — with defined green/amber/red "
                                    "thresholds and escalation procedures documented in the operational "
                                    "risk framework.",
                "rule_type": "kri_monitoring",
                "threshold_amount": 5.0,
                "test_requirement": "Verify each business line has >= 5 KRIs with documented thresholds",
                "parameters": {"min_kris_per_lob": 5, "threshold_levels": ["green", "amber", "red"]},
            },
        ])
        return reqs

    def _build_kpis(self) -> List[Dict[str, Any]]:
        return [
            {
                "kpi_id": "OPRISK-KPI-001",
                "regulation_id": "OPRISK-RCSA-001",
                "parent_requirement_id": "OPRISK-RCSA-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section III.A",
                "kpi_name": "RCSA Completion Rate",
                "description": "Percentage of business lines with current (within cycle) completed RCSA",
                "threshold": 100.0,
                "threshold_type": "minimum_percent",
                "unit": "percent",
            },
            {
                "kpi_id": "OPRISK-KPI-002",
                "regulation_id": "OPRISK-KRI-001",
                "parent_requirement_id": "OPRISK-KRI-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section IV.B",
                "kpi_name": "KRI Coverage per Business Line",
                "description": "Minimum number of active KRIs per material business line (supervisory reference: >=5)",
                "threshold": 5.0,
                "threshold_type": "minimum_count",
                "unit": "count",
            },
            {
                "kpi_id": "OPRISK-KPI-003",
                "regulation_id": "OPRISK-EXECUTION_DELIVERY-001",
                "parent_requirement_id": "OPRISK-EXECUTION_DELIVERY-001-R1",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook",
                "kpi_name": "Loss Event Capture Timeliness",
                "description": "Percentage of loss events at or above $10K captured within 5 business days",
                "threshold": 95.0,
                "threshold_type": "minimum_percent",
                "unit": "percent",
            },
            {
                "kpi_id": "OPRISK-KPI-004",
                "regulation_id": "OPRISK-APPETITE-001",
                "parent_requirement_id": None,
                "authority_type": "GUIDANCE",
                "authority_source": "FRB SR letters on operational risk / operational resilience",
                "kpi_name": "Operational Risk Appetite Breaches",
                "description": "Number of board-approved operational risk limit breaches in the period",
                "threshold": 0.0,
                "threshold_type": "maximum_count",
                "unit": "count",
            },
        ]

    def _build_test_scenarios(self) -> List[Dict[str, Any]]:
        return [
            {
                "scenario_id": "TC-OPRISK-001",
                "regulation_id": "OPRISK-RCSA-001",
                "parent_requirement_id": "OPRISK-RCSA-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section III.A",
                "scenario_name": "RCSA completed for all material business lines within cycle",
                "priority": "critical",
                "given": "Bank has 8 material business lines including 2 rated high-risk",
                "when_step": "OCC examiner requests RCSA completion records",
                "then_step": "System produces RCSA reports for all 8 lines with dates — standard lines "
                             "completed within 12 months, high-risk lines within 6 months",
            },
            {
                "scenario_id": "TC-OPRISK-002",
                "regulation_id": "OPRISK-EXECUTION_DELIVERY-001",
                "parent_requirement_id": "OPRISK-EXECUTION_DELIVERY-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook",
                "scenario_name": "Operational loss event captured within 5 business days",
                "priority": "high",
                "given": "A $25,000 processing error is discovered on Monday",
                "when_step": "Operations staff enters the loss event in the database",
                "then_step": "Event is categorized as Execution/Delivery, amount recorded as $25,000, "
                             "root cause documented, and entry timestamp <= 5 business days from discovery",
            },
            {
                "scenario_id": "TC-OPRISK-003",
                "regulation_id": "OPRISK-KRI-001",
                "parent_requirement_id": "OPRISK-KRI-001-R1",
                "regulator": "OCC",
                "authority_type": "GUIDANCE",
                "authority_source": "OCC Operational Risk Handbook Section IV.B",
                "scenario_name": "KRI breach triggers escalation to senior management",
                "priority": "high",
                "given": "Retail banking KRI for failed transaction rate has a red threshold of 2%",
                "when_step": "Failed transaction rate reaches 2.1%",
                "then_step": "System generates amber/red alert, escalation email sent to Risk Officer "
                             "and COO within 1 business day, incident logged in KRI tracking system",
            },
            {
                "scenario_id": "TC-OPRISK-004",
                "regulation_id": "OPRISK-APPETITE-001",
                "parent_requirement_id": None,
                "regulator": "FRB",
                "authority_type": "GUIDANCE",
                "authority_source": "FRB SR letters on operational risk / operational resilience",
                "scenario_name": "Board operational risk appetite statement current and approved",
                "priority": "high",
                "given": "Bank has a board-approved operational risk framework",
                "when_step": "Examiner requests operational risk appetite documentation",
                "then_step": "Bank produces board-approved appetite statement dated within 12 months "
                             "with quantitative limits per business line and loss event category",
            },
        ]
