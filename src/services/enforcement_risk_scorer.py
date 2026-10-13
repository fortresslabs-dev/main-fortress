"""
Enforcement Risk Scorer — REQ-011.
Computes a 0–1 enforcement risk score per regulation_id based on recent
enforcement actions, penalty amounts, and trend direction.
Score is written to EnforcementRiskScore table and back-propagated to
ComplianceRule.enforcement_risk_score.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Normalisation caps
_MAX_ACTIONS = 50          # 50+ actions in period → score component = 1.0
_MAX_PENALTY_USD = 1_000_000_000  # $1B+ penalty → score component = 1.0

# Weights
_W_FREQUENCY = 0.45
_W_PENALTY = 0.35
_W_TREND = 0.20


def _normalise(value: float, cap: float) -> float:
    """Clamp value to [0, 1] using a log scale relative to cap."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(cap))


def compute_risk_score(
    action_count: int,
    avg_penalty_usd: Optional[float],
    trend_direction: Optional[str],
) -> float:
    """Return a 0–1 enforcement risk score.

    trend_direction: "increasing" | "decreasing" | "stable"
    """
    if action_count <= 0:
        return 0.0
    freq_score = _normalise(action_count, _MAX_ACTIONS)
    penalty_score = _normalise(avg_penalty_usd or 0.0, _MAX_PENALTY_USD)

    trend_map = {"increasing": 1.0, "stable": 0.5, "decreasing": 0.0}
    trend_score = trend_map.get(trend_direction or "stable", 0.5)

    raw = (
        _W_FREQUENCY * freq_score
        + _W_PENALTY * penalty_score
        + _W_TREND * trend_score
    )
    return round(min(1.0, max(0.0, raw)), 4)


class EnforcementRiskScorer:
    """Aggregates enforcement actions per regulation and writes risk scores.

    Called from ScraperScheduler after enforcement actions are ingested.
    """

    def __init__(self, period_months: int = 24):
        self.period_months = period_months

    async def run(self, session) -> int:
        """Compute and upsert risk scores for all active regulations.

        Returns:
            Count of risk score rows written.
        """
        from sqlalchemy import func, select, text
        from src.models.database import (
            ComplianceRule,
            EnforcementAction,
            EnforcementRiskScore,
        )

        cutoff_date = date.today() - timedelta(days=self.period_months * 30)

        # Aggregate enforcement actions by regulation citation
        result = await session.execute(
            select(
                EnforcementAction.regulations_cited,
                func.count(EnforcementAction.id).label("action_count"),
                func.avg(EnforcementAction.penalty_amount_usd).label("avg_penalty"),
                func.sum(EnforcementAction.penalty_amount_usd).label("total_penalty"),
                EnforcementAction.regulator,
            )
            .where(
                EnforcementAction.is_active == True,
                EnforcementAction.action_date >= cutoff_date,
            )
            .group_by(EnforcementAction.regulations_cited, EnforcementAction.regulator)
        )
        raw_rows = result.all()

        # Expand JSONB arrays into per-regulation counts
        reg_stats: Dict[str, Dict] = {}
        for regulations_cited, action_count, avg_penalty, total_penalty, regulator in raw_rows:
            for reg_id in (regulations_cited or []):
                if reg_id not in reg_stats:
                    reg_stats[reg_id] = {
                        "action_count": 0,
                        "avg_penalty": 0.0,
                        "total_penalty": 0.0,
                        "regulator": regulator,
                    }
                reg_stats[reg_id]["action_count"] += action_count
                if avg_penalty:
                    reg_stats[reg_id]["avg_penalty"] = float(avg_penalty)
                if total_penalty:
                    reg_stats[reg_id]["total_penalty"] += float(total_penalty)

        # Also compute trend — compare recent 12-month count vs prior 12-month count
        trend_map = await self._compute_trends(session, cutoff_date)

        # Pass 1 — upsert EnforcementRiskScore + collect per-rule matches.
        #
        # We SELECT the matching rule_ids per citation (same 2-branch join as
        # R3) and accumulate `rule_score_map[rule_id] = max(prior, score)`.
        # Then Pass 2 does a single bulk UPDATE grouped by score. This
        # replaces the R3 loop's last-write-wins overwrite (where a family
        # label at 0.78 could stomp a precise CFR citation at 0.85 on the
        # same rule) with MAX-across-matches semantics.
        rule_score_map: Dict[str, float] = {}
        # winning_reg tracks which reg_id contributed the MAX per rule —
        # kept for structured logging so an ops incident can trace which
        # citation drove a rule's final score.
        winning_reg: Dict[str, str] = {}
        upserted = 0
        for reg_id, stats in reg_stats.items():
            trend = trend_map.get(reg_id, "stable")
            score = compute_risk_score(
                stats["action_count"],
                stats["avg_penalty"],
                trend,
            )

            with session.no_autoflush:
                existing = await session.execute(
                    select(EnforcementRiskScore).where(
                        EnforcementRiskScore.regulation_id == reg_id,
                        EnforcementRiskScore.period_months == self.period_months,
                    )
                )
                row = existing.scalar_one_or_none()
            if row is None:
                session.add(
                    EnforcementRiskScore(
                        regulation_id=reg_id,
                        regulator=stats["regulator"],
                        period_months=self.period_months,
                        action_count=stats["action_count"],
                        avg_penalty_usd=stats["avg_penalty"] or None,
                        total_penalty_usd=stats["total_penalty"] or None,
                        trend_direction=trend,
                        risk_score=score,
                        computed_at=datetime.now(timezone.utc),
                    )
                )
            else:
                row.action_count = stats["action_count"]
                row.avg_penalty_usd = stats["avg_penalty"] or None
                row.total_penalty_usd = stats["total_penalty"] or None
                row.trend_direction = trend
                row.risk_score = score
                row.computed_at = datetime.now(timezone.utc)
            upserted += 1

            # SELECT (not UPDATE) matching rule_ids for this reg_id.
            # Shape-aware 2-branch match (R3): CFR/USC citation → cfr_citation;
            # bare family label (BSA/AML/…) → category/rule_id substring.
            matches = await session.execute(text("""
                SELECT rule_id FROM compliance_rules
                 WHERE (
                         cfr_citation IS NOT NULL
                         AND regexp_replace(lower(cfr_citation),        '[.\\s§]', '', 'g')
                           = regexp_replace(lower(:reg_id),             '[.\\s§]', '', 'g')
                       )
                    OR (
                         lower(:reg_id) IN
                           ('bsa','aml','cra','hmda','ecoa','tila','respa','fca','fdca')
                         AND (
                              lower(category) LIKE '%' || lower(:reg_id) || '%'
                           OR lower(rule_id)  LIKE '%' || lower(:reg_id) || '%'
                         )
                       )
            """), {"reg_id": reg_id})
            matched_ids = [r[0] for r in matches.fetchall()]
            for rid in matched_ids:
                if score > rule_score_map.get(rid, 0.0):
                    rule_score_map[rid] = score
                    winning_reg[rid] = reg_id
            logger.debug(
                "enforcement_risk_match",
                reg_id=reg_id, score=score, matched=len(matched_ids),
            )

        # Pass 2 — bulk UPDATE grouped by score.
        # One UPDATE per distinct score value keeps the statement count
        # bounded by the (small) cardinality of `compute_risk_score` outputs
        # regardless of rule fan-out.
        by_score: Dict[float, List[str]] = defaultdict(list)
        for rid, s in rule_score_map.items():
            by_score[s].append(rid)

        rules_updated_total = 0
        for score_val, rule_ids in by_score.items():
            res = await session.execute(text("""
                UPDATE compliance_rules
                   SET enforcement_risk_score = :score,
                       updated_at             = NOW()
                 WHERE rule_id = ANY(:rule_ids)
            """), {"score": score_val, "rule_ids": rule_ids})
            rules_updated_total += res.rowcount or 0

        logger.info(
            "enforcement_risk_backprop_bulk",
            unique_rules=len(rule_score_map),
            unique_scores=len(by_score),
            rows_updated=rules_updated_total,
        )
        logger.info("enforcement_risk_scores_computed", regulations=upserted, period_months=self.period_months)
        return upserted

    async def _compute_trends(self, session, cutoff_date: date) -> Dict[str, str]:
        """Compare recent 12 months vs prior 12 months action counts per regulation."""
        from sqlalchemy import func, select
        from src.models.database import EnforcementAction

        mid_date = date.today() - timedelta(days=365)

        async def _count_period(start: date, end: date):
            r = await session.execute(
                select(
                    EnforcementAction.regulations_cited,
                    func.count(EnforcementAction.id).label("cnt"),
                )
                .where(
                    EnforcementAction.is_active == True,
                    EnforcementAction.action_date >= start,
                    EnforcementAction.action_date < end,
                )
                .group_by(EnforcementAction.regulations_cited)
            )
            counts: Dict[str, int] = {}
            for regs, cnt in r.all():
                for reg in (regs or []):
                    counts[reg] = counts.get(reg, 0) + cnt
            return counts

        recent = await _count_period(mid_date, date.today())
        prior = await _count_period(cutoff_date, mid_date)

        trends: Dict[str, str] = {}
        for reg_id in set(list(recent.keys()) + list(prior.keys())):
            r = recent.get(reg_id, 0)
            p = prior.get(reg_id, 0)
            if p == 0:
                trends[reg_id] = "increasing" if r > 0 else "stable"
            elif r > p * 1.2:
                trends[reg_id] = "increasing"
            elif r < p * 0.8:
                trends[reg_id] = "decreasing"
            else:
                trends[reg_id] = "stable"

        return trends
