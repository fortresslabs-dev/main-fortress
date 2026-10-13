"""
Change Manager — REQ-013: Regulatory Change Management.
Detects when ComplianceRule content changes, computes a semantic diff,
classifies the change type, and creates RegulatoryChangeRecord rows.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Change type classifier keywords
_ADDED_KEYWORDS = re.compile(r"\bnew\b|\badded\b|\bintroduced\b|\beffective\b", re.I)
_CLARIFIED_KEYWORDS = re.compile(r"\bclarif\b|\bamend\b|\bupdat\b|\bmodifi\b", re.I)
_REMOVED_KEYWORDS = re.compile(r"\bremov\b|\brescind\b|\bwithdrawn\b|\bexpir\b", re.I)

# Impact rating thresholds (based on % of content changed)
_IMPACT_HIGH_PCT = 50.0
_IMPACT_MEDIUM_PCT = 15.0


def _change_ref(rule_id: str, detected_at: datetime) -> str:
    """Generate a short unique change reference."""
    raw = f"{rule_id}::{detected_at.isoformat()}"
    return "CHG-" + hashlib.sha256(raw.encode()).hexdigest()[:8].upper()


def _compute_text_diff(old_text: str, new_text: str) -> Dict[str, Any]:
    """Compute a structured diff between two text strings.

    Returns:
        {
            "added": [list of added sentences],
            "removed": [list of removed sentences],
            "change_pct": float,
        }
    """
    old_lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", old_text) if s.strip()]
    new_lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", new_text) if s.strip()]

    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    added: List[str] = []
    removed: List[str] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])

    total_lines = max(len(old_lines), len(new_lines), 1)
    change_pct = (len(added) + len(removed)) / (2 * total_lines) * 100.0

    return {
        "added": added[:20],    # cap to avoid huge JSON payloads
        "removed": removed[:20],
        "change_pct": round(change_pct, 2),
    }


def _classify_change_type(old_text: str, new_text: str, is_new_rule: bool = False) -> str:
    """Return change type: added | modified | removed | clarified."""
    if is_new_rule:
        return "added"
    if not new_text:
        return "removed"
    delta = _compute_text_diff(old_text or "", new_text)
    if delta["change_pct"] < 5.0:
        return "clarified"
    if _CLARIFIED_KEYWORDS.search(new_text):
        return "clarified"
    return "modified"


def _impact_rating(change_pct: float) -> str:
    if change_pct >= _IMPACT_HIGH_PCT:
        return "high"
    if change_pct >= _IMPACT_MEDIUM_PCT:
        return "medium"
    return "low"


class ChangeManager:
    """Detects regulatory content changes and records them for review.

    Called from ScraperScheduler._run_scrape_cycle() after upserts are
    committed, passing changed rules for processing.
    """

    async def record_change(
        self,
        session,
        rule_id: str,
        regulator: str,
        old_text: Optional[str],
        new_text: str,
        old_hash: Optional[str],
        new_hash: str,
        is_new_rule: bool = False,
    ) -> Optional[str]:
        """Create a RegulatoryChangeRecord for a detected rule change.

        Returns:
            change_ref if a record was created, None if skipped.
        """
        from src.models.database import ComplianceRule, RegulatoryChangeRecord

        detected_at = datetime.now(timezone.utc)
        change_ref = _change_ref(rule_id, detected_at)

        # Compute diff
        delta = _compute_text_diff(old_text or "", new_text)
        change_type = _classify_change_type(old_text or "", new_text, is_new_rule)
        impact = _impact_rating(delta["change_pct"])

        # Build human-readable summary
        n_added = len(delta["added"])
        n_removed = len(delta["removed"])
        if is_new_rule:
            summary = f"New rule added: {rule_id}"
        else:
            summary = (
                f"Rule {rule_id} changed: {n_added} sentences added, "
                f"{n_removed} sentences removed ({delta['change_pct']:.1f}% content change)"
            )

        # Semantic diff as text (first 3 added/removed shown)
        added_preview = " | ".join(delta["added"][:3])
        removed_preview = " | ".join(delta["removed"][:3])
        semantic_diff = f"ADDED: {added_preview}\nREMOVED: {removed_preview}"

        session.add(
            RegulatoryChangeRecord(
                change_ref=change_ref,
                regulation_id=rule_id,
                rule_id=rule_id,
                regulator=regulator,
                detected_at=detected_at,
                previous_hash=old_hash,
                new_hash=new_hash,
                change_summary=summary,
                semantic_diff=semantic_diff[:4000],
                change_delta=delta,
                impact_rating=impact,
                change_type=change_type,
                status="detected",
                status_updated_at=detected_at,
                is_active=True,
            )
        )

        # Also update ComplianceRule enrichment fields
        from sqlalchemy import update
        await session.execute(
            update(ComplianceRule)
            .where(ComplianceRule.rule_id == rule_id)
            .values(
                previous_content=old_text,
                change_delta=delta,
                change_type=change_type,
            )
        )

        logger.info(
            "change_record_created",
            change_ref=change_ref,
            rule_id=rule_id,
            change_type=change_type,
            impact=impact,
            change_pct=delta["change_pct"],
        )
        return change_ref

    async def bulk_record_changes(
        self,
        session,
        changed_rules: List[Dict[str, Any]],
    ) -> int:
        """Process a list of changed rule dicts and create change records.

        Each dict must have keys: rule_id, regulator, old_text, new_text,
        old_hash, new_hash, is_new_rule.

        Returns count of records created.
        """
        created = 0
        for rule in changed_rules:
            try:
                ref = await self.record_change(
                    session,
                    rule_id=rule["rule_id"],
                    regulator=rule.get("regulator", "UNKNOWN"),
                    old_text=rule.get("old_text"),
                    new_text=rule["new_text"],
                    old_hash=rule.get("old_hash"),
                    new_hash=rule["new_hash"],
                    is_new_rule=rule.get("is_new_rule", False),
                )
                if ref:
                    created += 1
            except Exception as exc:
                logger.warning(
                    "change_record_failed",
                    rule_id=rule.get("rule_id"),
                    error=str(exc),
                )

        logger.info("bulk_change_records_created", count=created)
        return created
