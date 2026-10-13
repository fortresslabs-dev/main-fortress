"""Generation Agent - Generates Pytest Code with LLM (cached + mock mode)"""
from typing import Dict, Any, List
import json
import hashlib
import structlog
from src.agents.base_agent import BaseAgent
from src.utils.config import settings
from src.services.redis_service import get_redis

logger = structlog.get_logger()

CACHE_PREFIX = "llm:gen:"


class GenerationAgent(BaseAgent):
    agent_type = "generation"

    def __init__(self):
        super().__init__()
        self.mock_mode = settings.LLM_MOCK_MODE
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.model = settings.P2_AGENT_GENERATION_MODEL or settings.ANTHROPIC_MODEL
        self.cache_ttl = settings.LLM_CACHE_TTL

        if not self.mock_mode:
            import anthropic
            self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """Generate pytest code from scenarios using Anthropic API (with cache & mock)"""
        workflow_id = input_data.get("workflow_id")
        try:
            jira_key = input_data.get("jira_key", "")
            scenarios = input_data.get("scenarios", [])
            understanding = input_data.get("understanding", {})
            self.logger.info("generation_started", workflow_id=workflow_id)

            # --- Mock mode: return realistic stub without LLM call ---
            if self.mock_mode:
                test_code = self._generate_mock_code(jira_key, scenarios)
                return self._build_result(test_code, scenarios, workflow_id, source="mock")

            prompt = self._build_prompt(jira_key, scenarios, understanding)

            # --- Check Redis cache ---
            cache_key = CACHE_PREFIX + hashlib.sha256(prompt.encode()).hexdigest()
            redis = await get_redis()
            if redis:
                cached = await redis.get(cache_key)
                if cached:
                    self.logger.info("generation_cache_hit", workflow_id=workflow_id)
                    return self._build_result(cached, scenarios, workflow_id, source="cache")

            # --- Call LLM ---
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            test_code = self._clean_code(message.content[0].text)

            # --- Store in cache ---
            if redis:
                await redis.set(cache_key, test_code, ex=self.cache_ttl)
                self.logger.info("generation_cached", workflow_id=workflow_id)

            usage = getattr(message, "usage", None)
            if usage:
                self.logger.info(
                    "generation_token_usage",
                    workflow_id=workflow_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )

            return self._build_result(test_code, scenarios, workflow_id, source="llm")

        except Exception as e:
            self.logger.error("generation_failed", workflow_id=workflow_id, error=str(e))
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_result(self, test_code: str, scenarios: list, workflow_id: str, source: str) -> Dict[str, Any]:
        result = {
            "test_code": test_code,
            "language": "python",
            "framework": "pytest",
            "scenario_count": len(scenarios),
            "lines_of_code": len(test_code.split("\n")),
            "source": source,
        }
        self.logger.info(
            "generation_completed",
            workflow_id=workflow_id,
            lines=result["lines_of_code"],
            source=source,
        )
        return result

    def _build_prompt(self, jira_key: str, scenarios: List, understanding: Dict) -> str:
        """Build a compact US banking compliance test prompt (token-optimised)."""
        scenario_text = "\n".join(
            f"- {s['name']}: Given {s['given']} When {s['when']} Then {s['then']}"
            for s in scenarios[:10]
        )
        banking_rules = understanding.get("banking_rules", {})
        rules_summary = json.dumps(banking_rules, separators=(",", ":")) if banking_rules else "{}"

        return (
            f"Generate pytest code for US banking compliance tests.\n"
            f"JIRA: {jira_key}\n\n"
            f"Scenarios:\n{scenario_text}\n\n"
            f"Rules: {rules_summary}\n\n"
            f"Requirements:\n"
            f"- One test per scenario, Arrange-Act-Assert, Decimal amounts\n"
            f"- Fixtures: customer, account, transaction_service (mocked)\n"
            f"- BSA/AML $10k SAR, Reg E, Reg Z, KYC/CDD, audit trail\n"
            f"- Type hints, edge cases, no markdown fences\n"
            f"Return ONLY Python code."
        )

    def _clean_code(self, code: str) -> str:
        """Remove markdown code blocks if present"""
        code = code.replace("```python", "").replace("```", "")
        return code.strip()

    def _generate_mock_code(self, jira_key: str, scenarios: List) -> str:
        """Return deterministic test code without calling LLM — for dev/testing."""
        lines = [
            '"""Auto-generated compliance tests for ' + jira_key + ' (mock mode)"""',
            "import pytest",
            "from decimal import Decimal",
            "from datetime import datetime",
            "from unittest.mock import MagicMock, patch",
            "",
            "",
            "@pytest.fixture",
            "def customer():",
            '    return {"id": "CUST-001", "name": "Test User", "kyc_verified": True, "pan": "ABCDE1234F"}',
            "",
            "",
            "@pytest.fixture",
            "def account(customer):",
            '    return {"id": "ACC-001", "customer_id": customer["id"], "balance": Decimal("100000.00")}',
            "",
            "",
            "@pytest.fixture",
            "def transaction_service():",
            "    return MagicMock()",
            "",
        ]

        for i, s in enumerate(scenarios[:10]):
            fn_name = s["name"].replace(" ", "_").replace("-", "_").lower()
            lines += [
                "",
                f"def test_{fn_name}(customer, account, transaction_service):",
                f'    """Scenario: {s["name"]}',
                f'    Given {s["given"]}',
                f'    When {s["when"]}',
                f'    Then {s["then"]}',
                f'    """',
                "    # Arrange",
                f'    amount = Decimal("1000.00")',
                f"    audit_log = []",
                "",
                "    # Act",
                f'    result = transaction_service.process(account["id"], amount)',
                "",
                "    # Assert",
                f"    assert result is not None",
                f"    # BSA/AML: verify SAR threshold check",
                f"    if amount >= Decimal('10000'):",
                f"        assert transaction_service.file_sar.called",
                f"    # Audit trail",
                f"    audit_log.append({{'event': 'test_{fn_name}', 'ts': datetime.utcnow().isoformat()}})",
                f"    assert len(audit_log) > 0",
            ]

        return "\n".join(lines)