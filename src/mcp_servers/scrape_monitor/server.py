"""
src/mcp_servers/scrape_monitor/server.py
MCP Server — Scrape health monitoring and failure logging.

Tools:
  log_scrape_run      Record the result of a scrape cycle (success/failure per connector)
  log_connector_failure  Log a specific connector failure with error detail
  get_scrape_health   Return health status of all 17 connectors
  get_failed_connectors  List connectors that failed in the last N days

Writes to:
  - S3: scrape-logs/{YYYY/MM/DD}/run_{timestamp}.json
  - Aurora: scrape_runs table (queried by Agent 1 for health checks)
  - CloudWatch: custom metric fortress/scraper/connector_failures

Called by scraper_scheduler.py at the end of each cycle.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

import structlog
from mcp.server import Server
from mcp.types import Tool, TextContent

logger = structlog.get_logger(__name__)
server = Server("scrape-monitor")

_S3_BUCKET = os.getenv("S3_BUCKET", "")
_S3_LOG_PREFIX = "scrape-logs"
_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


# ── Helpers ──────────────────────────────────────────────────────────

def _write_to_s3(key: str, payload: dict) -> bool:
    """Write JSON payload to S3. Non-fatal if S3 unavailable."""
    if not _S3_BUCKET:
        return False
    try:
        import boto3
        s3 = boto3.client("s3", region_name=_AWS_REGION)
        s3.put_object(
            Bucket=_S3_BUCKET,
            Key=key,
            Body=json.dumps(payload, indent=2, default=str).encode(),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
        return True
    except Exception as exc:
        logger.warning("scrape_monitor_s3_write_failed", key=key, error=str(exc))
        return False


def _emit_cloudwatch_metric(connector: str, success: bool) -> None:
    """Emit a CloudWatch metric for connector scrape health."""
    try:
        import boto3
        cw = boto3.client("cloudwatch", region_name=_AWS_REGION)
        cw.put_metric_data(
            Namespace="Fortress/Scraper",
            MetricData=[{
                "MetricName": "ConnectorFailure" if not success else "ConnectorSuccess",
                "Dimensions": [{"Name": "Connector", "Value": connector}],
                "Value": 1,
                "Unit": "Count",
                "Timestamp": datetime.now(timezone.utc),
            }]
        )
    except Exception:
        pass  # Metric emission is non-fatal


async def _write_to_aurora(table_data: dict) -> None:
    """Write scrape run record to Aurora scrape_runs table."""
    try:
        from src.services.database import get_db
        from sqlalchemy import text
        async with get_db() as session:
            await session.execute(text("""
                INSERT INTO scrape_runs
                    (id, run_at, connector_name, success, rules_extracted,
                     rules_changed, error_message, duration_seconds, s3_key)
                VALUES
                    (gen_random_uuid(), :run_at, :connector, :success,
                     :extracted, :changed, :error, :duration, :s3_key)
            """), table_data)
    except Exception as exc:
        logger.warning("scrape_monitor_aurora_write_failed", error=str(exc))


# ── MCP Tool definitions ──────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> List[Tool]:
    return [
        Tool(
            name="log_scrape_run",
            description=(
                "Record the complete result of a scrape cycle. "
                "Call once per connector after each scrape attempt. "
                "Writes to S3, Aurora, and CloudWatch."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connector_name": {"type": "string",
                        "description": "Connector identifier e.g. occ_bsa, fincen_sar"},
                    "success": {"type": "boolean",
                        "description": "True if connector completed without error"},
                    "rules_extracted": {"type": "integer", "default": 0},
                    "rules_changed": {"type": "integer", "default": 0,
                        "description": "Rules that differed from previous cycle"},
                    "duration_seconds": {"type": "number", "default": 0},
                    "error_message": {"type": "string", "default": ""},
                    "source_url": {"type": "string", "default": ""},
                },
                "required": ["connector_name", "success"],
            },
        ),
        Tool(
            name="log_connector_failure",
            description=(
                "Log a specific connector failure with full error context. "
                "Used for immediate alerting when a connector crashes mid-cycle."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connector_name": {"type": "string"},
                    "error_type": {"type": "string",
                        "description": "Exception class e.g. ConnectionError, TimeoutError"},
                    "error_message": {"type": "string"},
                    "traceback": {"type": "string", "default": ""},
                    "source_url": {"type": "string", "default": ""},
                    "retry_count": {"type": "integer", "default": 0},
                },
                "required": ["connector_name", "error_type", "error_message"],
            },
        ),
        Tool(
            name="get_scrape_health",
            description=(
                "Return health status of all 17 connectors "
                "based on the last N scrape runs. "
                "Returns: per-connector success rate, last success time, "
                "consecutive failures."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 7,
                        "description": "How many days of history to check"},
                },
            },
        ),
        Tool(
            name="get_failed_connectors",
            description=(
                "List connectors that failed in the last N days. "
                "Use this in Agent 1 health checks and CLAUDE.md debug runbook."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 1},
                    "min_failures": {"type": "integer", "default": 1,
                        "description": "Minimum number of failures to include"},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> List[TextContent]:

    if name == "log_scrape_run":
        connector = arguments["connector_name"]
        success = arguments["success"]
        now = datetime.now(timezone.utc)

        payload = {
            "run_at": now.isoformat(),
            "connector_name": connector,
            "success": success,
            "rules_extracted": arguments.get("rules_extracted", 0),
            "rules_changed": arguments.get("rules_changed", 0),
            "duration_seconds": arguments.get("duration_seconds", 0),
            "error_message": arguments.get("error_message", ""),
            "source_url": arguments.get("source_url", ""),
        }

        # S3 log
        date_str = now.strftime("%Y/%m/%d")
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        s3_key = f"{_S3_LOG_PREFIX}/{date_str}/{connector}_{ts_str}.json"
        s3_ok = _write_to_s3(s3_key, payload)

        # Aurora
        await _write_to_aurora({**payload, "s3_key": s3_key if s3_ok else ""})

        # CloudWatch metric
        _emit_cloudwatch_metric(connector, success)

        status = "success" if success else "FAILURE"
        logger.info(
            "scrape_run_logged",
            connector=connector,
            status=status,
            rules=payload["rules_extracted"],
            s3=s3_ok,
        )

        return [TextContent(
            type="text",
            text=json.dumps({
                "logged": True,
                "connector": connector,
                "status": status,
                "s3_key": s3_key if s3_ok else "s3_unavailable",
            })
        )]

    elif name == "log_connector_failure":
        connector = arguments["connector_name"]
        now = datetime.now(timezone.utc)

        payload = {
            "event": "connector_failure",
            "timestamp": now.isoformat(),
            "connector_name": connector,
            "error_type": arguments["error_type"],
            "error_message": arguments["error_message"],
            "traceback": arguments.get("traceback", ""),
            "source_url": arguments.get("source_url", ""),
            "retry_count": arguments.get("retry_count", 0),
        }

        date_str = now.strftime("%Y/%m/%d")
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        s3_key = f"{_S3_LOG_PREFIX}/{date_str}/FAILURE_{connector}_{ts_str}.json"
        _write_to_s3(s3_key, payload)

        # Always log to CloudWatch with ERROR severity for alerting
        _emit_cloudwatch_metric(connector, success=False)

        logger.error(
            "connector_failure_logged",
            connector=connector,
            error_type=arguments["error_type"],
            error=arguments["error_message"],
        )

        return [TextContent(
            type="text",
            text=json.dumps({"logged": True, "s3_key": s3_key, **payload})
        )]

    elif name == "get_scrape_health":
        days = arguments.get("days", 7)
        since = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            from src.services.database import get_db
            from sqlalchemy import text

            async with get_db() as session:
                rows = await session.execute(text("""
                    SELECT
                        connector_name,
                        COUNT(*) AS total_runs,
                        SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes,
                        MAX(CASE WHEN success THEN run_at END) AS last_success,
                        MAX(run_at) AS last_run,
                        SUM(rules_extracted) AS total_rules,
                        SUM(rules_changed) AS total_changed
                    FROM scrape_runs
                    WHERE run_at >= :since
                    GROUP BY connector_name
                    ORDER BY connector_name
                """), {"since": since})

                health = []
                for r in rows.fetchall():
                    success_rate = (r.successes / r.total_runs * 100) if r.total_runs else 0
                    health.append({
                        "connector": r.connector_name,
                        "success_rate_pct": round(success_rate, 1),
                        "total_runs": r.total_runs,
                        "last_success": str(r.last_success),
                        "last_run": str(r.last_run),
                        "total_rules_extracted": r.total_rules,
                        "total_rules_changed": r.total_changed,
                        "status": "healthy" if success_rate >= 80 else "degraded" if success_rate >= 50 else "failing",
                    })

            return [TextContent(type="text", text=json.dumps({
                "days_checked": days,
                "connectors": health,
                "unhealthy": [c for c in health if c["status"] != "healthy"],
            }, indent=2))]

        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({
                "error": str(exc),
                "hint": "scrape_runs table may not exist — run schema setup"
            }))]

    elif name == "get_failed_connectors":
        days = arguments.get("days", 1)
        min_failures = arguments.get("min_failures", 1)
        since = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            from src.services.database import get_db
            from sqlalchemy import text

            async with get_db() as session:
                rows = await session.execute(text("""
                    SELECT
                        connector_name,
                        COUNT(*) AS failure_count,
                        MAX(run_at) AS last_failure,
                        MAX(error_message) AS last_error
                    FROM scrape_runs
                    WHERE run_at >= :since AND success = FALSE
                    GROUP BY connector_name
                    HAVING COUNT(*) >= :min_failures
                    ORDER BY failure_count DESC
                """), {"since": since, "min_failures": min_failures})

                failed = [
                    {
                        "connector": r.connector_name,
                        "failure_count": r.failure_count,
                        "last_failure": str(r.last_failure),
                        "last_error": r.last_error,
                    }
                    for r in rows.fetchall()
                ]

            return [TextContent(type="text", text=json.dumps({
                "days_checked": days,
                "failed_connectors": failed,
                "total_failing": len(failed),
            }, indent=2))]

        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
