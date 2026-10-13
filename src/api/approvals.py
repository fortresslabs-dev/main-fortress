"""src/api/approvals.py — Workflow checkpoint approvals."""
from __future__ import annotations
from typing import Optional
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from src.api.auth import CurrentUser, get_current_user, require_approver
from src.services.database import get_db

router = APIRouter()
logger = structlog.get_logger()


class ApprovalRequest(BaseModel):
    workflow_id: str
    checkpoint: str          # pre_generation | pre_execution
    decision: str            # approved | rejected
    note: Optional[str] = None


@router.post("/")
async def create_approval(
    request: ApprovalRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_approver),
) -> dict:
    async with get_db() as session:
        wf = (await session.execute(text("""
            SELECT id, jira_story_key, status FROM workflow_executions
            WHERE id=:wid
        """), {"wid": request.workflow_id})).fetchone()

        if not wf:
            raise HTTPException(404, "Workflow not found")
        if wf.status not in ("running", "waiting_approval"):
            raise HTTPException(400, f"Workflow is {wf.status}")

        import json as _json
        # Schema (migration 016) has no approved_by_role / notes columns —
        # role + note live in the feedback JSON column.
        await session.execute(text("""
            INSERT INTO checkpoint_approvals
              (id, workflow_id, checkpoint_name, status,
               approved_by, approved_at, feedback)
            VALUES
              (gen_random_uuid(),:wid,:cp,:dec,:email,NOW(),CAST(:fb AS json))
        """), {"wid": request.workflow_id, "cp": request.checkpoint,
               "dec": request.decision, "email": user.email,
               "fb": _json.dumps({"role": user.role, "note": request.note or ""})})

        new_status = "running" if request.decision == "approved" else "failed"
        # workflow_executions has no updated_at column (see migration 016);
        # completed_at is the canonical terminal-state stamp.
        if new_status == "failed":
            await session.execute(text("""
                UPDATE workflow_executions SET status=:s, completed_at=NOW()
                WHERE id=:wid
            """), {"s": new_status, "wid": request.workflow_id})
        else:
            await session.execute(text("""
                UPDATE workflow_executions SET status=:s WHERE id=:wid
            """), {"s": new_status, "wid": request.workflow_id})
        await session.commit()

    # Post back to JIRA via a sync background task. python-jira's
    # JIRA(server=…) constructor blocks on GET /rest/api/2/serverInfo with
    # urllib3's default retry chain (~19s + 32s + 49s of time.sleep), so
    # calling it inline freezes the asyncio worker for the entire retry
    # window — every other endpoint queues behind it. FastAPI runs sync
    # background callables in a threadpool, so the sleep stays off-loop.
    if wf.jira_story_key:
        background.add_task(
            _post_jira_approval_comment,
            jira_key=wf.jira_story_key,
            decision=request.decision,
            checkpoint=request.checkpoint,
            email=user.email,
            role=user.role,
            note=request.note or "",
        )

    logger.info("checkpoint_approval", workflow_id=request.workflow_id,
                decision=request.decision, by=user.email)
    return {"status": "recorded", "workflow_id": request.workflow_id,
            "decision": request.decision, "by": user.email}


def _post_jira_approval_comment(
    *, jira_key: str, decision: str, checkpoint: str,
    email: str, role: str, note: str,
) -> None:
    """Sync helper — dispatched via BackgroundTasks so python-jira's blocking
    serverInfo retry chain runs on the threadpool, not the event loop."""
    try:
        from src.integrations.jira_client import JIRAClient
        verb = "approved" if decision == "approved" else "rejected"
        body = (
            f"*Checkpoint {verb}* by {email} ({role})\n"
            f"Checkpoint: {checkpoint}"
        )
        if note:
            body += f"\nNote: {note}"
        JIRAClient().add_comment(jira_key, body)
    except Exception as exc:
        logger.warning("jira_comment_failed", jira_key=jira_key, error=str(exc))


@router.get("/{workflow_id}")
async def get_pending_approvals(
    workflow_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT checkpoint_name, status, approved_by,
                   approved_at, feedback
            FROM checkpoint_approvals WHERE workflow_id=:wid
            ORDER BY approved_at NULLS LAST
        """), {"wid": workflow_id})
    out = []
    for r in rows.fetchall():
        m = dict(r._mapping)
        fb = m.get("feedback") or {}
        # Flatten role + note out of feedback for dashboard consumers.
        m["approved_by_role"] = fb.get("role") if isinstance(fb, dict) else None
        m["note"] = fb.get("note") if isinstance(fb, dict) else None
        out.append(m)
    return {"approvals": out}
