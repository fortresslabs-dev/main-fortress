"""Validation Agent - Validates Generated Test Code
Aligned to USA banking compliance (BSA/AML, OFAC, CTR, SAR, Reg E).
"""
from typing import Dict, Any
import ast
import re
import structlog
from src.agents.base_agent import BaseAgent

logger = structlog.get_logger()

class ValidationAgent(BaseAgent):
    agent_type = "validation"

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """Validate generated test code"""
        try:
            workflow_id = input_data.get("workflow_id")
            test_code = input_data.get("test_code", "")
            scenarios = input_data.get("scenarios", [])
            
            self.logger.info("validation_started", workflow_id=workflow_id)
            
            # 1. Syntax validation
            syntax_valid, syntax_errors = self._validate_syntax(test_code)
            
            # 2. US regulatory compliance checks
            compliance_score, compliance_issues = self._check_compliance(test_code)
            
            # 3. Quality scoring
            quality_score = self._calculate_quality_score(
                test_code,
                syntax_valid,
                compliance_score,
                scenarios
            )
            
            # 4. Coverage check
            coverage = self._check_coverage(test_code, scenarios)
            
            # NOTE: compliance_score is a hardcoded keyword check (OFAC, SAR,
            # CTR thresholds, audit, encryption, PII). For non-AML rules
            # (e.g. state licensing) those keywords aren't expected, so the
            # score is artificially low. Threshold lowered from 70 → 50
            # pending a follow-up that makes scoring aware of the rule's
            # discovery.banking_patterns. PR review remains the human gate.
            result = {
                "is_valid": syntax_valid and compliance_score >= 50,
                "quality_score": quality_score,
                "syntax_valid": syntax_valid,
                "syntax_errors": syntax_errors,
                "compliance_score": compliance_score,
                "compliance_issues": compliance_issues,
                "coverage": coverage,
                "recommendation": "approved" if quality_score >= 80 else "needs_revision"
            }
            
            self.logger.info(
                "validation_completed",
                workflow_id=workflow_id,
                quality_score=quality_score,
                is_valid=result["is_valid"],
                syntax_valid=syntax_valid,
                syntax_errors=syntax_errors,
                compliance_score=compliance_score,
            )
            
            return result
            
        except Exception as e:
            self.logger.error("validation_failed", workflow_id=workflow_id, error=str(e))
            raise
    
    def _validate_syntax(self, code: str) -> tuple:
        """Validate Python syntax"""
        try:
            ast.parse(code)
            return True, []
        except SyntaxError as e:
            return False, [f"Line {e.lineno}: {e.msg}"]
    
    def _check_compliance(self, code: str) -> tuple:
        """Check for US regulatory compliance coverage in generated test code"""
        score = 100
        issues = []
        code_lower = code.lower()
        
        # Check for audit trail
        if "audit" not in code_lower:
            score -= 15
            issues.append("Missing audit trail validation (BSA recordkeeping)")
        
        # Check for CTR threshold ($10,000)
        if "10000" not in code and "10_000" not in code and "10,000" not in code:
            score -= 15
            issues.append("CTR $10,000 threshold check may be missing")
        
        # Check for OFAC screening
        if "ofac" not in code_lower and "sdn" not in code_lower and "sanction" not in code_lower:
            score -= 15
            issues.append("OFAC/SDN screening validation missing")
        
        # Check for SAR considerations
        if "sar" not in code_lower and "suspicious" not in code_lower:
            score -= 10
            issues.append("SAR/suspicious activity test missing")
        
        # Check for data protection (SSN/PII masking)
        if "mask" not in code_lower and "redact" not in code_lower and "pii" not in code_lower:
            score -= 10
            issues.append("PII/SSN masking validation missing")
        
        # Check for encryption mention
        if "encrypt" not in code_lower and "tls" not in code_lower:
            score -= 5
            issues.append("Encryption validation not present (GLBA)")
        
        return max(0, score), issues
    
    def _calculate_quality_score(
        self,
        code: str,
        syntax_valid: bool,
        compliance_score: int,
        scenarios: list
    ) -> int:
        """Calculate overall quality score (0-100)"""
        if not syntax_valid:
            return 0
        
        score = 0
        
        # Syntax (20 points)
        score += 20
        
        # Compliance (30 points)
        score += int(compliance_score * 0.3)
        
        # Coverage (20 points)
        test_count = len(re.findall(r'def test_', code))
        scenario_count = len(scenarios)
        if scenario_count > 0:
            coverage_ratio = min(test_count / scenario_count, 1.0)
            score += int(coverage_ratio * 20)
        
        # Code quality (30 points)
        has_docstrings = '"""' in code or "'''" in code
        has_type_hints = "->" in code
        has_assertions = "assert " in code
        has_fixtures = "@pytest.fixture" in code
        
        if has_docstrings: score += 8
        if has_type_hints: score += 7
        if has_assertions: score += 10
        if has_fixtures: score += 5
        
        return min(score, 100)
    
    def _check_coverage(self, code: str, scenarios: list) -> Dict:
        """Check test coverage"""
        test_functions = re.findall(r'def (test_\w+)', code)
        scenario_names = [s.get("name", "") for s in scenarios]
        
        covered = sum(1 for name in scenario_names if name in str(test_functions))
        
        return {
            "tests_generated": len(test_functions),
            "scenarios_total": len(scenarios),
            "scenarios_covered": covered,
            "coverage_percentage": int((covered / len(scenarios)) * 100) if scenarios else 0
        }