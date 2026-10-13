"""
B5 — SandboxTestRunner must run generated pytest with a minimal env
allowlist, NEVER inheriting the host's secrets.

Pre-B5, the subprocess inherited `os.environ` wholesale. A malicious
or buggy generated test could exfiltrate DATABASE_URL, GITHUB_TOKEN,
ANTHROPIC_API_KEY, JIRA_API_TOKEN, AWS_*  by writing them to stdout,
disk, or a network host.

This test pins:
  - HOME / TMPDIR forced to the runner's output_dir (not user $HOME).
  - PYTHONDONTWRITEBYTECODE=1.
  - PYTHONPATH NOT propagated (so generated code cannot import src.*).
  - The known secret env names (DATABASE_URL, GITHUB_TOKEN, etc.) NOT
    propagated even if they exist in the host env at run time.
"""
from __future__ import annotations

import os

import pytest

from src.services.test_runner import SandboxTestRunner


_SECRET_KEYS = (
    "DATABASE_URL",
    "GITHUB_TOKEN",
    "ANTHROPIC_API_KEY",
    "JIRA_API_TOKEN",
    "JIRA_WEBHOOK_SECRET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "PIPELINE2_API_TOKEN",
    "REDIS_URL",
    # PYTHONPATH would let the child `import src.api.sync_api`
    "PYTHONPATH",
)


def test_b5_sandbox_env_excludes_known_secrets(monkeypatch, tmp_path):
    """Set fake values for every known secret in the parent env, then
    assert none of them survive into the sandbox env."""
    for k in _SECRET_KEYS:
        monkeypatch.setenv(k, f"sentinel-{k.lower()}")

    runner = SandboxTestRunner(output_dir=str(tmp_path))
    env = runner._build_sandbox_env()

    leaked = [k for k in _SECRET_KEYS if k in env]
    assert not leaked, (
        f"Sandbox env leaks {leaked}. The allowlist in "
        f"SandboxTestRunner._SANDBOX_ALLOWED_ENV must reject these keys."
    )


def test_b5_sandbox_home_and_tmpdir_isolated(tmp_path):
    """HOME/TMPDIR must point at the runner's output_dir so pytest's
    cache + tempfiles cannot read or write into the user's real $HOME
    (which on a dev box may contain ~/.aws/credentials, ~/.ssh, etc)."""
    runner = SandboxTestRunner(output_dir=str(tmp_path))
    env = runner._build_sandbox_env()

    assert env["HOME"] == str(tmp_path), (
        f"Sandbox HOME must be the output_dir, got {env['HOME']!r}."
    )
    assert env["TMPDIR"] == str(tmp_path), (
        f"Sandbox TMPDIR must be the output_dir, got {env['TMPDIR']!r}."
    )
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1", (
        "PYTHONDONTWRITEBYTECODE must be set so pytest doesn't drop "
        ".pyc cache files into the output_dir."
    )


def test_b5_sandbox_propagates_only_allowlisted_keys(monkeypatch, tmp_path):
    """Beyond the four forced keys, ONLY allowlist entries survive."""
    monkeypatch.setenv("ARBITRARY_HOST_VAR", "leaked")
    runner = SandboxTestRunner(output_dir=str(tmp_path))
    env = runner._build_sandbox_env()

    forced_keys = {"HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE", "LANG", "LC_ALL"}
    allowed = set(SandboxTestRunner._SANDBOX_ALLOWED_ENV) | forced_keys

    surprise = set(env) - allowed
    assert not surprise, (
        f"Sandbox env contains keys outside the allowlist: {surprise}. "
        f"_SANDBOX_ALLOWED_ENV must be a closed set."
    )
    assert "ARBITRARY_HOST_VAR" not in env
