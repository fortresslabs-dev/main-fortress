"""
B9 — Dockerfiles must drop privileges before CMD.

We don't need docker available at test-time — parsing the Dockerfile is
enough to regression-lock the contract. The rule:

  In each Dockerfile's FINAL stage, there must be a `USER` directive
  before the trailing `CMD`/`ENTRYPOINT`, and that USER must not be
  `root` or numeric `0`.

This catches the common drift where someone adds a stage to a previously-
hardened Dockerfile and forgets to re-issue the USER directive.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Both pipeline images are deployed to ECS — both must run as non-root.
# Dockerfile (plain) is the legacy combined image; if present, hold it
# to the same bar.
DOCKERFILES = [
    REPO_ROOT / "Dockerfile.pipeline1",
    REPO_ROOT / "Dockerfile.pipeline2",
]


def _split_into_stages(text: str) -> list[str]:
    """Split a Dockerfile by `FROM` directives. Each chunk is one build stage."""
    # Match FROM at line-start (case-insensitive). Keep the FROM line with its stage.
    parts = re.split(r"(?im)^(?=FROM\s)", text)
    return [p for p in parts if p.strip()]


def _final_user(stage_text: str) -> str | None:
    """Return the LAST `USER <name>` directive in a stage, or None."""
    matches = re.findall(r"(?im)^\s*USER\s+(\S+)\s*$", stage_text)
    return matches[-1] if matches else None


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_b9_final_stage_drops_root(dockerfile: Path):
    assert dockerfile.exists(), f"{dockerfile} not found — repo layout changed"
    stages = _split_into_stages(dockerfile.read_text())
    assert stages, f"{dockerfile.name} has no FROM stages"

    final_stage = stages[-1]
    user = _final_user(final_stage)

    assert user is not None, (
        f"{dockerfile.name} final stage has no USER directive — the container "
        f"would run as root. Add `USER <non-root>` before the trailing CMD."
    )
    assert user.lower() not in {"root", "0"}, (
        f"{dockerfile.name} final stage sets USER={user!r} — must be non-root."
    )


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_b9_user_directive_precedes_cmd(dockerfile: Path):
    """USER must appear before the trailing CMD/ENTRYPOINT in the final stage —
    otherwise the privilege drop happens too late and CMD still runs as root."""
    final_stage = _split_into_stages(dockerfile.read_text())[-1]
    lines = [ln.strip() for ln in final_stage.splitlines()]

    last_user_idx = max(
        (i for i, ln in enumerate(lines) if re.match(r"(?i)^USER\s", ln)),
        default=-1,
    )
    last_cmd_idx = max(
        (i for i, ln in enumerate(lines) if re.match(r"(?i)^(CMD|ENTRYPOINT)\s", ln)),
        default=-1,
    )

    assert last_user_idx != -1, f"{dockerfile.name}: missing USER directive"
    assert last_cmd_idx != -1, f"{dockerfile.name}: missing CMD/ENTRYPOINT"
    assert last_user_idx < last_cmd_idx, (
        f"{dockerfile.name}: USER (line {last_user_idx}) must precede "
        f"CMD/ENTRYPOINT (line {last_cmd_idx}) so the privilege drop "
        f"applies to the running process."
    )
