"""
Pipeline 2 Integration Tests — Customer Docker (6-Agent Pipeline + Validators).

Tests:
  - FortressAPIClient PII sanitizer blocks leaks
  - ComplianceService falls back to defaults when DB is empty
  - Connector PluginManager loads USA regulatory connectors
"""
import pytest
import pytest_asyncio

from src.services.fortress_api_client import FortressAPIClient, DataPrivacyError
from src.services.compliance_service import ComplianceService


# ── FortressAPIClient PII Sanitizer ─────────────────────────────────

class TestFortressAPIClientPII:
    """Verify the PII deny-list guard on outbound requests."""

    def test_pii_params_raises_error(self):
        client = FortressAPIClient(base_url="http://dummy:8000")
        with pytest.raises(DataPrivacyError, match="account_number"):
            client._check_pii({"geography": "usa", "account_number": "123456789"})

    def test_clean_params_pass(self):
        client = FortressAPIClient(base_url="http://dummy:8000")
        # Should NOT raise
        client._check_pii({"geography": "usa", "regulator": "occ", "domain": "wire_transfer"})

    def test_multiple_pii_fields_all_caught(self):
        client = FortressAPIClient(base_url="http://dummy:8000")
        with pytest.raises(DataPrivacyError):
            client._check_pii({"ssn": "123-45-6789", "beneficiary_name": "John Doe"})


# ── ComplianceService Fallback ──────────────────────────────────────

class TestComplianceServiceFallback:
    """Verify the service returns hardcoded defaults when DB is unavailable."""

    def test_get_rule_returns_fallback(self):
        svc = ComplianceService()
        rule = svc.get_rule("wire_transfer_rules")
        assert rule is not None
        assert rule["ctr_threshold"] == 10000

    def test_unknown_rule_returns_none(self):
        svc = ComplianceService()
        assert svc.get_rule("nonexistent_rule") is None


# ── PluginManager Connector Loading ─────────────────────────────────

class TestPluginManagerUSA:
    """Verify USA connectors can be discovered and loaded."""

    def test_load_usa_connectors(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        connectors = mgr.load_connectors()

        # At least the regulatory connectors we created should load
        regulatory = {k for k in connectors if k.startswith("regulatory.")}
        assert len(regulatory) >= 3, f"Expected >=3 regulatory connectors, got {regulatory}"

    def test_get_compliance_rules_returns_data(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        mgr.load_connectors()
        rules = mgr.get_compliance_rules()
        assert isinstance(rules, dict)
        # OCC BSA connector should contribute rules
        assert len(rules) > 0
