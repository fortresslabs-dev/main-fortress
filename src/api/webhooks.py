"""Webhook Handlers - Complete Implementation"""
import hashlib
import hmac
import json
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
import structlog

from src.orchestration.workflow_engine import WorkflowOrchestrator
from src.services.database import get_db
from src.models.database import WorkflowExecution, WorkflowStatus

router = APIRouter()
logger = structlog.get_logger()

# Cluster-wide FIFO serialization for the JIRA → 6-agent pipeline.
# Postgres queues pg_advisory_lock waiters in arrival order, so multiple
# ECS tasks all funnel through one Bedrock invocation at a time.
# Value: int.from_bytes(b"p2sg", "big") — "P2 Story Generation".
_WORKFLOW_LOCK_KEY = 0x70327367


def adf_to_text(node) -> str:
    """Normalise JIRA Cloud ADF description to plain text.

    ADF looks like:
      {"type": "doc", "content": [{"type": "paragraph",
       "content": [{"type": "text", "text": "..."}]}]}

    Walks `content` recursively, collecting every `text` node value
    and joining with newlines. Strings pass through; None / {} → "".
    """
    if node is None or node == {}:
        return ""
    if isinstance(node, str):
        return node
    parts: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            parts.append(node["text"])
        for child in node.get("content", []) or []:
            text = adf_to_text(child)
            if text:
                parts.append(text)
    elif isinstance(node, list):
        for child in node:
            text = adf_to_text(child)
            if text:
                parts.append(text)
    return "\n".join(parts)


def _authenticate(request: Request, body: bytes) -> None:
    """Fail-closed auth for /webhooks/jira.

    JIRA_AUTH_MODE:
      bearer  (default, JIRA Cloud) — Authorization: Bearer <secret>
      hmac    (JIRA Server / DC)    — X-Hub-Signature-256: sha256=<hex>

    DEV_MODE=true bypasses auth ONLY when ENVIRONMENT != "production".
    A stray DEV_MODE on a prod stack must not silently disable webhook
    signature verification — the bypass fails closed in that case.
    Never logs the secret value.
    """
    if os.environ.get("DEV_MODE", "").lower() == "true":
        env = os.environ.get("ENVIRONMENT", "").lower()
        if env == "production":
            logger.error("jira_webhook_dev_mode_in_production_refused", env=env)
            raise HTTPException(401, "Authentication required")
        logger.warning("jira_webhook_dev_mode_bypass", env=env)
        return
    secret = os.environ.get("JIRA_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("jira_webhook_secret_unset")
        raise HTTPException(401, "Authentication required")
    mode = os.environ.get("JIRA_AUTH_MODE", "bearer").lower()
    if mode == "bearer":
        auth = request.headers.get("Authorization", "")
        ok = auth.startswith("Bearer ") and hmac.compare_digest(
            auth[len("Bearer "):], secret
        )
    elif mode == "hmac":
        header = request.headers.get("X-Hub-Signature-256", "")
        if not header.startswith("sha256="):
            ok = False
        else:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(expected, header[len("sha256="):])
    else:
        logger.error("jira_webhook_unknown_auth_mode", auth_mode=mode)
        raise HTTPException(500, "Server misconfiguration")
    logger.info("jira_webhook_auth", auth_mode=mode, ok=ok)
    if not ok:
        raise HTTPException(401, "Authentication required")


@router.post("/jira")
async def jira_webhook(request: Request, background: BackgroundTasks):
    """Handle JIRA webhook events. Fail-closed auth via JIRA_AUTH_MODE."""
    body = await request.body()
    _authenticate(request, body)

    try:
        payload = json.loads(body)
        issue = payload.get("issue") or {}
        jira_key = issue.get("key")
        fields = issue.get("fields", {})
        description = adf_to_text(fields.get("description", ""))
        summary = fields.get("summary", "")

        if not jira_key or not description.strip():
            raise HTTPException(400, "Missing jira_key or description")

        # If generate_test_api already created a pending row for this jira_key
        # (Fortress-originated story with rule_id set), update it in place so
        # we keep the rule_id linkage. Otherwise insert a new row.
        async with get_db() as db:
            existing = (await db.execute(
                select(WorkflowExecution)
                .where(WorkflowExecution.jira_story_id == jira_key)
                .order_by(WorkflowExecution.started_at.desc())
                .limit(1)
            )).scalar_one_or_none()

            # Already generated successfully — return the prior PR without
            # re-running the 6-agent pipeline. Failed runs fall through and
            # are allowed to retry.
            if (
                existing is not None
                and existing.status == WorkflowStatus.COMPLETED
                and (existing.artifacts or {}).get("github_pr_url")
            ):
                logger.info(
                    "jira_webhook_skip_already_generated",
                    workflow_id=str(existing.id),
                    jira_key=jira_key,
                    github_pr_url=existing.artifacts["github_pr_url"],
                )
                return {
                    "status": "duplicate",
                    "workflow_id": str(existing.id),
                    "jira_key": jira_key,
                    "github_pr_url": existing.artifacts["github_pr_url"],
                }

            if existing is not None and existing.status in (
                WorkflowStatus.PENDING, WorkflowStatus.RUNNING,
            ):
                # M9 — in-flight workflow already exists for this key.
                # Reuse it instead of spawning a duplicate. JIRA retries
                # webhook deliveries on transient errors; without this,
                # one story produces N parallel runs.
                existing.context_data = {
                    "jira_story": description,
                    "summary": summary,
                }
                existing.status = WorkflowStatus.RUNNING
                workflow_id = str(existing.id)
                await db.commit()
                logger.info(
                    "jira_webhook_deduplicated",
                    workflow_id=workflow_id,
                    jira_key=jira_key,
                )
            else:
                workflow_id = str(uuid.uuid4())
                db.add(WorkflowExecution(
                    id=workflow_id,
                    jira_story_id=jira_key,
                    jira_story_key=jira_key,
                    status=WorkflowStatus.RUNNING,
                    context_data={
                        "jira_story": description,
                        "summary": summary,
                    },
                ))
                try:
                    await db.commit()
                except IntegrityError:
                    # Concurrent retry won the race — uq_workflow_jira_story_inflight
                    # rejected our insert. Roll back and pick up the in-flight row.
                    await db.rollback()
                    existing = (await db.execute(
                        select(WorkflowExecution)
                        .where(WorkflowExecution.jira_story_id == jira_key)
                        .where(WorkflowExecution.status.in_(
                            [WorkflowStatus.PENDING, WorkflowStatus.RUNNING]
                        ))
                        .limit(1)
                    )).scalar_one()
                    workflow_id = str(existing.id)
                    logger.info(
                        "jira_webhook_dedupe_race_resolved",
                        workflow_id=workflow_id,
                        jira_key=jira_key,
                    )
        
        # Queue background processing
        background.add_task(
            process_workflow,
            workflow_id,
            jira_key,
            description
        )
        
        logger.info(
            "webhook_received",
            workflow_id=workflow_id,
            jira_key=jira_key
        )
        
        return {
            "status": "processing",
            "workflow_id": workflow_id,
            "jira_key": jira_key
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("webhook_failed", error=str(e))
        raise HTTPException(500, f"Webhook processing failed: {str(e)}")

async def process_workflow(workflow_id: str, jira_key: str, story_text: str, rule_id: str = None):
    """Background task to execute workflow.

    Holds a cluster-wide pg_advisory_lock for the duration of the
    6-agent pipeline so only one Bedrock invocation runs at a time
    across all ECS tasks. Postgres orders waiters by arrival.
    """
    # Lock-holding session must stay open for the full workflow.
    async with get_db() as lock_db:
        await lock_db.execute(
            text("SELECT pg_advisory_lock(:k)"), {"k": _WORKFLOW_LOCK_KEY}
        )
        logger.info(
            "workflow_lock_acquired",
            workflow_id=workflow_id,
            jira_key=jira_key,
        )
        try:
            # Re-check status after winning the lock: a parallel webhook
            # retry may have finished this same workflow_id while we waited.
            async with get_db() as db:
                current = await db.get(WorkflowExecution, workflow_id)
                if current and current.status == WorkflowStatus.COMPLETED:
                    logger.info(
                        "workflow_skipped_already_completed",
                        workflow_id=workflow_id,
                        jira_key=jira_key,
                    )
                    return

            logger.info("workflow_processing_started", workflow_id=workflow_id)

            # Resolve the trigger rule from the workflow row when not passed in
            # (the JIRA webhook path doesn't carry it). Threaded to Agent 2 so
            # the clicked rule's own obligations are always in the covered set.
            if rule_id is None:
                async with get_db() as db:
                    wf = await db.get(WorkflowExecution, workflow_id)
                    rule_id = wf.rule_id if wf else None

            orchestrator = WorkflowOrchestrator()
            result = await orchestrator.execute_workflow({
                "workflow_id": workflow_id,
                "key": jira_key,
                "description": story_text,
                "rule_id": rule_id
            })

            # Hoist the PR URL to artifacts.github_pr_url so the dashboard's
            # /test-status endpoint and Workflows tab can find it without
            # traversing nested keys.
            execution = result.get("execution") or {}
            pr_url = execution.get("pr_url") or (execution.get("artifacts") or {}).get("pr_url")
            artifacts_out = dict(result)
            if pr_url:
                artifacts_out["github_pr_url"] = pr_url

            async with get_db() as db:
                workflow = await db.get(WorkflowExecution, workflow_id)
                if workflow:
                    workflow.status = WorkflowStatus.COMPLETED
                    workflow.artifacts = artifacts_out
                    await db.commit()

            logger.info(
                "workflow_processing_completed",
                workflow_id=workflow_id,
                quality_score=result.get("quality_score", 0)
            )

        except Exception as e:
            logger.error(
                "workflow_processing_failed",
                workflow_id=workflow_id,
                error=str(e),
                exc_info=True
            )
            async with get_db() as db:
                workflow = await db.get(WorkflowExecution, workflow_id)
                if workflow:
                    workflow.status = WorkflowStatus.FAILED
                    await db.commit()
        finally:
            await lock_db.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _WORKFLOW_LOCK_KEY}
            )
            logger.info(
                "workflow_lock_released",
                workflow_id=workflow_id,
            )