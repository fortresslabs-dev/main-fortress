"""Discovery Agent - Complete Implementation
Discovers APIs, functions, and dependencies from JIRA story and codebase.

Aligned to USA banking compliance (OCC, FDIC, CFPB, FinCEN, OFAC, FFIEC).
"""
from typing import Dict, Any, List
import re
import structlog
from src.agents.base_agent import BaseAgent

logger = structlog.get_logger()

class DiscoveryAgent(BaseAgent):
    agent_type = "discovery"
    
    # USA banking-specific patterns to detect in JIRA stories
    BANKING_PATTERNS = {
        "wire_transfer": r'\b(wire\s*transfer|wire|fedwire|swift|chips)\b',
        "ach": r'\b(ach|automated clearing house|direct deposit|ach debit|nacha)\b',
        "sar": r'\b(sar|suspicious activity report|suspicious transaction)\b',
        "ctr": r'\b(ctr|currency transaction report|cash reporting)\b',
        "ofac": r'\b(ofac|sanctions|sdn|specially designated|blocked person)\b',
        "bsa_aml": r'\b(bsa|aml|anti[\s-]?money laundering|bank secrecy act)\b',
        "kyc_cdd": r'\b(kyc|cdd|cip|know your customer|customer due diligence|customer identification)\b',
        "transfer": r'\b(transfer|payment|send money|fund transfer|remittance)\b',
        "limit": r'\b(limit|daily limit|transaction limit|threshold|maximum|minimum)\b',
        "reg_e": r'\b(reg\s*e|regulation\s*e|electronic fund transfer|error resolution)\b',
        "hmda": r'\b(hmda|home mortgage|mortgage disclosure)\b',
        "section_1071": r'\b(1071|section 1071|small business lending)\b',
        "loan": r'\b(loan|lending|credit|mortgage|underwriting)\b',
        "account": r'\b(account|savings|checking|deposit|dda)\b',
        "pii": r'\b(ssn|social security|tin|ein|date of birth|dob)\b',
    }
    
    # US regulatory compliance patterns
    US_COMPLIANCE_KEYWORDS = [
        "occ", "fdic", "cfpb", "fincen", "ffiec", "frb", "federal reserve",
        "compliance", "regulation", "examination", "audit", "bsa", "aml",
        "sox", "pci-dss", "pci", "glba", "reg e", "reg z", "dodd-frank",
    ]

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """
        Discover context from JIRA story
        
        Args:
            context: Workflow context
            input_data: Contains jira_story, workflow_id
            
        Returns:
            discovered_apis: List of API endpoints mentioned
            dependencies: List of services/dependencies
            related_code: List of related code patterns
            banking_patterns: Banking domain patterns detected
            compliance_requirements: RBI compliance needs
        """
        try:
            jira_story = input_data.get("jira_story", "")
            jira_key = input_data.get("jira_key", "")
            workflow_id = input_data.get("workflow_id")
            
            self.logger.info(
                "discovery_started",
                workflow_id=workflow_id,
                jira_key=jira_key
            )
            
            # 1. Extract API/function mentions
            discovered_apis = self._extract_api_mentions(jira_story)
            
            # 2. Detect banking patterns
            banking_patterns = self._detect_banking_patterns(jira_story)
            
            # 3. Identify compliance requirements
            compliance_requirements = self._identify_compliance(jira_story, banking_patterns)
            
            # 4. Extract dependencies
            dependencies = self._extract_dependencies(jira_story, banking_patterns)
            
            # 5. Look up related code patterns for the discovered banking patterns
            related_code = await self._search_related_code(jira_story, banking_patterns)
            
            # 6. Build discovery summary
            summary = self._build_discovery_summary(
                jira_story,
                discovered_apis,
                banking_patterns,
                compliance_requirements
            )
            
            result = {
                "discovered_apis": discovered_apis,
                "banking_patterns": banking_patterns,
                "compliance_requirements": compliance_requirements,
                "dependencies": dependencies,
                "related_code": related_code,
                "summary": summary
            }
            
            self.logger.info(
                "discovery_completed",
                workflow_id=workflow_id,
                apis_found=len(discovered_apis),
                patterns_found=len(banking_patterns),
                compliance_count=len(compliance_requirements)
            )
            
            return result
            
        except Exception as e:
            self.logger.error(
                "discovery_failed",
                workflow_id=workflow_id,
                error=str(e),
                exc_info=True
            )
            raise
    
    def _extract_api_mentions(self, story_text: str) -> List[str]:
        """Extract API endpoints and function names from story"""
        apis = []
        
        # Pattern 1: REST API paths (/api/v1/transfer, /payments/upi)
        api_pattern = r'/(?:api/)?(?:v\d+/)?([a-z\-_]+(?:/[a-z\-_]+)*)'
        apis.extend(re.findall(api_pattern, story_text, re.IGNORECASE))
        
        # Pattern 2: Function names (transfer_money, validate_upi)
        function_pattern = r'\b([a-z_]+(?:_[a-z]+)+)\s*\('
        apis.extend(re.findall(function_pattern, story_text))
        
        # Pattern 3: Service names (TransferService, PaymentProcessor)
        service_pattern = r'\b([A-Z][a-zA-Z]*(?:Service|Processor|Manager|Handler))\b'
        apis.extend(re.findall(service_pattern, story_text))
        
        # Remove duplicates and clean
        apis = list(set([api.strip().lower() for api in apis if api]))
        
        return apis
    
    def _detect_banking_patterns(self, story_text: str) -> Dict[str, bool]:
        """Detect which banking patterns are mentioned in story"""
        detected = {}
        
        for pattern_name, pattern_regex in self.BANKING_PATTERNS.items():
            matches = re.search(pattern_regex, story_text, re.IGNORECASE)
            if matches:
                detected[pattern_name] = True
                self.logger.debug(f"detected_pattern", pattern=pattern_name)
        
        return detected
    
    def _identify_compliance(self, story_text: str, banking_patterns: Dict) -> List[str]:
        """Identify US regulatory compliance requirements based on story"""
        requirements = []
        
        # Check for explicit compliance mentions
        story_lower = story_text.lower()
        for keyword in self.US_COMPLIANCE_KEYWORDS:
            if keyword in story_lower:
                requirements.append(f"explicit_{keyword.replace(' ', '_')}")
        
        # Infer compliance from banking patterns
        if "wire_transfer" in banking_patterns:
            requirements.extend([
                "bsa_aml_wire_transfer_recordkeeping",
                "wire_transfer_dual_approval_above_10k",
                "ofac_screening_required",
                "ctr_filing_above_10k",
                "travel_rule_3000_plus",
            ])
        
        if "sar" in banking_patterns or "bsa_aml" in banking_patterns:
            requirements.extend([
                "sar_filing_suspicious_activity",
                "sar_threshold_5000",
                "sar_no_tipping_off",
                "bsa_recordkeeping",
            ])
        
        if "ctr" in banking_patterns:
            requirements.extend([
                "ctr_10000_threshold",
                "ctr_structuring_detection",
                "ctr_filing_within_15_days",
            ])
        
        if "ofac" in banking_patterns:
            requirements.extend([
                "ofac_sdn_screening",
                "ofac_fuzzy_name_matching",
                "ofac_real_time_blocking",
            ])
        
        if "ach" in banking_patterns:
            requirements.extend([
                "nacha_ach_rules",
                "ach_return_handling",
                "reg_e_error_resolution",
            ])
        
        if "kyc_cdd" in banking_patterns:
            requirements.extend([
                "cip_identity_verification",
                "cdd_beneficial_ownership",
                "enhanced_due_diligence_high_risk",
            ])
        
        if "reg_e" in banking_patterns:
            requirements.extend([
                "reg_e_60_day_reporting_window",
                "reg_e_10_day_provisional_credit",
                "reg_e_error_investigation",
            ])
        
        if "hmda" in banking_patterns:
            requirements.extend([
                "hmda_data_collection",
                "hmda_lar_reporting",
            ])
        
        if "section_1071" in banking_patterns:
            requirements.extend([
                "cfpb_1071_demographic_collection",
                "cfpb_1071_firewall_rule",
            ])
        
        if "loan" in banking_patterns:
            requirements.extend([
                "fair_lending_ecoa",
                "tila_disclosure",
                "cfpb_ability_to_repay",
            ])
        
        if "limit" in banking_patterns:
            requirements.extend([
                "daily_transaction_limit_check",
                "per_transaction_limit_check",
            ])
        
        # Remove duplicates
        requirements = list(set(requirements))
        
        return requirements
    
    def _extract_dependencies(self, story_text: str, banking_patterns: Dict) -> List[str]:
        """Extract service dependencies based on story"""
        dependencies = []
        
        # Banking services
        if "wire_transfer" in banking_patterns:
            dependencies.extend(["fedwire_gateway", "swift_gateway", "wire_transfer_service", "ofac_screening_service"])
        
        if "ach" in banking_patterns:
            dependencies.extend(["nacha_gateway", "ach_processor", "ach_return_handler"])
        
        if "ofac" in banking_patterns:
            dependencies.extend(["ofac_sdn_service", "sanctions_screening_service"])
        
        if "sar" in banking_patterns or "bsa_aml" in banking_patterns:
            dependencies.extend(["fincen_filing_service", "transaction_monitoring_service"])
        
        if "ctr" in banking_patterns:
            dependencies.extend(["ctr_filing_service", "cash_transaction_service"])
        
        if "kyc_cdd" in banking_patterns:
            dependencies.extend(["identity_verification_service", "cdd_service", "beneficial_ownership_service"])
        
        if "reg_e" in banking_patterns:
            dependencies.extend(["dispute_resolution_service", "provisional_credit_service"])
        
        if "loan" in banking_patterns or "hmda" in banking_patterns or "section_1071" in banking_patterns:
            dependencies.extend(["loan_origination_system", "credit_scoring_service", "demographic_data_service"])
        
        # Common banking dependencies
        if "transfer" in banking_patterns or "account" in banking_patterns:
            dependencies.extend([
                "core_banking_system",
                "account_service",
                "transaction_service",
                "audit_service",
            ])
        
        # Remove duplicates
        dependencies = list(set(dependencies))
        
        return dependencies
    
    async def _search_related_code(self, story_text: str, banking_patterns: Dict) -> List[Dict]:
        """Return hardcoded related-code hints for the discovered banking patterns."""
        related = []
        
        if "wire_transfer" in banking_patterns:
            related.append({
                "type": "function",
                "name": "validate_wire_transfer",
                "signature": "validate_wire_transfer(amount: Decimal, beneficiary: str) -> WireResult",
                "description": "Validates wire transfer with OFAC screening and CTR threshold ($10,000)"
            })
        
        if "ofac" in banking_patterns:
            related.append({
                "type": "function",
                "name": "screen_ofac_sdn",
                "signature": "screen_ofac_sdn(name: str, country: str) -> ScreeningResult",
                "description": "Screens beneficiary against OFAC SDN list with fuzzy matching"
            })
        
        if "sar" in banking_patterns or "bsa_aml" in banking_patterns:
            related.append({
                "type": "function",
                "name": "file_suspicious_activity_report",
                "signature": "file_sar(transaction_id: str, reason: str) -> SARFiling",
                "description": "Files SAR with FinCEN for suspicious activity above $5,000"
            })
        
        if "transfer" in banking_patterns:
            related.append({
                "type": "function",
                "name": "transfer_funds",
                "signature": "transfer_funds(from_account: str, to_account: str, amount: Decimal) -> Transaction",
                "description": "Transfers funds between accounts with BSA/AML validation"
            })
        
        if "limit" in banking_patterns or "ctr" in banking_patterns:
            related.append({
                "type": "function",
                "name": "check_ctr_threshold",
                "signature": "check_ctr_threshold(account_id: str, amount: Decimal) -> bool",
                "description": "Checks if cash transaction exceeds $10,000 CTR reporting threshold"
            })
        
        if "reg_e" in banking_patterns:
            related.append({
                "type": "function",
                "name": "process_reg_e_dispute",
                "signature": "process_dispute(dispute_id: str) -> DisputeResult",
                "description": "Processes Reg E dispute with 10-day provisional credit"
            })
        
        return related
    
    def _build_discovery_summary(
        self, 
        story_text: str, 
        apis: List, 
        patterns: Dict, 
        compliance: List
    ) -> str:
        """Build human-readable summary of discovery"""
        
        summary_parts = []
        
        # Story type
        if "wire_transfer" in patterns:
            summary_parts.append("Wire transfer operation")
        elif "ach" in patterns:
            summary_parts.append("ACH payment processing")
        elif "ofac" in patterns:
            summary_parts.append("OFAC sanctions screening")
        elif "sar" in patterns:
            summary_parts.append("SAR filing workflow")
        elif "ctr" in patterns:
            summary_parts.append("CTR cash transaction reporting")
        elif "loan" in patterns:
            summary_parts.append("Loan origination")
        elif "transfer" in patterns:
            summary_parts.append("Fund transfer operation")
        else:
            summary_parts.append("Banking operation")
        
        # Compliance summary
        if compliance:
            summary_parts.append(f"with {len(compliance)} US regulatory compliance requirements")
        
        # Pattern summary
        pattern_names = list(patterns.keys())
        if pattern_names:
            summary_parts.append(f"involving: {', '.join(pattern_names)}")
        
        summary = " ".join(summary_parts) + "."
        
        return summary
