"""Generation Agent - Generates Pytest Code using AWS Bedrock Claude API

UPDATED: Now uses AWS Bedrock (Claude Sonnet) instead of OpenAI
"""
from typing import Dict, Any, List
import os
import json
import structlog
from src.agents.base_agent import BaseAgent
from src.services.bedrock_service import BedrockService

logger = structlog.get_logger()

class GenerationAgent(BaseAgent):
    agent_type = "generation"
    
    def __init__(self):
        super().__init__()
        from src.utils.config import settings
        self.model = settings.P2_AGENT_GENERATION_MODEL or settings.BEDROCK_MODEL
        self.bedrock = BedrockService(model_id=self.model)

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """Generate pytest code from scenarios using GPT-5.1 API"""
        try:
            workflow_id = input_data.get("workflow_id")
            jira_key = input_data.get("jira_key", "")
            scenarios = input_data.get("scenarios", [])
            understanding = input_data.get("understanding", {})
            trigger_rule_id = input_data.get("rule_id")
            self.logger.info("generation_started", workflow_id=workflow_id, rule_id=trigger_rule_id)
            grounding = await self._build_grounding(trigger_rule_id) if trigger_rule_id else ""
            prompt = self._build_prompt(jira_key, scenarios, understanding, grounding)
            
            test_code = await self.bedrock.generate_code(
                prompt=prompt,
                max_tokens=16000,
                temperature=0.3,
            )
            
            test_code = self._clean_code(test_code)
            result = {
                "test_code": test_code,
                "language": "python",
                "framework": "pytest",
                "scenario_count": len(scenarios),
                "lines_of_code": len(test_code.split("\n"))
            }
            self.logger.info(
                "generation_completed",
                workflow_id=workflow_id,
                lines=result["lines_of_code"]
            )
            return result
        except Exception as e:
            self.logger.error("generation_failed", workflow_id=workflow_id, error=str(e))
            raise
    
    # Canonical, do-not-invent values (kept in sync with CLAUDE.md "Critical
    # constants"). beneficial_ownership_pct=20 per FinCEN CHG-2026-0412.
    _AUTHORITATIVE_THRESHOLDS = {
        "ctr_threshold": 10000,
        "sar_threshold": 5000,
        "travel_rule_threshold": 3000,
        "wire_dual_approval_threshold": 10000,
        "ofac_fuzzy_threshold": 85,
        "beneficial_ownership_pct": 20,
    }

    async def _build_grounding(self, trigger_rule_id: str) -> str:
        """Deterministic compliance grounding for the rule the CCO clicked.

        Injects (1) the exact CFR citation, (2) every obligation of the trigger
        rule as a MUST-TEST item, (3) authoritative thresholds (do not invent),
        and (4) a few semantically-nearest obligations from other rules as
        context — using the per-obligation embeddings (migration 052).
        """
        from src.services.database import get_db
        from sqlalchemy import text as _t
        try:
            async with get_db() as s:
                meta = (await s.execute(_t(
                    "SELECT cfr_citation, category FROM compliance_rules WHERE rule_id=:r"
                ), {"r": trigger_rule_id})).first()
                own = (await s.execute(_t(
                    "SELECT obligation_id, source_text FROM obligations "
                    "WHERE regulation_id=:r AND is_active=TRUE ORDER BY obligation_id"
                ), {"r": trigger_rule_id})).fetchall()
                related = (await s.execute(_t(
                    "WITH t AS (SELECT embedding FROM obligations "
                    "           WHERE regulation_id=:r AND embedding IS NOT NULL LIMIT 1) "
                    "SELECT o.regulation_id, o.source_text FROM obligations o, t "
                    "WHERE o.regulation_id <> :r AND o.is_active=TRUE "
                    "  AND o.embedding IS NOT NULL "
                    "ORDER BY o.embedding <=> t.embedding LIMIT 5"
                ), {"r": trigger_rule_id})).fetchall()
        except Exception as exc:
            self.logger.warning("grounding_fetch_failed", rule_id=trigger_rule_id, error=str(exc))
            return ""

        cfr = (meta.cfr_citation if meta else None) or trigger_rule_id
        must = "\n".join(f"  - [{o.obligation_id}] {o.source_text}" for o in own) or "  (none on record)"
        ctx = "\n".join(f"  - ({o.regulation_id}) {(o.source_text or '')[:200]}" for o in related) or "  (none)"
        thresholds = "\n".join(f"  - {k} = {v}" for k, v in self._AUTHORITATIVE_THRESHOLDS.items())
        return f"""AUTHORITATIVE COMPLIANCE GROUNDING (source of truth — obey exactly):

Rule under test: {trigger_rule_id}  (CFR: {cfr})

MUST-TEST OBLIGATIONS — write at least one explicit test asserting EACH of these,
and include the citation "{cfr}" in every assertion message for this rule:
{must}

AUTHORITATIVE THRESHOLDS — use these EXACT values; never invent, round, or guess:
{thresholds}

RELATED OBLIGATIONS (context only — test only if a scenario covers them):
{ctx}
"""

    def _build_prompt(self, jira_key: str, scenarios: List, understanding: Dict,
                      grounding: str = "") -> str:
        """Build the U.S. banking compliance pytest-generation prompt."""

        banking_rules = understanding.get("banking_rules", {})
        compliance = understanding.get("compliance_context", {})

        scenario_text = "\n\n".join([
            f"Scenario: {s['name']}\n"
            f"Given {s['given']}\n"
            f"When {s['when']}\n"
            f"Then {s['then']}"
            for s in scenarios[:10]
        ])

        prompt = f"""You are an expert U.S. banking compliance test automation engineer.

Generate production-quality pytest tests from the JIRA story and BDD scenarios.
Use only the supplied scenarios and rule context as source of truth.

{grounding}
JIRA Story: {jira_key}

BDD Scenarios:
{scenario_text}

Banking Rules (structured/domain):
{json.dumps(banking_rules, indent=2)}

Compliance Context:
{json.dumps(compliance, indent=2)}

Requirements:
1. Generate runnable Python pytest code only (no markdown, no prose).
2. Create at least one test per scenario, preserving scenario intent and names,
   AND at least one explicit test per MUST-TEST obligation above (put its CFR
   citation in the assertion message).
3. Use Arrange-Act-Assert style and clear docstrings.
4. Include fixtures and mocks for external dependencies (OFAC service, rule resolver, DB/audit logger).
5. Use Decimal for currency and timezone-aware datetimes where relevant.
6. Add boundary tests based on rule thresholds present in input context.
7. Use the AUTHORITATIVE THRESHOLDS listed in the grounding block above as the
   exact values. Never invent, round, or guess a threshold.
8. Cover U.S. compliance behaviors when relevant to scenarios:
   - OFAC/SDN screening and block/review outcomes
   - CTR/SAR conditions and suspicious activity handling
   - Travel Rule data requirements
   - Dual approval requirements for high-value wires
   - Reg E error/dispute handling windows
   - Audit trail and decision logging
   - PII handling/masking and secure transport assumptions
9. Include negative-path tests (invalid account, insufficient funds, zero/negative amount, false positives).
10. Keep tests deterministic and offline: no live network/API calls.
11. Include type hints where practical.

Output format:
- Return ONLY valid Python code that can run under pytest.
"""
        return prompt
    
    def _clean_code(self, code: str) -> str:
        """Extract the first ```python ... ``` block; fall back to naive strip.

        Claude sometimes prefaces/suffixes the code with prose ("Here's the
        test code:" / "This test verifies ...") even when told not to. Naive
        marker-stripping leaves that prose in place and breaks ast.parse in
        the validation agent. Prefer regex extraction of the fenced block.
        """
        import re
        match = re.search(r"```(?:python)?\s*\n(.*?)```", code, re.DOTALL)
        if match:
            return match.group(1).strip()
        return code.replace("```python", "").replace("```", "").strip()