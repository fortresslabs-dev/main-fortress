"""Stable JSON canonicalisation + content hash per design doc §2.2.

The content hash is computed BEFORE rendering. Two runs against the
same as-of must produce the same hash, and that hash is the
deterministic part of the S3 key — so re-running idempotently returns
the existing artifact instead of an unnecessary re-render.

Envelope contents (everything that influences output bytes):

  * snapshot data (sorted keys, ISO dates, Decimal as 2dp string)
  * template_version
  * vocab_version
  * app_commit

If any of those changes, the hash changes — explainable to a model-risk
reviewer why two artifacts with the same as-of can differ (template was
revised).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid as _uuid
from decimal import Decimal
from typing import Any, Mapping


def _default(o: Any):
    if isinstance(o, _dt.datetime):
        # Always-Z for timestamps so canonical form survives tz drift.
        return o.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(o, _dt.date):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, Decimal):
        # 2dp default for currency. Integer-valued Decimals (counts) come
        # in as int from snapshot.py before they ever reach here.
        return format(o.quantize(Decimal("0.01")), "f")
    if isinstance(o, _uuid.UUID):
        return str(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"canonical: don't know how to serialize {type(o).__name__}")


def canonical_bytes(envelope: Mapping[str, Any]) -> bytes:
    """Stable bytes for hashing. Keys are sorted recursively; no
    whitespace; no NaN; values normalised by ``_default``."""
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_default,
    ).encode("utf-8")


def hash_envelope(
    snapshot: Mapping[str, Any],
    *,
    template_version: str,
    vocab_version: str,
    app_commit: str,
) -> tuple[str, str]:
    """Return (sha256_hex, version_hash7).

    version_hash7 is the first 7 hex chars — short enough for filenames
    and the document-ID line ("v.4a1f93c"), long enough for non-collision
    in practice (16M bucket space per (bank, type, as_of) key).
    """
    envelope = {
        "snapshot": snapshot,
        "template_version": template_version,
        "vocab_version": vocab_version,
        "app_commit": app_commit,
    }
    digest = hashlib.sha256(canonical_bytes(envelope)).hexdigest()
    return digest, digest[:7]
