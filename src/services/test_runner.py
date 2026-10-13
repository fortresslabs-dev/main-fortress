"""Sandbox test runner — writes generated pytest code to a temp file and executes it."""
import asyncio
import os
import re
import subprocess
import textwrap
from typing import Any, Dict

import structlog

logger = structlog.get_logger()


class SandboxTestRunner:
    """
    Runs generated pytest code in an isolated subprocess.

    The generated tests only import stdlib + pytest + unittest.mock, so they
    are self-contained and safe to run without network access or DB.
    """

    # B5: Generated test code runs in a subprocess. Without this allowlist
    # the child process inherits the FULL host env — DATABASE_URL,
    # GITHUB_TOKEN, ANTHROPIC_API_KEY, JIRA_API_TOKEN, etc — and a
    # malicious-or-buggy generated test can exfiltrate them by writing
    # to disk or stdout. The allowlist keeps only what pytest itself
    # actually needs: a PATH to find python3+pytest, HOME/TMPDIR to put
    # its cache, locale, and the bytecode-suppressor.
    #
    # PYTHONPATH is INTENTIONALLY excluded so the generated code cannot
    # `import src.api.sync_api` and pull in production modules.
    _SANDBOX_ALLOWED_ENV = (
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        # PYTHONUSERBASE is a path, not a secret. In prod containers it
        # is unset (pytest is system-installed). On dev boxes it points
        # to ~/.local so a sandbox child can find a user-installed pytest.
        "PYTHONUSERBASE",
    )

    def __init__(self, timeout: int = 120, output_dir: str = "/tmp/fortress_tests"):
        self.timeout = timeout
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def _build_sandbox_env(self) -> Dict[str, str]:
        """Return a minimal env for the pytest subprocess.

        Only allowlisted keys from the host env are propagated; HOME
        and TMPDIR are forced to the runner's output dir so pytest's
        cache + temp files cannot accidentally read or write into the
        user's home (which on dev boxes may contain ~/.aws/credentials,
        ~/.ssh, etc).
        """
        base: Dict[str, str] = {
            "HOME": self.output_dir,
            "TMPDIR": self.output_dir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        for key in self._SANDBOX_ALLOWED_ENV:
            if key in os.environ:
                base.setdefault(key, os.environ[key])
        return base

    async def run(self, test_code: str, jira_key: str) -> Dict[str, Any]:
        """Write test_code to disk and run pytest. Returns structured result."""
        safe_key = re.sub(r"[^a-z0-9_]", "_", jira_key.lower())
        test_file = os.path.join(self.output_dir, f"test_{safe_key}.py")

        prepared = self._prepare_code(test_code)
        try:
            with open(test_file, "w", encoding="utf-8") as fh:
                fh.write(prepared)
        except OSError as exc:
            return self._error_result(test_file, f"Could not write test file: {exc}")

        try:
            proc_result = await asyncio.to_thread(
                subprocess.run,
                [
                    "python3", "-m", "pytest",
                    test_file,
                    "-v", "--tb=short", "--no-header", "-q",
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout + 10,
                env=self._build_sandbox_env(),
            )
        except subprocess.TimeoutExpired:
            return {
                "ran": True, "passed": False, "return_code": -1,
                "output": f"Test execution timed out after {self.timeout}s",
                "test_file": test_file,
                "stats": {"total": 0, "passed": 0, "failed": 0, "errors": 1},
            }
        except Exception as exc:
            return self._error_result(test_file, f"Subprocess error: {exc}")

        output = (proc_result.stdout + proc_result.stderr)[:6000]
        stats = self._parse_output(output)
        passed = proc_result.returncode == 0

        logger.info(
            "sandbox_tests_complete",
            jira_key=jira_key,
            passed=passed,
            stats=stats,
        )
        return {
            "ran": True,
            "passed": passed,
            "return_code": proc_result.returncode,
            "output": output,
            "test_file": test_file,
            "stats": stats,
        }

    # ── helpers ───────────────────────────────────────────────────────────────

    def _prepare_code(self, code: str) -> str:
        """Prepend guard imports if the generated code is missing them."""
        guard = textwrap.dedent("""\
            # -- Fortress sandbox preamble --
            from unittest.mock import MagicMock, patch, AsyncMock  # noqa: F401
        """)
        if "from unittest.mock" not in code and "MagicMock" not in code:
            code = guard + "\n" + code
        return code

    @staticmethod
    def _parse_output(output: str) -> Dict[str, int]:
        passed = int((re.search(r"(\d+) passed", output) or type("", (), {"group": lambda *_: "0"})()).group(1))
        failed = int((re.search(r"(\d+) failed", output) or type("", (), {"group": lambda *_: "0"})()).group(1))
        errors = int((re.search(r"(\d+) error", output) or type("", (), {"group": lambda *_: "0"})()).group(1))
        return {"total": passed + failed + errors, "passed": passed, "failed": failed, "errors": errors}

    @staticmethod
    def _error_result(test_file: str, msg: str) -> Dict[str, Any]:
        return {
            "ran": False, "passed": False, "return_code": -1,
            "output": msg, "test_file": test_file,
            "stats": {"total": 0, "passed": 0, "failed": 0, "errors": 1},
        }
