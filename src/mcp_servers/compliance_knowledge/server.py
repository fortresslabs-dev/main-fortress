"""Compliance Knowledge MCP Server — USA regulatory compliance rules.

Provides tools for querying US banking compliance rules used by the 6-agent pipeline.
Backed by ComplianceService (DB) with hardcoded fallback.
"""
from mcp.server import Server
from mcp.types import Tool, TextContent
import json
import structlog

logger = structlog.get_logger(__name__)

server = Server("compliance-knowledge")

# ── USA Compliance Knowledge Base (fallback) ───────────────────────

_USA_COMPLIANCE = {
    "bsa_aml": {
        "regulator": "FinCEN / OCC",
        "regulation": "Bank Secrecy Act / Anti-Money Laundering",
        "key_rules": [
            "CTR required for cash transactions > $10,000",
            "SAR required for suspicious activity > $5,000",
            "SAR for insider abuse at any amount",
            "Recordkeeping for wire transfers > $3,000 (Travel Rule)",
            "5-year retention for BSA records",
            "Structuring detection across related transactions",
        ],
        "thresholds": {"ctr": 10000, "sar": 5000, "travel_rule": 3000},
    },
    "ofac": {
        "regulator": "OFAC / Treasury",
        "regulation": "OFAC Sanctions Compliance",
        "key_rules": [
            "Screen all parties against SDN list before funds transfer",
            "Fuzzy name matching with configurable threshold (default 85%)",
            "Block transactions involving sanctioned parties",
            "Report blocked transactions within 10 business days",
            "Maintain screening logs for 5 years",
        ],
        "thresholds": {"fuzzy_match_pct": 85, "reporting_days": 10},
    },
    "reg_e": {
        "regulator": "CFPB",
        "regulation": "Regulation E — Electronic Fund Transfer Act",
        "key_rules": [
            "Consumer has 60 days to report unauthorized EFT",
            "Bank must provide provisional credit within 10 business days",
            "Investigation must complete within 45 days",
            "Extended to 90 days for new accounts (< 30 days)",
            "Written notice of investigation results required",
        ],
        "thresholds": {
            "reporting_window_days": 60,
            "provisional_credit_days": 10,
            "investigation_days": 45,
            "new_account_investigation_days": 90,
        },
    },
    "cfpb_1071": {
        "regulator": "CFPB",
        "regulation": "Section 1071 — Small Business Lending Data Collection",
        "key_rules": [
            "Collect demographic data at loan application",
            "Firewall demographic data from underwriting decisions",
            "Annual LAR data submission to CFPB",
            "Applies to covered financial institutions",
            "Effective date: July 2026",
        ],
        "thresholds": {},
    },
    "cip_cdd": {
        "regulator": "FinCEN / OCC",
        "regulation": "Customer Identification Program / Customer Due Diligence",
        "key_rules": [
            "Verify identity of all new customers (CIP)",
            "Collect beneficial ownership for entities (> 20% ownership, amended 2026-04-11)",
            "Apply Enhanced Due Diligence for high-risk customers",
            "Ongoing monitoring of customer activity",
            "Risk-based approach to KYC",
        ],
        "thresholds": {"beneficial_ownership_pct": 20},
    },
    "glba": {
        "regulator": "FTC / OCC",
        "regulation": "Gramm-Leach-Bliley Act — Financial Privacy",
        "key_rules": [
            "Protect consumer Non-Public Personal Information (NPI)",
            "SSN must be masked (show last 4 only)",
            "Encryption required in transit (TLS 1.2+) and at rest",
            "Privacy notice required at account opening",
            "Opt-out right for information sharing",
        ],
        "thresholds": {},
    },
}


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="query_compliance_rule",
            description="Query US banking compliance rules by regulation name or keyword",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regulation name or keyword (e.g. 'bsa_aml', 'ofac', 'reg_e')"}
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_threshold",
            description="Get numeric compliance threshold for a specific rule",
            inputSchema={
                "type": "object",
                "properties": {
                    "regulation": {"type": "string", "description": "Regulation key (e.g. 'bsa_aml')"},
                    "threshold_name": {"type": "string", "description": "Threshold name (e.g. 'ctr', 'sar')"},
                },
                "required": ["regulation", "threshold_name"],
            },
        ),
        Tool(
            name="list_regulations",
            description="List all available US compliance regulations",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "list_regulations":
        regs = {k: v["regulation"] for k, v in _USA_COMPLIANCE.items()}
        return [TextContent(type="text", text=json.dumps({"regulations": regs}))]

    if name == "get_threshold":
        reg = arguments.get("regulation", "")
        threshold_name = arguments.get("threshold_name", "")
        entry = _USA_COMPLIANCE.get(reg, {})
        value = entry.get("thresholds", {}).get(threshold_name)
        return [TextContent(type="text", text=json.dumps({"value": value}))]

    if name == "query_compliance_rule":
        query = (arguments.get("query") or "").lower()
        results = []

        # Try DB-backed ComplianceService first
        try:
            from src.services.compliance_service import ComplianceService
            svc = ComplianceService()
            rule = svc.get_rule(query)
            if rule:
                results.append({"source": "database", "rule": rule})
        except Exception:
            pass

        # Fallback to hardcoded knowledge
        if not results:
            for key, entry in _USA_COMPLIANCE.items():
                if query in key or query in entry["regulation"].lower():
                    results.append(entry)

        return [TextContent(type="text", text=json.dumps({"rules": results}))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
