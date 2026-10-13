"""Test Patterns MCP Server — Banking test generation patterns.

Provides tools for querying test pattern templates used by the Generation agent
to produce compliance-aware pytest code.
"""
from mcp.server import Server
from mcp.types import Tool, TextContent
import json
import structlog

logger = structlog.get_logger(__name__)

server = Server("test-patterns")

# ── USA Banking Test Pattern Templates ─────────────────────────────

_TEST_PATTERNS = {
    "wire_transfer_ctr": {
        "pattern_name": "Wire Transfer CTR Threshold Test",
        "description": "Tests wire transfer CTR filing at the $10,000 threshold",
        "template": '''
import pytest
from decimal import Decimal

class TestWireTransferCTR:
    """BSA/AML: CTR filing for wire transfers >= $10,000"""

    def test_wire_below_threshold_no_ctr(self, wire_service):
        result = wire_service.transfer(amount=Decimal("9999.99"))
        assert result.success is True
        assert result.ctr_filed is False

    def test_wire_at_threshold_ctr_filed(self, wire_service):
        result = wire_service.transfer(amount=Decimal("10000.00"))
        assert result.success is True
        assert result.ctr_filed is True

    def test_wire_above_threshold_ctr_and_dual_approval(self, wire_service):
        result = wire_service.transfer(amount=Decimal("50000.00"))
        assert result.success is True
        assert result.ctr_filed is True
        assert result.dual_approval_obtained is True
''',
        "tags": ["wire_transfer", "ctr", "bsa_aml"],
    },
    "ofac_screening": {
        "pattern_name": "OFAC SDN Screening Test",
        "description": "Tests OFAC sanctions screening with exact and fuzzy matching",
        "template": '''
import pytest

class TestOFACScreening:
    """OFAC: SDN list screening with fuzzy name matching"""

    def test_exact_sdn_match_blocked(self, ofac_screener):
        result = ofac_screener.screen("SPECIALLY DESIGNATED NATIONAL")
        assert result.blocked is True
        assert result.match_type == "exact"

    def test_fuzzy_match_above_threshold_held(self, ofac_screener):
        result = ofac_screener.screen("SPECIALLY DESGNATED NATNL")
        assert result.held_for_review is True
        assert result.match_score >= 85

    def test_no_match_passes(self, ofac_screener):
        result = ofac_screener.screen("John Smith")
        assert result.blocked is False
        assert result.held_for_review is False

    def test_false_positive_release(self, ofac_screener):
        result = ofac_screener.review_and_release(match_id="FP-001", is_false_positive=True)
        assert result.released is True
        assert result.documented is True
''',
        "tags": ["ofac", "sanctions", "sdn"],
    },
    "sar_filing": {
        "pattern_name": "SAR Filing Test",
        "description": "Tests SAR filing thresholds and confidentiality requirements",
        "template": '''
import pytest
from decimal import Decimal

class TestSARFiling:
    """FinCEN: Suspicious Activity Report filing"""

    def test_suspicious_above_5k_sar_filed(self, sar_service):
        result = sar_service.evaluate(amount=Decimal("5001"), is_suspicious=True)
        assert result.sar_required is True

    def test_suspicious_below_5k_no_sar(self, sar_service):
        result = sar_service.evaluate(amount=Decimal("4999"), is_suspicious=True)
        assert result.sar_required is False

    def test_insider_abuse_any_amount(self, sar_service):
        result = sar_service.evaluate(amount=Decimal("1"), is_suspicious=True, is_insider=True)
        assert result.sar_required is True

    def test_no_tipping_off(self, sar_service):
        result = sar_service.get_customer_notification(sar_filed=True)
        assert "suspicious" not in result.message.lower()
        assert "sar" not in result.message.lower()
''',
        "tags": ["sar", "bsa_aml", "fincen"],
    },
    "reg_e_dispute": {
        "pattern_name": "Regulation E Dispute Resolution Test",
        "description": "Tests Reg E error resolution timelines and provisional credit",
        "template": '''
import pytest
from datetime import datetime, timedelta

class TestRegEDispute:
    """CFPB: Regulation E error resolution"""

    def test_dispute_within_60_days_accepted(self, dispute_service):
        txn_date = datetime.now() - timedelta(days=30)
        result = dispute_service.file_dispute(transaction_date=txn_date)
        assert result.accepted is True

    def test_dispute_after_60_days_rejected(self, dispute_service):
        txn_date = datetime.now() - timedelta(days=61)
        result = dispute_service.file_dispute(transaction_date=txn_date)
        assert result.accepted is False

    def test_provisional_credit_within_10_days(self, dispute_service):
        result = dispute_service.process(dispute_id="D-001", days_elapsed=10)
        assert result.provisional_credit_issued is True

    def test_investigation_complete_within_45_days(self, dispute_service):
        result = dispute_service.process(dispute_id="D-001", days_elapsed=45)
        assert result.investigation_complete is True
''',
        "tags": ["reg_e", "cfpb", "dispute"],
    },
    "structuring_detection": {
        "pattern_name": "Structuring Detection Test",
        "description": "Tests detection of structured transactions designed to avoid CTR",
        "template": '''
import pytest
from decimal import Decimal

class TestStructuringDetection:
    """BSA/AML: Structuring detection for CTR avoidance"""

    def test_multiple_transactions_under_threshold_flagged(self, monitoring_service):
        transactions = [Decimal("3500"), Decimal("3500"), Decimal("3500")]
        result = monitoring_service.check_structuring(transactions, window_hours=24)
        assert result.structuring_detected is True
        assert result.aggregate_amount == Decimal("10500")

    def test_single_transaction_under_threshold_ok(self, monitoring_service):
        result = monitoring_service.check_structuring([Decimal("5000")], window_hours=24)
        assert result.structuring_detected is False
''',
        "tags": ["structuring", "ctr", "bsa_aml"],
    },
}


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="query_test_pattern",
            description="Query banking test pattern templates by name, tag, or keyword",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Pattern name, tag, or keyword"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_test_patterns",
            description="List all available banking test pattern names",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_test_template",
            description="Get the full pytest template code for a specific pattern",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_key": {"type": "string", "description": "Pattern key (e.g. 'wire_transfer_ctr')"},
                },
                "required": ["pattern_key"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "list_test_patterns":
        patterns = {k: v["pattern_name"] for k, v in _TEST_PATTERNS.items()}
        return [TextContent(type="text", text=json.dumps({"patterns": patterns}))]

    if name == "get_test_template":
        key = arguments.get("pattern_key", "")
        pattern = _TEST_PATTERNS.get(key)
        if pattern:
            return [TextContent(type="text", text=json.dumps({"template": pattern["template"]}))]
        return [TextContent(type="text", text=json.dumps({"error": f"Pattern not found: {key}"}))]

    if name == "query_test_pattern":
        query = (arguments.get("query") or "").lower()
        results = []

        for key, pattern in _TEST_PATTERNS.items():
            if (query in key or
                query in pattern["pattern_name"].lower() or
                query in pattern["description"].lower() or
                any(query in tag for tag in pattern["tags"])):
                results.append({
                    "key": key,
                    "pattern_name": pattern["pattern_name"],
                    "description": pattern["description"],
                    "tags": pattern["tags"],
                })

        return [TextContent(type="text", text=json.dumps({"patterns": results}))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
