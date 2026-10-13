"""Banking Workflows MCP Server — USA banking compliance workflows.

Provides tools for querying banking workflow patterns used by the 6-agent pipeline.
Backed by a hardcoded workflow registry (USA banking compliance reference patterns).
"""
from mcp.server import Server
from mcp.types import Tool, TextContent
import json
import structlog

logger = structlog.get_logger(__name__)

server = Server("banking-workflows")

# ── Hardcoded USA banking workflows ──

_USA_WORKFLOWS = {
    "wire_transfer": {
        "workflow_name": "Domestic Wire Transfer",
        "description": "End-to-end wire transfer with BSA/AML compliance checks",
        "steps": [
            "Validate originator identity (CIP)",
            "Screen beneficiary against OFAC SDN list",
            "Check CTR threshold ($10,000)",
            "Obtain dual approval if above threshold",
            "Include Travel Rule data for transfers > $3,000",
            "Execute wire via Fedwire/SWIFT",
            "Record audit trail",
            "File CTR with FinCEN if applicable",
        ],
        "business_rules": "CTR required > $10,000. OFAC screening mandatory. Travel Rule > $3,000.",
        "test_scenarios": "below_threshold,at_threshold,above_threshold,ofac_blocked,structuring",
    },
    "sar_filing": {
        "workflow_name": "SAR Filing Workflow",
        "description": "Suspicious Activity Report filing with FinCEN",
        "steps": [
            "Transaction monitoring detects anomaly",
            "Alert reviewed by compliance analyst",
            "Gather supporting documentation",
            "File SAR within 30 days of detection",
            "Do NOT notify the subject (no tipping off)",
            "Retain SAR and supporting docs for 5 years",
        ],
        "business_rules": "SAR threshold: $5,000 (or $0 for insider abuse). 30-day filing deadline.",
        "test_scenarios": "above_threshold,insider_abuse,no_tipping_off,filing_deadline",
    },
    "ofac_screening": {
        "workflow_name": "OFAC SDN Screening",
        "description": "Real-time sanctions screening against OFAC lists",
        "steps": [
            "Extract beneficiary/originator names",
            "Run exact match against SDN list",
            "Run fuzzy match (85% threshold)",
            "Block exact matches immediately",
            "Queue fuzzy matches for manual review",
            "Report blocked transactions within 10 days",
        ],
        "business_rules": "100% screening required. Fuzzy match >= 85%. Block and report.",
        "test_scenarios": "exact_match,fuzzy_match,false_positive,no_match",
    },
    "reg_e_dispute": {
        "workflow_name": "Regulation E Error Resolution",
        "description": "Consumer dispute resolution for unauthorized EFTs",
        "steps": [
            "Consumer reports unauthorized EFT (within 60 days)",
            "Bank acknowledges within 1 business day",
            "Investigate the claim",
            "If not resolved in 10 days, issue provisional credit",
            "Complete investigation within 45 days (90 for new accounts)",
            "Notify consumer of results in writing",
        ],
        "business_rules": "60-day reporting window. 10-day provisional credit. 45-day investigation.",
        "test_scenarios": "within_window,expired_window,provisional_credit,investigation_complete",
    },
    "account_opening_kyc": {
        "workflow_name": "Account Opening with KYC/CIP",
        "description": "New account opening with Customer Identification Program",
        "steps": [
            "Collect customer information (name, DOB, address, SSN/TIN)",
            "Verify identity (documentary or non-documentary)",
            "Screen against OFAC SDN list",
            "Assess risk level (standard, medium, high)",
            "Apply CDD / Enhanced Due Diligence if high-risk",
            "Collect beneficial ownership for entities (> 25%)",
            "Open account and record CIP documentation",
        ],
        "business_rules": "CIP mandatory. OFAC screening. CDD for all. EDD for high-risk.",
        "test_scenarios": "standard_risk,high_risk,ofac_hit,missing_docs,beneficial_ownership",
    },
}


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="query_banking_workflow",
            description="Query USA banking workflow patterns by name or keyword",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Workflow name or search keyword"}
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_banking_workflows",
            description="List all available USA banking workflow names",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "list_banking_workflows":
        names = [v["workflow_name"] for v in _USA_WORKFLOWS.values()]
        return [TextContent(type="text", text=json.dumps({"workflows": names}))]

    if name == "query_banking_workflow":
        query = (arguments.get("query") or "").lower()
        results = []
        for key, wf in _USA_WORKFLOWS.items():
            if query in key or query in wf["workflow_name"].lower() or query in wf["description"].lower():
                results.append(wf)

        return [TextContent(type="text", text=json.dumps({"workflows": results}))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
