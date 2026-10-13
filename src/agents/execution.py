"""Execution Agent — opens the GitHub PR and posts the PR URL as a comment on the JIRA story.
Optionally also fires a webhook callback if jira_callback_url is configured.
"""
from typing import Dict, Any, List
import structlog
from src.agents.base_agent import BaseAgent
from src.services.github_service import GitHubService
from src.utils.config import settings

logger = structlog.get_logger()

class ExecutionAgent(BaseAgent):
    agent_type = "execution"

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """Execute final steps, store results, and optionally post to JIRA"""
        try:
            workflow_id = input_data.get("workflow_id")
            jira_key = input_data.get("jira_key", "")
            validation = input_data.get("validation", {})
            test_code = input_data.get("test_code", "")
            test_results = input_data.get("test_results")
            understanding = input_data.get("understanding", {}) or {}
            scenarios = input_data.get("scenarios", []) or []

            self.logger.info("execution_started", workflow_id=workflow_id)

            quality_score = validation.get("quality_score", 0)
            is_approved = validation.get("is_valid", False)

            pr_url = await self._open_github_pr(
                jira_key, test_code, quality_score, validation, test_results, is_approved
            )

            # Post the PR URL as a comment on the JIRA story (best-effort).
            jira_comment_posted = False
            jira_comment_error: str | None = None
            if pr_url and jira_key:
                jira_comment_posted, jira_comment_error = self._post_jira_comment(
                    jira_key, pr_url, validation
                )

            # REQ-010 writeback: flip obligations.has_test_coverage on the
            # obligations resolved in UnderstandingAgent. Gated on PR-open
            # success AND validation approval so we don't credit failed runs.
            obligations_covered: List[str] = []
            obligation_writeback_error: str | None = None
            obligation_ids = understanding.get("obligation_ids") or []
            if pr_url and is_approved and obligation_ids:
                obligations_covered, obligation_writeback_error = (
                    await self._mark_obligations_covered(
                        obligation_ids, workflow_id, jira_key, scenarios
                    )
                )

            # Build final result package. M10 — surface comment failure
            # in artifacts AND status so the dashboard / Workflows tab
            # shows "degraded" instead of green when JIRA dropped the
            # comment. PR is still open, so this is not a hard fail.
            comment_attempted = bool(pr_url and jira_key)
            comment_failed = comment_attempted and not jira_comment_posted
            status = "degraded" if comment_failed else "completed"

            result = {
                "workflow_id": workflow_id,
                "jira_key": jira_key,
                "status": status,
                "quality_score": quality_score,
                "is_approved": is_approved,
                "compliance_score": validation.get("compliance_score", 0),
                "pr_url": pr_url,
                "artifacts": {
                    "test_code_generated": bool(test_code),
                    "scenarios_generated": True,
                    "compliance_validated": True,
                    "audit_trail_created": True,
                    "pr_opened": bool(pr_url),
                    "pr_url": pr_url,
                    "jira_comment_attempted": comment_attempted,
                    "jira_comment_posted": jira_comment_posted,
                    "jira_comment_error": jira_comment_error,
                    "obligations_covered": obligations_covered,
                    "obligations_covered_count": len(obligations_covered),
                    "obligation_writeback_error": obligation_writeback_error,
                },
                "next_steps": self._determine_next_steps(validation, pr_url),
            }

            # Post results to JIRA if webhook configured
            jira_callback_url = context.get("jira_callback_url") or input_data.get("jira_callback_url")
            if jira_callback_url and jira_key:
                await self._post_to_jira(jira_callback_url, jira_key, result)
            
            self.logger.info(
                "execution_completed",
                workflow_id=workflow_id,
                status=result["status"],
                quality_score=result["quality_score"],
            )
            
            return result
            
        except Exception as e:
            self.logger.error("execution_failed", workflow_id=workflow_id, error=str(e))
            raise
    
    async def _mark_obligations_covered(
        self,
        obligation_ids: List[str],
        workflow_id: str | None,
        jira_key: str,
        scenarios: List[Dict[str, Any]],
    ) -> tuple[List[str], str | None]:
        """Flip obligations.has_test_coverage = TRUE for the resolved set.

        Best-effort — failure must not break the run because the PR is
        already open and JIRA has already been notified. Returns the list
        of obligation_ids actually updated and an error string (or None).
        """
        try:
            import json
            from sqlalchemy import text
            from src.services.database import get_db
            scenario_ids = [
                f"{jira_key or workflow_id or 'wf'}::{s.get('name', f'scn_{i+1}')}"
                for i, s in enumerate(scenarios)
            ]
            async with get_db() as session:
                rows = (await session.execute(text("""
                    UPDATE obligations
                       SET has_test_coverage  = TRUE,
                           coverage_updated_at = NOW(),
                           test_scenario_ids   = COALESCE(test_scenario_ids, '[]'::jsonb)
                                                 || CAST(:new_ids AS jsonb)
                     WHERE obligation_id = ANY(:ids)
                       AND is_active = TRUE
                 RETURNING obligation_id
                """), {
                    "ids":     obligation_ids,
                    "new_ids": json.dumps(scenario_ids),
                })).all()
                await session.commit()
            updated = [r[0] for r in rows]
            self.logger.info(
                "obligations_marked_covered",
                workflow_id=workflow_id,
                jira_key=jira_key,
                requested=len(obligation_ids),
                updated=len(updated),
            )
            return updated, None
        except Exception as exc:
            self.logger.warning(
                "obligation_writeback_failed",
                workflow_id=workflow_id,
                jira_key=jira_key,
                error=str(exc),
            )
            return [], f"{type(exc).__name__}: {exc}"

    def _post_jira_comment(
        self, jira_key: str, pr_url: str, validation: Dict
    ) -> tuple[bool, str | None]:
        """Post the PR URL + CFR citation as a JIRA comment.

        Returns (posted_ok, error_or_None). Failure is non-fatal — the PR
        is already open — but M10 requires the caller to surface the
        outcome in workflow artifacts so the dashboard shows degraded
        instead of silently green.
        """
        try:
            from src.integrations.jira_client import JIRAClient
            from src.services.authority_gate import citation_for_pr_body, may_assert_as_law
            rule_hits = validation.get("rule_hits") or []
            # §11.3 — pick the first LAW-classified rule for the "CFR:"
            # line. Falling back to whichever rule happened to be first
            # in the list (the old behaviour) let a fabricated
            # cfr_citation on an INTERNAL_CONTROL rule surface as if it
            # were codified law. If no LAW rule is present we emit the
            # advisory citation string via citation_for_pr_body(), which
            # renders "Control expectation per <authority_source>".
            first_law = next(
                (r for r in rule_hits if isinstance(r, dict) and may_assert_as_law(r)),
                None,
            )
            first_rule = first_law or (rule_hits[0] if rule_hits and isinstance(rule_hits[0], dict) else None)
            citation = citation_for_pr_body(first_rule) if first_rule else "n/a"
            body = (
                f"Compliance test PR opened: {pr_url}\n"
                f"Citation: {citation}\n"
                f"Generated by Fortress Labs"
            )
            JIRAClient().add_comment(jira_key, body)
            self.logger.info("jira_comment_posted", jira_key=jira_key, pr_url=pr_url)
            return True, None
        except Exception as exc:
            self.logger.warning(
                "jira_comment_failed", jira_key=jira_key, error=str(exc)
            )
            return False, f"{type(exc).__name__}: {exc}"

    async def _post_to_jira(self, callback_url: str, jira_key: str, result: Dict) -> None:
        """Post execution results back to JIRA via webhook callback."""
        try:
            import httpx
            
            comment_body = self._build_jira_comment(result)
            payload = {
                "issue_key": jira_key,
                "comment": comment_body,
                "status": "Done" if result["is_approved"] else "In Review",
                "quality_score": result["quality_score"],
                "compliance_score": result.get("compliance_score", 0),
            }
            
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(callback_url, json=payload)
                resp.raise_for_status()
            
            self.logger.info("jira_callback_posted", jira_key=jira_key, status=resp.status_code)
        except Exception as e:
            # JIRA callback failure is non-fatal
            self.logger.warning("jira_callback_failed", jira_key=jira_key, error=str(e))
    
    def _build_jira_comment(self, result: Dict) -> str:
        """Build a human-readable JIRA comment from execution result."""
        status = "APPROVED" if result["is_approved"] else "NEEDS REVISION"
        lines = [
            f"*Test Generation {status}*",
            f"Quality Score: {result['quality_score']}/100",
            f"Compliance Score: {result.get('compliance_score', 'N/A')}/100",
            "",
            "*Next Steps:*",
        ]
        for step in result.get("next_steps", []):
            lines.append(f"- {step}")
        return "\n".join(lines)
    
    async def _open_github_pr(
        self,
        jira_key: str,
        test_code: str,
        quality_score: int,
        validation: Dict,
        test_results: Any,
        is_approved: bool,
    ) -> str | None:
        """Open a PR with the generated test code. Best-effort — failure is non-fatal."""
        if not jira_key or not test_code:
            return None
        if not is_approved:
            self.logger.info("github_pr_skipped", reason="validation not approved", jira_key=jira_key)
            return None

        gh = GitHubService(
            token=settings.GITHUB_TOKEN,
            org=settings.GITHUB_ORG,
            repo=settings.GITHUB_REPO,
        )
        if not gh.enabled:
            self.logger.info("github_pr_skipped", reason="GITHUB_TOKEN/ORG/REPO unset")
            return None

        return await gh.create_pr(
            jira_key=jira_key,
            test_code=test_code,
            quality_score=quality_score,
            validation_result=validation,
            test_results=test_results,
        )

    def _determine_next_steps(self, validation: Dict, pr_url: str | None = None) -> List[str]:
        """Determine what should happen next"""
        next_steps = []

        if validation.get("quality_score", 0) >= 80:
            if pr_url:
                next_steps.append(f"Review PR: {pr_url}")
            next_steps.append("Run tests in CI/CD")
            next_steps.append("Update JIRA story to Done after merge")
        else:
            next_steps.append("Needs revision — quality score below threshold")
            if validation.get("compliance_issues"):
                next_steps.append("Review compliance issues: " + ", ".join(validation["compliance_issues"][:3]))
            next_steps.append("Regenerate tests with additional context")

        return next_steps