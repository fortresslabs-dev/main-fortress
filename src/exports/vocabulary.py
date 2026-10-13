"""Triple-locked vocabulary per design doc §9 and §11.4 Q18.

Vocabulary is enforced at three layers:

  1. DB CHECK constraint or PostgreSQL ENUM (migration 042)
  2. SQLAlchemy Python enum (src/models/database.py)
  3. Render-time validator (this module)

A render that touches an unknown value raises ``VocabularyError`` before
hashing — so an artifact never gets created with stale vocab in the
first place. Adding a new value means: ALTER TYPE in a new migration
+ extend the Py enum + register here + bump ``CURRENT_VOCAB_VERSION``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


CURRENT_VOCAB_VERSION = "v1.2026.06"

PASS_REVIEW_HOLD: Sequence[str] = ("PASS", "REVIEW", "HOLD")
TEST_RESULTS: Sequence[str] = ("PASS", "PARTIAL", "FAIL", "NOT_TESTED")
FIVE_CS: Sequence[str] = (
    "concern", "cause", "consequence", "corrective_action", "commitment",
)
FINDING_STATUSES: Sequence[str] = (
    "open", "past_due", "pending_validation", "validated_closed",
)
FINDING_TYPES: Sequence[str] = (
    "MRA", "MRIA", "MRBA", "VIOLATION", "OBSERVATION",
)
CHANGE_DISPOSITIONS: Sequence[str] = (
    "acknowledged", "requires_action", "not_applicable",
)
OFFICER_ROLES: Sequence[str] = (
    "bsa_officer", "cco", "cro", "internal_audit",
    "audit_committee_chair", "board_chair", "ceo", "other",
)


class VocabularyError(ValueError):
    """Raised when a render-time value is not in the locked vocabulary."""


@dataclass(frozen=True)
class _Rule:
    """One JSON-path rule: every value at `path` must be one of `vocab`."""
    path: str
    vocab: Sequence[str]


# Path syntax: dot-delimited keys; "[]" descends into a list of dicts;
# matched values may be either str or a list of str (handled in apply()).
_RULES: tuple[_Rule, ...] = (
    _Rule("execution[].result", TEST_RESULTS),
    _Rule("findings[].type", FINDING_TYPES),
    _Rule("findings[].status", FINDING_STATUSES),
    _Rule("change_log[].disposition", CHANGE_DISPOSITIONS),
    _Rule("signoffs[].role", OFFICER_ROLES),
    _Rule("verdict", PASS_REVIEW_HOLD),
)


def _walk(obj, path_parts: list[str]):
    """Yield every leaf value reached by the path."""
    if not path_parts:
        yield obj
        return
    head, *rest = path_parts
    if head.endswith("[]"):
        key = head[:-2]
        items = obj.get(key, []) if isinstance(obj, Mapping) else []
        for item in items:
            yield from _walk(item, rest)
        return
    if isinstance(obj, Mapping) and head in obj:
        yield from _walk(obj[head], rest)


def validate(snapshot: Mapping) -> None:
    """Walk every locked path and reject unknown values."""
    for rule in _RULES:
        for leaf in _walk(snapshot, rule.path.split(".")):
            if leaf is None:
                continue
            values = leaf if isinstance(leaf, (list, tuple)) else (leaf,)
            for v in values:
                if v not in rule.vocab:
                    raise VocabularyError(
                        f"{rule.path} carries unknown value {v!r}; "
                        f"allowed: {tuple(rule.vocab)}"
                    )
