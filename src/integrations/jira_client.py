"""JIRA Integration"""
import os
import requests
from requests.auth import HTTPBasicAuth
from jira import JIRA
from src.utils.config import settings

_DEFAULT_ISSUE_TYPE = os.getenv("JIRA_ISSUE_TYPE", "Task")


class JIRAClient:
    def __init__(self):
        self.client = JIRA(
            server=settings.JIRA_URL,
            basic_auth=(settings.JIRA_USERNAME, settings.JIRA_API_TOKEN)
        )
        self._server = settings.JIRA_URL.rstrip("/")
        self._auth = HTTPBasicAuth(settings.JIRA_USERNAME, settings.JIRA_API_TOKEN)

    def get_issue(self, issue_key: str):
        return self.client.issue(issue_key)

    def add_comment(self, issue_key: str, comment: str):
        return self.client.add_comment(issue_key, comment)

    def _valid_issue_types(self, project: str) -> list:
        try:
            r = requests.get(
                f"{self._server}/rest/api/3/issue/createmeta",
                params={"projectKeys": project, "expand": "projects.issuetypes"},
                auth=self._auth, timeout=10,
            )
            if r.status_code != 200:
                return []
            projects = r.json().get("projects", [])
            if not projects:
                return []
            return [t["name"] for t in projects[0].get("issuetypes", [])]
        except Exception:
            return []

    def create_story(
        self, project: str, title: str, description: str,
        priority: str = "High", labels: list = None,
        issue_type: str = None,
    ) -> dict:
        """Create a JIRA issue. Returns dict with 'key' and 'id'.

        Uses plain requests (not self.client._session) because python-jira's
        ResilientSession monkey-patches .post() to raise JiraError on 4xx
        before we can inspect the response body for an issuetype mismatch.
        """
        itype = issue_type or _DEFAULT_ISSUE_TYPE
        body = {
            "fields": {
                "project": {"key": project},
                "issuetype": {"name": itype},
                "summary": title,
                "description": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": description}
                    ]}]
                },
                "priority": {"name": priority},
                "labels": labels or ["compliance", "fortress-generated"],
            }
        }
        resp = requests.post(
            f"{self._server}/rest/api/3/issue",
            json=body, auth=self._auth, timeout=15,
        )
        if resp.status_code == 400 and "issuetype" in resp.text:
            valid = self._valid_issue_types(project)
            raise RuntimeError(
                f"JIRA rejected issuetype='{itype}' for project {project}. "
                f"Valid types: {valid or 'unknown (createmeta call failed)'}. "
                f"Set JIRA_ISSUE_TYPE env var to one of these and redeploy."
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"JIRA {resp.status_code} for project {project} "
                f"issuetype={itype}: {resp.text[:500]}"
            )
        result = resp.json()
        import structlog
        structlog.get_logger().info(
            "jira_story_created", key=result.get("key"), issue_type=itype
        )
        return result
