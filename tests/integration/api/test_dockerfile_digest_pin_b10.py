"""
B10 — every FROM directive in shipped Dockerfiles must be digest-pinned.

A floating `FROM python:3.11-slim` lets the registry serve a different
image on every build. That's:
  - a reproducibility hole (a working build can fail tomorrow with no
    code change), and
  - a supply-chain hole (a tag takeover swaps the base image silently).

This test parses both production Dockerfiles and asserts every FROM
references an image by `@sha256:<64hex>`. Refresh procedure:

    docker buildx imagetools inspect python:3.11-slim

then update the digest in the Dockerfile.

The legacy top-level `Dockerfile` is intentionally excluded — it is
no longer referenced by docker-compose or any Make target. If it
comes back into the build path, add it to PINNED_DOCKERFILES below.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

PINNED_DOCKERFILES = [
    REPO_ROOT / "Dockerfile.pipeline1",
    REPO_ROOT / "Dockerfile.pipeline2",
]

# Captures `FROM <ref>` optionally followed by `AS <stage>`.
# <ref> is everything between FROM and the trailing AS/end-of-line.
_FROM_RE = re.compile(
    r"(?im)^\s*FROM\s+(?P<ref>\S+)(?:\s+AS\s+\S+)?\s*$"
)

# image[@sha256:<64-hex>] — the bit we require.
_DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}$")


def _from_refs(dockerfile: Path) -> list[tuple[int, str]]:
    """Return (line_no, image_ref) for every FROM directive."""
    refs = []
    for lineno, line in enumerate(dockerfile.read_text().splitlines(), start=1):
        m = _FROM_RE.match(line)
        if m:
            refs.append((lineno, m.group("ref")))
    return refs


@pytest.mark.parametrize("dockerfile", PINNED_DOCKERFILES, ids=lambda p: p.name)
def test_b10_every_from_is_digest_pinned(dockerfile: Path):
    assert dockerfile.exists(), f"{dockerfile} not found — repo layout changed"
    refs = _from_refs(dockerfile)
    assert refs, f"{dockerfile.name}: no FROM directives found"

    unpinned = [(ln, ref) for ln, ref in refs if not _DIGEST_RE.search(ref)]
    assert not unpinned, (
        f"{dockerfile.name} has unpinned FROM references — every base "
        f"image must be addressed by @sha256:<64hex>. Offenders:\n"
        + "\n".join(f"  line {ln}: {ref}" for ln, ref in unpinned)
        + "\n\nRefresh with: docker buildx imagetools inspect <image>:<tag>"
    )


@pytest.mark.parametrize("dockerfile", PINNED_DOCKERFILES, ids=lambda p: p.name)
def test_b10_digests_are_well_formed(dockerfile: Path):
    """Catch typos: digest must be exactly 64 lowercase hex chars."""
    for lineno, ref in _from_refs(dockerfile):
        # Extract the digest after the last `@`.
        if "@" not in ref:
            pytest.fail(
                f"{dockerfile.name}:{lineno}: FROM {ref!r} has no @ — "
                f"caught separately by test_b10_every_from_is_digest_pinned"
            )
        digest_part = ref.rsplit("@", 1)[1]
        assert digest_part.startswith("sha256:"), (
            f"{dockerfile.name}:{lineno}: digest must start with 'sha256:', "
            f"got {digest_part!r}"
        )
        hex_part = digest_part[len("sha256:"):]
        assert re.fullmatch(r"[a-f0-9]{64}", hex_part), (
            f"{dockerfile.name}:{lineno}: digest hex must be 64 lowercase "
            f"hex chars, got {hex_part!r} ({len(hex_part)} chars)"
        )
