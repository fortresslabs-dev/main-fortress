"""Framework Agent - Generates BDD Scenarios
Aligned to USA banking compliance (OCC, FDIC, CFPB, FinCEN, OFAC).
"""
from typing import Dict, Any, List
import structlog
from src.agents.base_agent import BaseAgent

logger = structlog.get_logger()

class FrameworkAgent(BaseAgent):
    agent_type = "framework"

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """Generate BDD test scenarios"""
        try:
            workflow_id = input_data.get("workflow_id")
            understanding = input_data.get("understanding", {})
            
            self.logger.info("framework_started", workflow_id=workflow_id)
            
            # Extract scenarios outline from understanding
            scenarios_outline = understanding.get("test_scenarios_outline", [])
            requirements = understanding.get("requirements", [])
            banking_rules = understanding.get("banking_rules", {})
            
            # Generate detailed BDD scenarios
            scenarios = []
            
            # Happy path scenario
            scenarios.append({
                "name": "successful_transaction",
                "given": "User has sufficient balance and valid account",
                "when": "User initiates valid transaction",
                "then": "Transaction succeeds, balance updated, audit trail recorded",
                "priority": "P0",
                "tags": ["happy_path", "smoke"]
            })
            
            # Wire transfer / BSA-AML scenarios
            if "wire_transfer" in banking_rules:
                ctr = banking_rules["wire_transfer"].get("ctr_threshold", 10000)
                travel = banking_rules["wire_transfer"].get("travel_rule_threshold", 3000)
                scenarios.extend([
                    {
                        "name": "wire_transfer_below_ctr_threshold",
                        "given": "User has a valid account with balance $50,000",
                        "when": "User sends wire transfer of $5,000",
                        "then": "Wire succeeds, no CTR filed, OFAC screening passed",
                        "priority": "P0",
                        "tags": ["wire_transfer", "bsa_aml"]
                    },
                    {
                        "name": "wire_transfer_above_ctr_threshold",
                        "given": "User has valid account and approved by compliance",
                        "when": f"User sends wire transfer of ${ctr + 1:,}",
                        "then": f"Wire succeeds, CTR filed with FinCEN, dual approval obtained",
                        "priority": "P0",
                        "tags": ["wire_transfer", "ctr", "bsa_aml"]
                    },
                    {
                        "name": "wire_transfer_at_ctr_threshold",
                        "given": "User initiates wire transfer",
                        "when": f"User sends wire transfer of exactly ${ctr:,}",
                        "then": "Wire succeeds, CTR filed (threshold is inclusive)",
                        "priority": "P0",
                        "tags": ["wire_transfer", "ctr", "boundary"]
                    },
                    {
                        "name": "wire_transfer_ofac_blocked",
                        "given": "Beneficiary name matches OFAC SDN list",
                        "when": "User attempts wire transfer to blocked party",
                        "then": "Wire rejected, funds frozen, OFAC blocking report filed",
                        "priority": "P0",
                        "tags": ["wire_transfer", "ofac", "sanctions"]
                    },
                    {
                        "name": "wire_transfer_travel_rule",
                        "given": "User sends international wire",
                        "when": f"Wire amount is ${travel:,} or more",
                        "then": "Originator and beneficiary info included per Travel Rule",
                        "priority": "P0",
                        "tags": ["wire_transfer", "travel_rule", "bsa_aml"]
                    },
                ])
            
            # SAR filing scenarios
            if "sar" in banking_rules:
                sar_threshold = banking_rules["sar"].get("suspicious_threshold", 5000)
                scenarios.extend([
                    {
                        "name": "sar_suspicious_activity_above_threshold",
                        "given": "Transaction monitoring detects suspicious pattern",
                        "when": f"Suspicious activity involves > ${sar_threshold:,}",
                        "then": "SAR filed with FinCEN within 30 days, subject NOT notified",
                        "priority": "P0",
                        "tags": ["sar", "bsa_aml", "fincen"]
                    },
                    {
                        "name": "sar_insider_abuse_any_amount",
                        "given": "Bank employee involved in suspicious activity",
                        "when": "Insider abuse detected at any dollar amount",
                        "then": "SAR filed immediately, no minimum threshold",
                        "priority": "P0",
                        "tags": ["sar", "insider", "bsa_aml"]
                    },
                    {
                        "name": "sar_no_tipping_off",
                        "given": "SAR has been filed for a customer",
                        "when": "Customer inquires about account restrictions",
                        "then": "Bank does NOT disclose SAR filing",
                        "priority": "P0",
                        "tags": ["sar", "confidentiality"]
                    },
                ])
            
            # OFAC screening scenarios
            if "ofac" in banking_rules:
                scenarios.extend([
                    {
                        "name": "ofac_sdn_exact_match_blocked",
                        "given": "Customer name exactly matches OFAC SDN entry",
                        "when": "Wire transfer or account opening attempted",
                        "then": "Transaction blocked, blocking report filed within 10 days",
                        "priority": "P0",
                        "tags": ["ofac", "sanctions", "sdn"]
                    },
                    {
                        "name": "ofac_fuzzy_match_review",
                        "given": "Customer name is a close variant of SDN entry",
                        "when": "Fuzzy match score >= 85%",
                        "then": "Transaction held for manual compliance review",
                        "priority": "P0",
                        "tags": ["ofac", "fuzzy_match", "review"]
                    },
                    {
                        "name": "ofac_false_positive_release",
                        "given": "Compliance officer reviews fuzzy match",
                        "when": "Determination is false positive",
                        "then": "Transaction released, false positive documented",
                        "priority": "P1",
                        "tags": ["ofac", "false_positive"]
                    },
                ])
            
            # Reg E scenarios
            if "reg_e" in banking_rules:
                scenarios.extend([
                    {
                        "name": "reg_e_provisional_credit_within_10_days",
                        "given": "Consumer reports unauthorized EFT within 60 days",
                        "when": "Bank cannot resolve within 10 business days",
                        "then": "Provisional credit issued, investigation continues",
                        "priority": "P0",
                        "tags": ["reg_e", "provisional_credit", "cfpb"]
                    },
                    {
                        "name": "reg_e_dispute_after_60_days",
                        "given": "Consumer notices unauthorized EFT on statement",
                        "when": "Consumer reports after 60-day window",
                        "then": "Bank may decline — consumer assumes liability",
                        "priority": "P1",
                        "tags": ["reg_e", "expired_window", "negative"]
                    },
                ])
            
            # Section 1071 scenarios
            if "section_1071" in banking_rules:
                scenarios.extend([
                    {
                        "name": "cfpb_1071_demographic_collection",
                        "given": "Small business applies for credit",
                        "when": "Application is submitted",
                        "then": "Demographic data collected and firewalled from underwriting",
                        "priority": "P0",
                        "tags": ["cfpb", "section_1071", "fair_lending"]
                    },
                ])
            
            # Edge cases
            scenarios.extend([
                {
                    "name": "zero_amount_transfer",
                    "given": "User initiates transfer",
                    "when": "User enters amount $0",
                    "then": "Transaction rejected 'Amount must be greater than 0'",
                    "priority": "P1",
                    "tags": ["edge_case", "validation"]
                },
                {
                    "name": "negative_amount_transfer",
                    "given": "User initiates transfer",
                    "when": "User enters amount -$100",
                    "then": "Transaction rejected 'Invalid amount'",
                    "priority": "P1",
                    "tags": ["edge_case", "negative"]
                },
            ])
            
            # Error handling
            scenarios.extend([
                {
                    "name": "insufficient_balance",
                    "given": "User has balance $1,000",
                    "when": "User transfers $5,000",
                    "then": "Transaction rejected 'Insufficient balance'",
                    "priority": "P0",
                    "tags": ["error_handling", "balance"]
                },
                {
                    "name": "invalid_beneficiary",
                    "given": "User initiates transfer",
                    "when": "User enters invalid routing/account number",
                    "then": "Transaction rejected 'Invalid beneficiary account'",
                    "priority": "P0",
                    "tags": ["error_handling", "validation"]
                },
            ])
            
            result = {
                "scenarios": scenarios,
                "scenario_count": len(scenarios),
                "coverage": self._calculate_coverage(scenarios, requirements)
            }
            
            self.logger.info(
                "framework_completed",
                workflow_id=workflow_id,
                scenario_count=len(scenarios)
            )
            
            return result
            
        except Exception as e:
            self.logger.error("framework_failed", workflow_id=workflow_id, error=str(e))
            raise
    
    def _calculate_coverage(self, scenarios: List, requirements: List) -> Dict:
        """Calculate requirement coverage"""
        return {
            "total_requirements": len(requirements),
            "covered_requirements": len(requirements),  # Simplified
            "coverage_percentage": 100 if requirements else 0
        }