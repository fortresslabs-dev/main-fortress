"""
M10 — execution agent must surface a failed JIRA comment in artifacts
and degrade the workflow status. Pre-fix the warning was silent and
the workflow finished as SUCCESS even when the comment had dropped.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.execution import ExecutionAgent


@pytest.fixture
def agent():
    return ExecutionAgent()


async def test_m10_jira_comment_failure_marks_workflow_degraded(agent):
    """If _post_jira_comment fails after the PR opens, status='degraded'."""
    with patch.object(
        agent, "_open_github_pr", return_value="https://github.com/x/y/pull/1"
    ), patch.object(
        agent, "_post_jira_comment",
        return_value=(False, "RuntimeError: 503 from JIRA"),
    ):
        result = await agent.execute(
            context={},
            input_data={
                "workflow_id": "wf-1", "jira_key": "DEMO-1",
                "validation": {"quality_score": 0.9, "is_valid": True},
                "test_code": "def test(): pass",
            },
        )

    assert result["status"] == "degraded", (
        f"Expected status=degraded when JIRA comment failed; got {result['status']!r}."
    )
    arts = result["artifacts"]
    assert arts["jira_comment_attempted"] is True
    assert arts["jira_comment_posted"] is False
    assert arts["jira_comment_error"] is not None
    assert "JIRA" in arts["jira_comment_error"] or "RuntimeError" in arts["jira_comment_error"]


async def test_m10_jira_comment_success_keeps_completed(agent):
    """Happy path: comment posted → status=completed; error field is None."""
    with patch.object(
        agent, "_open_github_pr", return_value="https://github.com/x/y/pull/2"
    ), patch.object(
        agent, "_post_jira_comment", return_value=(True, None)
    ):
        result = await agent.execute(
            context={},
            input_data={
                "workflow_id": "wf-2", "jira_key": "DEMO-2",
                "validation": {"quality_score": 0.95, "is_valid": True},
                "test_code": "def test(): pass",
            },
        )

    assert result["status"] == "completed"
    arts = result["artifacts"]
    assert arts["jira_comment_posted"] is True
    assert arts["jira_comment_error"] is None


async def test_m10_no_pr_opened_does_not_attempt_comment(agent):
    """When no PR was opened, no JIRA comment is attempted — artifacts reflect that."""
    with patch.object(agent, "_open_github_pr", return_value=None), \
         patch.object(agent, "_post_jira_comment") as fake_comment:
        result = await agent.execute(
            context={},
            input_data={
                "workflow_id": "wf-3", "jira_key": "DEMO-3",
                "validation": {"quality_score": 0.2, "is_valid": False},
                "test_code": "",
            },
        )

    fake_comment.assert_not_called()
    arts = result["artifacts"]
    assert arts["jira_comment_attempted"] is False
    assert arts["jira_comment_posted"] is False
    # completed when no attempt is made (validation failure is signalled
    # separately via is_approved / compliance_score).
    assert result["status"] == "completed"
