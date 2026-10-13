"""
src/api/generate_test_api.py
Dashboard → JIRA story → 6-agent pipeline → GitHub PR → CI/CD.

POST /api/v1/rules/{rule_id}/generate-test  single rule
POST /api/v1/rules/bulk-generate            multiple rules
GET  /api/v1/rules/{rule_id}/test-status    poll PR + pipeline status
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import text

from src.api.auth import CurrentUser, require_cco
from src.api.webhooks import process_workflow
from src.integrations.jira_client import JIRAClient
from src.services.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/rules", tags=["generate-test"])

_JIRA_PROJECT = os.getenv("JIRA_PROJECT_KEY", "COMP")


class GenerateTestRequest(BaseModel):
    story_title: Optional[str] = None
    jira_project: Optional[str] = None
    threshold_override: Optional[str] = None
    priority: str = "High"


class BulkGenerateRequest(BaseModel):
    rule_ids: List[str]
    jira_project: Optional[str] = None
    priority: str = "High"


@router.post("/{rule_id}/generate-test")
async def generate_test_for_rule(
    rule_id: str,
    body: GenerateTestRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_cco),
) -> dict:
    """CCO triggers test generation. Creates JIRA story + kicks off 6-agent pipeline inline."""
    async with get_db() as session:
        row = await session.execute(text("""
            SELECT rule_id, rule_text, regulator, cfr_citation,
                   enforcement_risk_score
            FROM compliance_rules
            WHERE rule_id=:rid AND is_active=TRUE
        """), {"rid": rule_id})
        rule = row.fetchone()

    if not rule:
        raise HTTPException(404, f"Rule {rule_id} not found or inactive")

    rule = dict(rule._mapping)
    project = body.jira_project or _JIRA_PROJECT
    title = body.story_title or _default_title(rule)
    description = _build_description(rule, body.threshold_override, user)

    try:
        client = JIRAClient()
        issue = client.create_story(
            project=project,
            title=title,
            description=description,
            priority=body.priority,
            labels=["compliance", "fortress-generated",
                    rule["regulator"].lower().replace(" ", "-")],
        )
    except Exception as exc:
        logger.error("jira_create_failed", rule_id=rule_id, error=str(exc))
        raise HTTPException(502, f"JIRA story creation failed: {exc}")

    # Insert workflow row in RUNNING state and capture the id so the background
    # task uses the same workflow_id (and the webhook handler, if JIRA also
    # round-trips, finds and reuses this row instead of duplicating it).
    # NOTE: use CAST(:ctx AS json), not :ctx::json — SQLAlchemy's text() bind
    # parser doesn't tolerate the PostgreSQL `::type` cast directly after a
    # named bind parameter (it fails to substitute `:ctx`).
    ctx_json = json.dumps({"jira_story": description or "", "summary": title or ""})
    async with get_db() as session:
        row = await session.execute(text("""
            INSERT INTO workflow_executions
                (id, jira_story_id, jira_story_key, rule_id, status,
                 context_data, artifacts)
            VALUES
                (gen_random_uuid(), :jira_key, :jira_key, :rule_id, 'running',
                 CAST(:ctx AS json), CAST('{}' AS json))
            RETURNING id
        """), {
            "jira_key": issue["key"],
            "rule_id": rule_id,
            "ctx": ctx_json,
        })
        workflow_id = str(row.scalar_one())
        await session.commit()

    # Kick off the 6-agent pipeline without waiting for a JIRA webhook callback.
    background.add_task(process_workflow, workflow_id, issue["key"], description)

    logger.info("generate_test_requested",
                rule_id=rule_id, jira_key=issue["key"],
                workflow_id=workflow_id, by=user.email)
    return {
        "rule_id": rule_id,
        "jira_key": issue["key"],
        "workflow_id": workflow_id,
        "jira_url": f"{os.getenv('JIRA_URL', '')}/browse/{issue['key']}",
        "status": "pipeline_started",
        "message": "6-agent pipeline will open a GitHub PR within ~5 minutes",
    }


@router.post("/bulk-generate")
async def bulk_generate(
    body: BulkGenerateRequest,
    user: CurrentUser = Depends(require_cco),
) -> dict:
    results = []
    project = body.jira_project or _JIRA_PROJECT
    client = JIRAClient()

    async with get_db() as session:
        for rule_id in body.rule_ids[:20]:
            row = await session.execute(text("""
                SELECT rule_id, rule_text, regulator, cfr_citation,
                       enforcement_risk_score
                FROM compliance_rules WHERE rule_id=:rid AND is_active=TRUE
            """), {"rid": rule_id})
            rule = row.fetchone()
            if not rule:
                results.append({"rule_id": rule_id, "status": "not_found"})
                continue
            rule = dict(rule._mapping)
            try:
                issue = client.create_story(
                    project=project,
                    title=_default_title(rule),
                    description=_build_description(rule, None, user),
                    priority=body.priority,
                    labels=["compliance", "fortress-generated", "bulk"],
                )
                await session.execute(text("""
                    INSERT INTO workflow_executions
                        (id, jira_story_id, jira_story_key, rule_id, status,
                         context_data, artifacts)
                    VALUES
                        (gen_random_uuid(), :jira_key, :jira_key, :rule_id,
                         'pending', '{}'::json, '{}'::json)
                """), {"jira_key": issue["key"], "rule_id": rule_id})
                await session.commit()
                results.append({"rule_id": rule_id,
                                 "jira_key": issue["key"], "status": "created"})
            except Exception as exc:
                results.append({"rule_id": rule_id,
                                 "status": "failed", "error": str(exc)})

    created = sum(1 for r in results if r["status"] == "created")
    return {"results": results, "created": created, "total": len(body.rule_ids)}


@router.get("/{rule_id}/test-status")
async def get_test_status(
    rule_id: str,
    user: CurrentUser = Depends(
        __import__("src.api.auth", fromlist=["get_current_user"]).get_current_user
    ),
) -> dict:
    async with get_db() as session:
        wf = await session.execute(text("""
            SELECT id, jira_story_key, status, started_at,
                   completed_at, artifacts
            FROM workflow_executions
            WHERE rule_id = :rule_id
            ORDER BY started_at DESC LIMIT 1
        """), {"rule_id": rule_id})
        row = wf.fetchone()

    if not row:
        return {"rule_id": rule_id, "status": "no_workflow", "pr_url": None}

    row = dict(row._mapping)
    artifacts = row.get("artifacts") or {}
    return {
        "rule_id": rule_id,
        "jira_key": row.get("jira_story_key"),
        "status": row.get("status"),
        "pr_url": artifacts.get("github_pr_url"),
    }


# ── Helpers ───────────────────────────────────────────────────────────

def _default_title(rule: dict) -> str:
    cfr = rule.get("cfr_citation", "")
    name = rule.get("regulator", "")
    return f"Add compliance test: {name} — {cfr}".strip(" —")


def _build_description(rule: dict, override: Optional[str],
                        user: CurrentUser) -> str:
    risk = rule.get("enforcement_risk_score") or 0.0
    lines = [
        "*Compliance test required — generated by Fortress Labs CCO Dashboard*",
        "",
        f"||Attribute||Value||",
        f"|Rule ID|{rule['rule_id']}|",
        f"|Regulator|{rule['regulator']}|",
        f"|CFR Citation|{rule.get('cfr_citation', 'N/A')}|",
        f"|Regulation|{rule.get('regulator', '')}|",
        f"|Enforcement Risk|{risk:.2f} / 1.0|",
        "",
        "*Obligation:*",
        (rule.get("rule_text") or "")[:500],
        "",
    ]
    if override:
        lines += [f"*Bank threshold override:* {override}", ""]
    lines += [
        "*Acceptance criteria:*",
        f"# All assertions must cite {rule.get('cfr_citation', 'the relevant CFR')}",
        "# Test must cover threshold boundary values",
        "# Audit log entry required for every compliance decision",
        "",
        f"_Generated by: {user.email} ({user.role}) via Fortress Dashboard_",
    ]
    return "\n".join(lines)
