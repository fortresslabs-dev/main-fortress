"""GitHub service — creates a branch, commits the generated test file, opens a PR."""
import base64
import re
from typing import Any, Dict, Optional

import httpx
import structlog

from src.services.authority_gate import citation_for_pr_body, may_assert_as_law
from src.utils.logger import redact_text

logger = structlog.get_logger()

_GH_API = "https://api.github.com"
_ACCEPT = "application/vnd.github.v3+json"


class GitHubService:
    def __init__(self, token: str, org: str, repo: str):
        self.token = token
        self.org = org
        self.repo = repo
        self._base = f"{_GH_API}/repos/{org}/{repo}"
        self._headers = {
            "Authorization": f"token {token}",
            "Accept": _ACCEPT,
            "Content-Type": "application/json",
        }

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.org and self.repo)

    async def create_pr(
        self,
        jira_key: str,
        test_code: str,
        quality_score: int,
        validation_result: Dict,
        test_results: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Create branch → commit test file → open PR.
        Returns the PR HTML URL, or None if GitHub integration is disabled or fails.
        """
        if not self.enabled:
            logger.info("github_pr_skipped", reason="GITHUB_TOKEN/ORG/REPO not configured")
            return None

        safe_key = jira_key.lower().replace("-", "_")
        branch = f"fortress/{jira_key.lower()}-compliance-tests"
        file_path = f"tests/compliance/test_{safe_key}.py"

        try:
            main_sha = await self._get_default_sha()
            if not main_sha:
                logger.warning("github_pr_skipped", reason="could not determine default branch SHA")
                return None

            await self._create_branch(branch, main_sha)
            commit_msg = f"feat({jira_key}): compliance tests — score {quality_score}/100"
            await self._upsert_file(branch, file_path, test_code, commit_msg)

            body = self._build_pr_body(jira_key, quality_score, validation_result, test_results)
            title = f"[Fortress] {jira_key} — Compliance Tests ({quality_score}/100)"
            pr_url = await self._open_pr(branch, title, body)

            if pr_url:
                logger.info("github_pr_created", jira_key=jira_key, pr_url=pr_url)
            return pr_url

        except Exception as exc:
            # redact_text strips token / Authorization fragments — h13.
            # An httpx exception that captured the request can leak the
            # PAT in the repr otherwise.
            logger.error("github_pr_failed", jira_key=jira_key, error=redact_text(str(exc)))
            return None

    # ── private helpers ────────────────────────────────────────────────────────

    async def _get_default_sha(self) -> Optional[str]:
        async with httpx.AsyncClient(timeout=15) as client:
            for branch in ("main", "master"):
                r = await client.get(
                    f"{self._base}/git/ref/heads/{branch}",
                    headers=self._headers,
                )
                if r.status_code == 200:
                    return r.json()["object"]["sha"]
        return None

    async def _create_branch(self, branch: str, sha: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self._base}/git/refs",
                headers=self._headers,
                json={"ref": f"refs/heads/{branch}", "sha": sha},
            )
            # 422 = branch already exists — fine to continue
            if r.status_code not in (201, 422):
                # GitHub error bodies don't echo the token, but redact
                # defensively in case proxy middleware does.
                raise RuntimeError(
                    f"create_branch HTTP {r.status_code}: {redact_text(r.text[:200])}"
                )

    async def _upsert_file(self, branch: str, path: str, content: str, message: str) -> None:
        encoded = base64.b64encode(content.encode()).decode()
        payload: Dict[str, Any] = {"message": message, "content": encoded, "branch": branch}

        # If file already exists, include its SHA so GitHub treats this as an update
        async with httpx.AsyncClient(timeout=15) as client:
            check = await client.get(
                f"{self._base}/contents/{path}",
                headers=self._headers,
                params={"ref": branch},
            )
            if check.status_code == 200:
                payload["sha"] = check.json().get("sha", "")

            r = await client.put(
                f"{self._base}/contents/{path}",
                headers=self._headers,
                json=payload,
            )
            if r.status_code not in (200, 201):
                raise RuntimeError(
                    f"upsert_file HTTP {r.status_code}: {redact_text(r.text[:200])}"
                )

    async def _open_pr(self, branch: str, title: str, body: str) -> Optional[str]:
        async with httpx.AsyncClient(timeout=15) as client:
            for base in ("main", "master"):
                r = await client.post(
                    f"{self._base}/pulls",
                    headers=self._headers,
                    json={"title": title, "body": body, "head": branch, "base": base},
                )
                if r.status_code == 201:
                    return r.json()["html_url"]
                if r.status_code == 422:
                    # PR may already exist; try to find it
                    existing = await client.get(
                        f"{self._base}/pulls",
                        headers=self._headers,
                        params={"head": f"{self.org}:{branch}", "state": "open"},
                    )
                    if existing.status_code == 200 and existing.json():
                        return existing.json()[0]["html_url"]
        return None

    @staticmethod
    def _build_pr_body(
        jira_key: str,
        quality_score: int,
        validation_result: Dict,
        test_results: Optional[Dict],
    ) -> str:
        coverage = validation_result.get("coverage", {})
        issues = validation_result.get("compliance_issues", [])[:8]
        rule_hits_list = validation_result.get("rule_hits") or []
        # §11.3 hard gate — split rule_hits into LAW (blocking) and
        # non-LAW (advisory) rails so the reviewer can tell which of
        # the cited authorities is codified law vs guidance / internal
        # control. Prior corpus rows fabricated CFR cites on the
        # advisory rail (see docs/correction_smb.md C-1); the gate
        # here is what makes that fabrication class structural.
        blocking = [r for r in rule_hits_list if isinstance(r, dict) and may_assert_as_law(r)]
        advisory = [r for r in rule_hits_list if isinstance(r, dict) and not may_assert_as_law(r)]

        lines = [
            "## Fortress Auto-Generated Compliance Tests",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| JIRA Story | {jira_key} |",
            f"| Quality Score | {quality_score}/100 |",
            f"| Compliance Score | {validation_result.get('compliance_score', 0)}/100 |",
            f"| Rules Checked | {len(rule_hits_list)} |",
            f"| Codified law (blocking) | {len(blocking)} |",
            f"| Guidance / internal (advisory) | {len(advisory)} |",
            f"| Tests Generated | {coverage.get('tests_generated', 0)} |",
            f"| Scenarios | {coverage.get('scenarios_total', 0)} |",
            "",
        ]

        if blocking:
            lines += [
                "### Codified law — required citations",
                "",
                "| Rule | Authority |",
                "|------|-----------|",
            ]
            for r in blocking:
                rid = r.get("rule_id") or "—"
                lines.append(f"| {rid} | {citation_for_pr_body(r)} |")
            lines.append("")

        if advisory:
            lines += [
                "### Guidance / internal controls (advisory)",
                "",
                "_Non-blocking. Cited from the manual/bulletin — NOT a CFR section._",
                "",
                "| Rule | Source |",
                "|------|--------|",
            ]
            for r in advisory:
                rid = r.get("rule_id") or "—"
                lines.append(f"| {rid} | {citation_for_pr_body(r)} |")
            lines.append("")

        if test_results and test_results.get("ran"):
            stats = test_results.get("stats", {})
            status = "PASS" if test_results["passed"] else "FAIL"
            lines += [
                f"### Sandbox Test Run: {status}",
                f"```",
                f"Passed: {stats.get('passed', 0)}  Failed: {stats.get('failed', 0)}  Errors: {stats.get('errors', 0)}",
                f"```",
                "",
            ]

        if issues:
            lines += ["### Compliance Issues Found", ""]
            for issue in issues:
                lines.append(f"- {issue}")
            lines.append("")

        lines += [
            "### Review Checklist",
            f"- [ ] Verify scenarios match JIRA acceptance criteria",
            f"- [ ] Confirm thresholds match current regulatory requirements",
            f"- [ ] Run locally: `pytest tests/compliance/test_{re.sub(r'[^a-z0-9_]', '_', jira_key.lower())}.py -v`",
            f"- [ ] Merge once QA sign-off obtained",
            "",
            "---",
            f"*Generated by Fortress Pipeline 2 — automated compliance testing*",
        ]
        return "\n".join(lines)
