"""
B5 — SandboxTestRunner env-isolation regression lock.

Generated tests run in a subprocess. Before B5, the runner passed the
full host env, so any secret in `os.environ` (DATABASE_URL,
GITHUB_TOKEN, ANTHROPIC_API_KEY, JIRA_API_TOKEN) was visible to —
and could be exfiltrated by — generated test code. These tests pin:

  1. The allowlist excludes every known secret env key.
  2. PYTHONPATH is dropped so generated code cannot import src.*.
  3. End-to-end: a generated test that introspects os.environ confirms
     the secret is gone.
"""
from __future__ import annotations

import asyncio
import os
import textwrap

import pytest


_SECRET_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "GITHUB_TOKEN",
    "ANTHROPIC_API_KEY",
    "JIRA_API_TOKEN",
    "JWT_SECRET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "PIPELINE2_API_TOKEN",
)


def test_b5_sandbox_env_excludes_secrets(monkeypatch):
    """Pure-function test on _build_sandbox_env — no subprocess needed."""
    from src.services.test_runner import SandboxTestRunner

    for k in _SECRET_KEYS:
        monkeypatch.setenv(k, f"secret-value-of-{k}")

    runner = SandboxTestRunner(output_dir="/tmp/fortress_tests_b5")
    env = runner._build_sandbox_env()

    leaks = [k for k in _SECRET_KEYS if k in env]
    assert not leaks, (
        f"Sandbox env leaks production secrets: {leaks}. "
        "Generated test code would see them via os.environ."
    )


def test_b5_sandbox_env_drops_pythonpath(monkeypatch):
    """PYTHONPATH must be excluded so generated tests cannot import src.*"""
    from src.services.test_runner import SandboxTestRunner

    monkeypatch.setenv("PYTHONPATH", "/home/ubuntu/fortress_server/fortress_server")
    env = SandboxTestRunner(output_dir="/tmp/fortress_tests_b5")._build_sandbox_env()
    assert "PYTHONPATH" not in env, (
        "PYTHONPATH survived sandboxing — generated test code can now "
        "`import src.api.sync_api` and pull in production modules. "
        "See B5."
    )


def test_b5_sandbox_env_forces_home_and_tmp_to_output_dir(monkeypatch):
    """HOME / TMPDIR must point at the runner's output dir so pytest cache
    cannot accidentally read or write into the user's real home (which on
    dev boxes contains ~/.aws/credentials, ~/.ssh, etc.)."""
    from src.services.test_runner import SandboxTestRunner

    monkeypatch.setenv("HOME", "/home/jamie")
    monkeypatch.setenv("TMPDIR", "/tmp/should-be-overridden")
    runner = SandboxTestRunner(output_dir="/tmp/fortress_tests_b5")
    env = runner._build_sandbox_env()
    assert env["HOME"] == "/tmp/fortress_tests_b5"
    assert env["TMPDIR"] == "/tmp/fortress_tests_b5"


@pytest.mark.asyncio
async def test_b5_generated_code_cannot_see_secret(monkeypatch):
    """End-to-end: inject a fake secret, run a generated test that asserts
    the env key is unset, and verify the subprocess sees an empty value."""
    from src.services.test_runner import SandboxTestRunner

    monkeypatch.setenv("FAKE_BANK_SECRET", "supersecret-9d1f-43e2")

    # Generated test introspects os.environ. It passes only if the secret
    # is NOT visible in the subprocess.
    generated = textwrap.dedent("""\
        import os
        def test_secret_not_visible():
            assert os.environ.get("FAKE_BANK_SECRET") is None, (
                f"FAKE_BANK_SECRET leaked into sandbox: "
                f"{os.environ.get('FAKE_BANK_SECRET')!r}"
            )
    """)

    runner = SandboxTestRunner(timeout=15, output_dir="/tmp/fortress_b5_e2e")
    result = await runner.run(generated, "B5-ENV-LEAK")
    assert result["passed"], (
        f"Sandbox leaked FAKE_BANK_SECRET — B5 regression. Output:\n"
        f"{result.get('output', '')}"
    )
