"""Tests for 6-Agent Pipeline — USA Banking Compliance

Tests all six agents (Discovery, Understanding, Framework, Validation,
Execution) with USA banking scenarios: wire transfers, BSA/AML,
OFAC screening, SAR filing, Reg E, and CFPB Section 1071.
"""
import pytest

from src.agents.discovery import DiscoveryAgent
from src.agents.understanding import UnderstandingAgent
from src.agents.framework import FrameworkAgent
from src.agents.validation import ValidationAgent
from src.agents.execution import ExecutionAgent


# ══════════════════════════════════════════════════════════════════════
# Discovery Agent Tests
# ══════════════════════════════════════════════════════════════════════

class TestDiscoveryAgent:

    @pytest.fixture
    def agent(self):
        return DiscoveryAgent()

    @pytest.mark.asyncio
    async def test_wire_transfer_patterns(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Implement wire transfer endpoint with OFAC screening and BSA/AML compliance",
            "jira_key": "BANK-101", "workflow_id": "wf-001",
        })
        assert "wire_transfer" in result["banking_patterns"]
        assert "ofac" in result["banking_patterns"]
        assert "bsa_aml" in result["banking_patterns"]
        assert len(result["compliance_requirements"]) > 0
        assert any("ofac" in r for r in result["compliance_requirements"])

    @pytest.mark.asyncio
    async def test_sar_patterns(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Build SAR filing workflow for suspicious activity reports to FinCEN",
            "jira_key": "BANK-102", "workflow_id": "wf-002",
        })
        assert "sar" in result["banking_patterns"]
        assert any("sar" in r for r in result["compliance_requirements"])

    @pytest.mark.asyncio
    async def test_reg_e_patterns(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Implement Regulation E error resolution for unauthorized EFTs",
            "jira_key": "BANK-103", "workflow_id": "wf-003",
        })
        assert "reg_e" in result["banking_patterns"]
        assert any("reg_e" in r for r in result["compliance_requirements"])

    @pytest.mark.asyncio
    async def test_section_1071_patterns(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Collect Section 1071 demographic data for small business lending",
            "jira_key": "BANK-104", "workflow_id": "wf-004",
        })
        assert "section_1071" in result["banking_patterns"]
        assert any("1071" in r for r in result["compliance_requirements"])

    @pytest.mark.asyncio
    async def test_ach_patterns(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Process ACH direct deposit batch via automated clearing house",
            "jira_key": "BANK-105", "workflow_id": "wf-005",
        })
        assert "ach" in result["banking_patterns"]

    @pytest.mark.asyncio
    async def test_extracts_api_mentions(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Update /api/v1/transfer endpoint and TransferService.validate_transfer()",
            "jira_key": "BANK-106", "workflow_id": "wf-006",
        })
        assert len(result["discovered_apis"]) > 0

    @pytest.mark.asyncio
    async def test_wire_dependencies(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Wire transfer with OFAC screening",
            "jira_key": "BANK-107", "workflow_id": "wf-007",
        })
        assert any("ofac" in d for d in result["dependencies"])

    @pytest.mark.asyncio
    async def test_summary_mentions_wire(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Wire transfer endpoint for $50,000",
            "jira_key": "BANK-108", "workflow_id": "wf-008",
        })
        assert "Wire transfer" in result["summary"]

    @pytest.mark.asyncio
    async def test_no_banking_patterns(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Refactor the logging module",
            "jira_key": "BANK-109", "workflow_id": "wf-009",
        })
        assert result["banking_patterns"] == {}
        assert "Banking operation" in result["summary"]

    @pytest.mark.asyncio
    async def test_ctr_pattern(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Currency transaction report for cash deposits above threshold",
            "jira_key": "BANK-110", "workflow_id": "wf-010",
        })
        assert "ctr" in result["banking_patterns"]

    @pytest.mark.asyncio
    async def test_kyc_cdd_pattern(self, agent):
        result = await agent.execute({}, {
            "jira_story": "Customer due diligence and KYC onboarding workflow",
            "jira_key": "BANK-111", "workflow_id": "wf-011",
        })
        assert "kyc_cdd" in result["banking_patterns"]


# ══════════════════════════════════════════════════════════════════════
# Understanding Agent Tests
# ══════════════════════════════════════════════════════════════════════

class TestUnderstandingAgent:

    @pytest.fixture
    def agent(self):
        return UnderstandingAgent()

    @pytest.mark.asyncio
    async def test_wire_transfer_rules(self, agent):
        discovery = {
            "banking_patterns": {"wire_transfer": True, "ofac": True},
            "compliance_requirements": ["bsa_aml_wire_transfer_recordkeeping"],
        }
        result = await agent.execute({}, {
            "jira_story": "Wire transfer with OFAC screening",
            "workflow_id": "wf-020", "discovery": discovery,
        })
        assert "wire_transfer" in result["banking_rules"]
        assert result["banking_rules"]["wire_transfer"]["ctr_threshold"] == 10000
        assert len(result["test_scenarios_outline"]) > 0
        assert len(result["requirements"]) > 0

    @pytest.mark.asyncio
    async def test_sar_rules(self, agent):
        discovery = {
            "banking_patterns": {"sar": True, "bsa_aml": True},
            "compliance_requirements": ["sar_filing_suspicious_activity"],
        }
        result = await agent.execute({}, {
            "jira_story": "SAR filing", "workflow_id": "wf-021", "discovery": discovery,
        })
        assert "sar" in result["banking_rules"]
        assert result["banking_rules"]["sar"]["suspicious_threshold"] == 5000

    @pytest.mark.asyncio
    async def test_reg_e_rules(self, agent):
        discovery = {
            "banking_patterns": {"reg_e": True},
            "compliance_requirements": ["reg_e_10_day_provisional_credit"],
        }
        result = await agent.execute({}, {
            "jira_story": "Reg E dispute", "workflow_id": "wf-022", "discovery": discovery,
        })
        assert "reg_e" in result["banking_rules"]
        assert result["banking_rules"]["reg_e"]["provisional_credit_days"] == 10

    @pytest.mark.asyncio
    async def test_wire_edge_cases_include_ctr(self, agent):
        discovery = {
            "banking_patterns": {"wire_transfer": True},
            "compliance_requirements": [],
        }
        result = await agent.execute({}, {
            "jira_story": "Wire compliance", "workflow_id": "wf-023", "discovery": discovery,
        })
        descriptions = [e["description"] for e in result["edge_cases"]]
        assert any("CTR" in d for d in descriptions)

    @pytest.mark.asyncio
    async def test_compliance_context_has_regulations(self, agent):
        discovery = {
            "banking_patterns": {"wire_transfer": True, "ofac": True},
            "compliance_requirements": ["ofac_screening_required"],
        }
        result = await agent.execute({}, {
            "jira_story": "Wire with OFAC", "workflow_id": "wf-024", "discovery": discovery,
        })
        ctx = result["compliance_context"]
        assert any("BSA" in r["name"] for r in ctx["regulations"])
        assert len(ctx["audit_requirements"]) > 0

    @pytest.mark.asyncio
    async def test_fallback_rag_wire(self, agent):
        discovery = {
            "banking_patterns": {"wire_transfer": True},
            "compliance_requirements": [],
        }
        result = await agent.execute({}, {
            "jira_story": "Wire transfer", "workflow_id": "wf-025", "discovery": discovery,
        })
        # Fallback RAG should have produced context
        assert len(result["rag_context"]) > 0

    @pytest.mark.asyncio
    async def test_section_1071_rules(self, agent):
        discovery = {
            "banking_patterns": {"section_1071": True},
            "compliance_requirements": ["cfpb_1071_demographic_collection"],
        }
        result = await agent.execute({}, {
            "jira_story": "Section 1071", "workflow_id": "wf-026", "discovery": discovery,
        })
        assert "section_1071" in result["banking_rules"]
        assert result["banking_rules"]["section_1071"]["firewall_required"] is True

    # ──────────────────────────────────────────────────────────────────
    # _resolve_obligation_ids — Fix 1 (test-coverage writeback) plumbing.
    # ExecutionAgent relies on understanding.obligation_ids to know which
    # obligations to flip has_test_coverage on. These guard the resolver.
    # ──────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_resolve_obligation_ids_no_patterns(self, agent):
        """Empty banking_patterns → ([], []) without hitting compliance_service."""
        result = await agent._resolve_obligation_ids({"banking_patterns": {}})
        assert result == ([], [])

    @pytest.mark.asyncio
    async def test_resolve_obligation_ids_unmapped_patterns(self, agent):
        """Patterns absent from _PATTERN_TO_DOMAIN must not over-attribute —
        they are skipped, not allowed to fan out into a domain query."""
        result = await agent._resolve_obligation_ids(
            {"banking_patterns": {"some_future_pattern": True}}
        )
        assert result == ([], [])

    @pytest.mark.asyncio
    async def test_resolve_obligation_ids_compliance_service_returns_empty(
        self, agent, monkeypatch
    ):
        """Mapped patterns but compliance_service yields no rules → ([], [])."""
        async def _empty(domain, geography="usa"):
            return []
        monkeypatch.setattr(
            agent.compliance_service, "get_rules_by_domain", _empty
        )
        result = await agent._resolve_obligation_ids(
            {"banking_patterns": {"wire_transfer": True}}
        )
        assert result == ([], [])

    @pytest.mark.asyncio
    async def test_resolve_obligation_ids_db_failure_returns_rule_ids_only(
        self, agent, monkeypatch
    ):
        """DB query failure must degrade gracefully — return rule_ids the
        resolver got far enough to collect, with obligation_ids empty.
        ExecutionAgent treats empty obligation_ids as a no-op writeback."""
        async def _rules(domain, geography="usa"):
            return [{"rule_id": "RULE-WIRE-1"}, {"rule_id": "RULE-WIRE-2"}]
        monkeypatch.setattr(
            agent.compliance_service, "get_rules_by_domain", _rules
        )

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _broken_get_db():
            raise RuntimeError("db unavailable")
            yield  # pragma: no cover

        import src.services.database as _db_mod
        monkeypatch.setattr(_db_mod, "get_db", _broken_get_db)

        obligation_ids, rule_ids = await agent._resolve_obligation_ids(
            {"banking_patterns": {"wire_transfer": True}}
        )
        assert obligation_ids == []
        assert rule_ids == ["RULE-WIRE-1", "RULE-WIRE-2"]

    @pytest.mark.asyncio
    async def test_resolve_obligation_ids_happy_path(self, agent, monkeypatch):
        """Mapped pattern + compliance_service rules + DB returns obligations
        → both lists populated; rule_ids deduped + sorted."""
        async def _rules(domain, geography="usa"):
            # Duplicate rule_id across calls — resolver must dedupe.
            return [
                {"rule_id": "RULE-WIRE-2"},
                {"rule_id": "RULE-WIRE-1"},
                {"rule_id": "RULE-WIRE-1"},
            ]
        monkeypatch.setattr(
            agent.compliance_service, "get_rules_by_domain", _rules
        )

        class _FakeResult:
            def all(self):
                return [("OBL-WIRE-A",), ("OBL-WIRE-B",)]

        class _FakeSession:
            async def execute(self, stmt):
                return _FakeResult()

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_get_db():
            yield _FakeSession()

        import src.services.database as _db_mod
        monkeypatch.setattr(_db_mod, "get_db", _fake_get_db)

        obligation_ids, rule_ids = await agent._resolve_obligation_ids(
            {"banking_patterns": {"wire_transfer": True}}
        )
        assert obligation_ids == ["OBL-WIRE-A", "OBL-WIRE-B"]
        assert rule_ids == ["RULE-WIRE-1", "RULE-WIRE-2"]


# ══════════════════════════════════════════════════════════════════════
# Framework Agent Tests
# ══════════════════════════════════════════════════════════════════════

class TestFrameworkAgent:

    @pytest.fixture
    def agent(self):
        return FrameworkAgent()

    @pytest.mark.asyncio
    async def test_wire_produces_ctr_ofac_scenarios(self, agent):
        understanding = {
            "test_scenarios_outline": [],
            "requirements": [{"type": "compliance", "requirement": "BSA/AML"}],
            "banking_rules": {
                "wire_transfer": {"ctr_threshold": 10000, "travel_rule_threshold": 3000, "ofac_screening_required": True},
            },
        }
        result = await agent.execute({}, {"workflow_id": "wf-030", "understanding": understanding})
        names = [s["name"] for s in result["scenarios"]]
        assert "wire_transfer_above_ctr_threshold" in names
        assert "wire_transfer_ofac_blocked" in names
        assert result["scenario_count"] > 5

    @pytest.mark.asyncio
    async def test_sar_scenarios(self, agent):
        understanding = {
            "test_scenarios_outline": [], "requirements": [],
            "banking_rules": {"sar": {"suspicious_threshold": 5000, "insider_threshold": 0, "no_tipping_off": True}},
        }
        result = await agent.execute({}, {"workflow_id": "wf-031", "understanding": understanding})
        names = [s["name"] for s in result["scenarios"]]
        assert "sar_suspicious_activity_above_threshold" in names
        assert "sar_no_tipping_off" in names

    @pytest.mark.asyncio
    async def test_ofac_scenarios(self, agent):
        understanding = {
            "test_scenarios_outline": [], "requirements": [],
            "banking_rules": {"ofac": {"sdn_screening": True, "fuzzy_match_threshold": 85}},
        }
        result = await agent.execute({}, {"workflow_id": "wf-032", "understanding": understanding})
        names = [s["name"] for s in result["scenarios"]]
        assert "ofac_sdn_exact_match_blocked" in names
        assert "ofac_fuzzy_match_review" in names

    @pytest.mark.asyncio
    async def test_reg_e_scenarios(self, agent):
        understanding = {
            "test_scenarios_outline": [], "requirements": [],
            "banking_rules": {"reg_e": {"reporting_window_days": 60, "provisional_credit_days": 10}},
        }
        result = await agent.execute({}, {"workflow_id": "wf-033", "understanding": understanding})
        names = [s["name"] for s in result["scenarios"]]
        assert "reg_e_provisional_credit_within_10_days" in names

    @pytest.mark.asyncio
    async def test_always_has_happy_path(self, agent):
        understanding = {"test_scenarios_outline": [], "requirements": [], "banking_rules": {}}
        result = await agent.execute({}, {"workflow_id": "wf-034", "understanding": understanding})
        assert "successful_transaction" in [s["name"] for s in result["scenarios"]]

    @pytest.mark.asyncio
    async def test_section_1071_scenario(self, agent):
        understanding = {
            "test_scenarios_outline": [], "requirements": [],
            "banking_rules": {"section_1071": {"demographic_collection": True, "firewall_required": True}},
        }
        result = await agent.execute({}, {"workflow_id": "wf-035", "understanding": understanding})
        names = [s["name"] for s in result["scenarios"]]
        assert "cfpb_1071_demographic_collection" in names

    @pytest.mark.asyncio
    async def test_coverage_calculation(self, agent):
        understanding = {
            "test_scenarios_outline": [],
            "requirements": [{"type": "functional", "requirement": "test"}],
            "banking_rules": {},
        }
        result = await agent.execute({}, {"workflow_id": "wf-036", "understanding": understanding})
        assert result["coverage"]["coverage_percentage"] == 100


# ══════════════════════════════════════════════════════════════════════
# Validation Agent Tests
# ══════════════════════════════════════════════════════════════════════

class TestValidationAgent:

    @pytest.fixture
    def agent(self):
        return ValidationAgent()

    @pytest.mark.asyncio
    async def test_fully_compliant_code_scores_100(self, agent):
        code = '''
import pytest

def test_wire_transfer_audit_trail():
    """Test audit trail for BSA compliance"""
    assert audit.log_exists()

def test_ctr_threshold_10000():
    """CTR filed for transactions above $10,000"""
    assert ctr.filed_amount >= 10000

def test_ofac_sdn_screening():
    """OFAC SDN screening blocks sanctioned parties"""
    assert screener.blocked("SDN_MATCH")

def test_sar_suspicious_activity():
    """SAR filed for suspicious activity"""
    assert sar.filed is True

def test_pii_masking():
    """PII data is masked in logs"""
    assert "***" in masked_ssn
    assert redact(ssn) != ssn

def test_encryption_tls():
    """Data encrypted in transit"""
    assert connection.tls_version >= "1.2"
'''
        result = await agent.execute({}, {
            "workflow_id": "wf-040", "test_code": code,
            "scenarios": [{"name": "audit"}, {"name": "ctr"}, {"name": "ofac"}],
        })
        assert result["syntax_valid"] is True
        assert result["compliance_score"] == 100
        assert result["is_valid"] is True

    @pytest.mark.asyncio
    async def test_missing_ofac_flagged(self, agent):
        code = '''
import pytest
def test_audit():
    assert audit.log_exists()
def test_ctr_10000():
    assert amount >= 10000
'''
        result = await agent.execute({}, {"workflow_id": "wf-041", "test_code": code, "scenarios": []})
        assert any("OFAC" in i or "SDN" in i for i in result["compliance_issues"])

    @pytest.mark.asyncio
    async def test_syntax_error_fails(self, agent):
        result = await agent.execute({}, {
            "workflow_id": "wf-042", "test_code": "def test_broken(:\n    pass", "scenarios": [],
        })
        assert result["syntax_valid"] is False
        assert result["quality_score"] == 0

    @pytest.mark.asyncio
    async def test_empty_code_low_quality(self, agent):
        result = await agent.execute({}, {
            "workflow_id": "wf-043", "test_code": "", "scenarios": [{"name": "t"}],
        })
        assert result["syntax_valid"] is True
        assert result["quality_score"] < 50

    @pytest.mark.asyncio
    async def test_recommendation_approved(self, agent):
        code = '''
import pytest

@pytest.fixture
def setup():
    """Fixture docstring"""
    return {}

def test_audit_trail() -> bool:
    """Docstring"""
    assert audit.log_exists()
    assert ctr >= 10000
    assert ofac.screened
    assert sar.filed
    assert redact(pii)
    assert tls_enabled
'''
        result = await agent.execute({}, {"workflow_id": "wf-044", "test_code": code, "scenarios": [{"name": "audit_trail"}]})
        assert result["recommendation"] == "approved"


# ══════════════════════════════════════════════════════════════════════
# Execution Agent Tests
# ══════════════════════════════════════════════════════════════════════

class TestExecutionAgent:

    @pytest.fixture
    def agent(self):
        return ExecutionAgent()

    @pytest.mark.asyncio
    async def test_approved_result(self, agent):
        result = await agent.execute({}, {
            "workflow_id": "wf-050", "jira_key": "BANK-200",
            "validation": {"quality_score": 90, "is_valid": True, "compliance_score": 95},
        })
        assert result["status"] == "completed"
        assert result["is_approved"] is True
        assert "Ready for deployment" in result["next_steps"]

    @pytest.mark.asyncio
    async def test_needs_revision(self, agent):
        result = await agent.execute({}, {
            "workflow_id": "wf-051", "jira_key": "BANK-201",
            "validation": {"quality_score": 50, "is_valid": False, "compliance_score": 40,
                           "compliance_issues": ["Missing OFAC check"]},
        })
        assert result["is_approved"] is False
        assert any("revision" in s.lower() for s in result["next_steps"])

    @pytest.mark.asyncio
    async def test_no_jira_callback_ok(self, agent):
        result = await agent.execute({}, {
            "workflow_id": "wf-052", "jira_key": "BANK-202",
            "validation": {"quality_score": 80, "is_valid": True},
        })
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_jira_comment_format(self, agent):
        comment = agent._build_jira_comment({
            "is_approved": True, "quality_score": 92, "compliance_score": 95,
            "next_steps": ["Ready for deployment", "Run CI/CD"],
        })
        assert "APPROVED" in comment
        assert "92" in comment

    @pytest.mark.asyncio
    async def test_no_obligations_no_writeback(self, agent):
        """When understanding resolved no obligation_ids, writeback must not
        be attempted — artifacts.obligations_covered_count = 0, no error."""
        result = await agent.execute({}, {
            "workflow_id": "wf-053", "jira_key": "BANK-203",
            "validation": {"quality_score": 90, "is_valid": True},
            "understanding": {"obligation_ids": []},
        })
        artifacts = result["artifacts"]
        assert artifacts["obligations_covered_count"] == 0
        assert artifacts["obligations_covered"] == []
        assert artifacts["obligation_writeback_error"] is None

    @pytest.mark.asyncio
    async def test_writeback_invoked_when_pr_opened(self, agent, monkeypatch):
        """When pr_url is set + is_approved + obligation_ids non-empty, the
        writeback path must run. We stub _open_github_pr to return a URL and
        _mark_obligations_covered to record its inputs."""
        monkeypatch.setattr(
            agent, "_open_github_pr",
            lambda *args, **kwargs: _AsyncReturn("https://github.com/x/y/pull/1"),
        )
        captured: dict = {}

        async def _fake_mark(obligation_ids, workflow_id, jira_key, scenarios):
            captured["ids"] = list(obligation_ids)
            return list(obligation_ids), None

        monkeypatch.setattr(agent, "_mark_obligations_covered", _fake_mark)

        result = await agent.execute({}, {
            "workflow_id": "wf-054", "jira_key": "BANK-204",
            "validation": {"quality_score": 90, "is_valid": True},
            "understanding": {"obligation_ids": ["OBL-1", "OBL-2"]},
            "scenarios": [{"name": "happy_path"}],
            "test_code": "def test_x(): pass",
        })
        assert captured["ids"] == ["OBL-1", "OBL-2"]
        assert result["artifacts"]["obligations_covered_count"] == 2
        assert result["artifacts"]["obligations_covered"] == ["OBL-1", "OBL-2"]


class _AsyncReturn:
    """Trivial awaitable wrapper so monkeypatch can replace an async method
    with a sync lambda returning a coroutine-like."""
    def __init__(self, value):
        self._value = value
    def __await__(self):
        async def _coro():
            return self._value
        return _coro().__await__()


# ══════════════════════════════════════════════════════════════════════
# ComplianceService USA Fallback Tests
# ══════════════════════════════════════════════════════════════════════

class TestComplianceServiceUSA:

    def test_wire_transfer_rules(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        rule = svc.get_rule("wire_transfer_rules")
        assert rule is not None
        assert rule["ctr_threshold"] == 10000
        assert rule["currency"] == "USD"

    def test_sar_thresholds(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        rule = svc.get_rule("sar_thresholds")
        assert rule is not None
        assert rule["suspicious_threshold"] == 5000
        assert rule["insider_threshold"] == 0

    def test_ofac_screening(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        rule = svc.get_rule("ofac_screening")
        assert rule is not None
        assert rule["fuzzy_match_threshold"] == 85

    def test_reg_e_rules(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        rule = svc.get_rule("reg_e_rules")
        assert rule is not None
        assert rule["provisional_credit_days"] == 10
        assert rule["investigation_days"] == 45

    def test_cfpb_1071_rules(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        rule = svc.get_rule("cfpb_1071_rules")
        assert rule is not None
        assert rule["firewall_required"] is True

    def test_unknown_rule_returns_none(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        assert svc.get_rule("nonexistent_rule") is None

    def test_india_rules_removed(self):
        from src.services.compliance_service import ComplianceService
        svc = ComplianceService()
        assert svc.get_rule("upi_limits") is None
        assert svc.get_rule("neft_rules") is None
        assert svc.get_rule("pan_requirements") is None


# ══════════════════════════════════════════════════════════════════════
# E2E 6-Agent Pipeline Round-Trip Test
# ══════════════════════════════════════════════════════════════════════

class TestAgentPipelineE2E:
    """End-to-end: JIRA story → Discovery → Understanding → Framework →
    (mock) Generation → Validation → Execution round-trip."""

    @pytest.mark.asyncio
    async def test_wire_transfer_full_pipeline(self):
        jira_story = (
            "As a compliance officer, I need wire transfers above $10,000 "
            "to require dual approval, OFAC SDN screening, and CTR filing "
            "per BSA/AML regulations."
        )

        # 1. Discovery
        discovery_agent = DiscoveryAgent()
        discovery = await discovery_agent.execute({}, {
            "jira_story": jira_story, "jira_key": "BANK-E2E", "workflow_id": "wf-e2e",
        })
        assert "wire_transfer" in discovery["banking_patterns"]
        assert "ofac" in discovery["banking_patterns"]

        # 2. Understanding
        understanding_agent = UnderstandingAgent()
        understanding = await understanding_agent.execute({}, {
            "jira_story": jira_story, "workflow_id": "wf-e2e", "discovery": discovery,
        })
        assert "wire_transfer" in understanding["banking_rules"]
        assert understanding["banking_rules"]["wire_transfer"]["ctr_threshold"] == 10000

        # 3. Framework
        framework_agent = FrameworkAgent()
        framework = await framework_agent.execute({}, {
            "workflow_id": "wf-e2e", "understanding": understanding,
        })
        assert framework["scenario_count"] > 5
        scenario_names = [s["name"] for s in framework["scenarios"]]
        assert "wire_transfer_above_ctr_threshold" in scenario_names
        assert "wire_transfer_ofac_blocked" in scenario_names

        # 4. (Mock) Generation — produce compliant test code
        test_code = '''
import pytest

def test_wire_ctr_10000_audit():
    """CTR filing and audit trail"""
    assert ctr_filed(10000)
    assert audit.exists()

def test_ofac_sdn_screening():
    """OFAC sanctions check"""
    assert screener.blocked("SDN")

def test_sar_suspicious():
    """SAR filing"""
    assert sar.evaluate(6000)

def test_pii_mask_redact():
    """PII masking"""
    assert redact(ssn)

def test_tls_encrypt():
    """Encryption"""
    assert tls
'''

        # 5. Validation
        validation_agent = ValidationAgent()
        validation = await validation_agent.execute({}, {
            "workflow_id": "wf-e2e", "test_code": test_code,
            "scenarios": framework["scenarios"],
        })
        assert validation["syntax_valid"] is True
        assert validation["compliance_score"] == 100
        assert validation["is_valid"] is True

        # 6. Execution
        execution_agent = ExecutionAgent()
        execution = await execution_agent.execute({}, {
            "workflow_id": "wf-e2e", "jira_key": "BANK-E2E",
            "validation": validation, "test_code": test_code,
        })
        assert execution["status"] == "completed"
        assert execution["is_approved"] is True
        assert execution["quality_score"] > 70
