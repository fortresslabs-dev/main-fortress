"""Authority gate — the class-boundary check between LAW and everything else.

Referenced from ``docs/correction_smb.md`` §11.3. This is the single
predicate the Test Factory, examiner-package export, and Compliance
Engine consult to decide whether a rule may be asserted as codified law.

Behaviour::

    may_assert_as_law(rule) is True
        ⇔ rule.authority_type == "LAW"
          AND rule.citation_verified is True

Callers use the outcome to route the rule to one of two rails:

    LAW rail:
        Test Factory PR body cites the CFR section.
        examiner_package export renders the citation column populated.
        Compliance Engine emits a BLOCKING check on transactions.

    ADVISORY rail:
        Test Factory PR body cites the manual / bulletin
        (``authority_source``) — NOT a CFR section.
        examiner_package renders the citation column empty; the source
        column carries the manual reference.
        Compliance Engine emits a NON-BLOCKING (advisory) check.

The predicate is deliberately trivial. Its value is that it is the
*only* place LAW-vs-not gets decided; the rest of the system reads
from here. That is what makes the C-1 and C-6 fabrication classes
structurally impossible rather than just patched.
"""
from __future__ import annotations

from typing import Any, Iterable, List


def _get(rule: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-key accessor — rules may arrive as dicts or ORM rows."""
    if rule is None:
        return default
    if hasattr(rule, key):
        return getattr(rule, key)
    if isinstance(rule, dict):
        return rule.get(key, default)
    return default


def may_assert_as_law(rule: Any) -> bool:
    """True iff the rule may be presented to an examiner as codified law.

    Reads two fields:
      * ``authority_type`` — must be exactly ``"LAW"``.
      * ``citation_verified`` — must be truthy.

    All other authority types (``STATUTE``, ``GUIDANCE``,
    ``INTERNAL_CONTROL``) fall through to the advisory rail. NULL /
    missing authority_type falls through as well — an unclassified
    rule is not law until proven otherwise.
    """
    if _get(rule, "authority_type") != "LAW":
        return False
    return bool(_get(rule, "citation_verified"))


def split_blocking_advisory(rules: Iterable[Any]) -> tuple[List[Any], List[Any]]:
    """Partition ``rules`` into (blocking, advisory) by the gate."""
    blocking: List[Any] = []
    advisory: List[Any] = []
    for r in rules:
        (blocking if may_assert_as_law(r) else advisory).append(r)
    return blocking, advisory


def citation_for_pr_body(rule: Any) -> str:
    """Return the citation string to embed in a Test Factory PR body.

    LAW  → ``"Required by 31 C.F.R. § 1010.410(f)"``
    else → ``"Control expectation per <authority_source>"``

    Callers must not build the string themselves — that split is
    exactly where prior corpus rows drifted into fabricating CFR cites
    for internal-control expectations.
    """
    if may_assert_as_law(rule):
        cite = _get(rule, "cfr_citation") or _get(rule, "citation") or "<citation missing>"
        return f"Required by {cite}"
    source = _get(rule, "authority_source") or "internal control"
    return f"Control expectation per {source}"
