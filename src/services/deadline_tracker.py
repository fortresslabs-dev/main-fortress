"""
Deadline Tracker — REQ-012: Compliance Deadline Calendar.
Seeds and refreshes ComplianceDeadline rows for known recurring compliance
deadlines, updates days_remaining, and emits alerts for approaching deadlines.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# Alert thresholds (days before deadline)
_ALERT_THRESHOLDS = [90, 60, 30, 14, 7]

# Static seed deadlines — extended at runtime by connector-supplied deadlines
_SEED_DEADLINES: List[Dict[str, Any]] = [
    # FFIEC Call Reports (quarterly — Q1–Q4)
    {
        "deadline_key": "FFIEC-CALL-Q1",
        "regulation_id": "FFIEC-CALL-041",
        "regulator": "FFIEC",
        "deadline_type": "quarterly_filing",
        "deadline_name": "FFIEC Call Report — Q1 (March 31 quarter-end)",
        "description": "FFIEC 031/041/051 Call Report due 30 calendar days after March 31",
        "month_day": (4, 30),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=30",
    },
    {
        "deadline_key": "FFIEC-CALL-Q2",
        "regulation_id": "FFIEC-CALL-041",
        "regulator": "FFIEC",
        "deadline_type": "quarterly_filing",
        "deadline_name": "FFIEC Call Report — Q2 (June 30 quarter-end)",
        "description": "FFIEC 031/041/051 Call Report due 30 calendar days after June 30",
        "month_day": (7, 30),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=7;BYMONTHDAY=30",
    },
    {
        "deadline_key": "FFIEC-CALL-Q3",
        "regulation_id": "FFIEC-CALL-041",
        "regulator": "FFIEC",
        "deadline_type": "quarterly_filing",
        "deadline_name": "FFIEC Call Report — Q3 (September 30 quarter-end)",
        "description": "FFIEC 031/041/051 Call Report due 30 calendar days after September 30",
        "month_day": (10, 30),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=30",
    },
    {
        "deadline_key": "FFIEC-CALL-Q4",
        "regulation_id": "FFIEC-CALL-041",
        "regulator": "FFIEC",
        "deadline_type": "quarterly_filing",
        "deadline_name": "FFIEC Call Report — Q4 (December 31 quarter-end)",
        "description": "FFIEC 031/041/051 Call Report due 30 calendar days after December 31",
        "month_day": (1, 30),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=30",
    },
    # HMDA LAR annual filing
    {
        "deadline_key": "CFPB-HMDA-LAR-ANNUAL",
        "regulation_id": "CFPB-HMDA-001",
        "regulator": "CFPB",
        "deadline_type": "annual_filing",
        "deadline_name": "HMDA Loan Application Register (LAR) Annual Submission",
        "description": "LAR for prior calendar year due by March 1",
        "month_day": (3, 1),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=1",
    },
    # CRA exam preparation (annual internal review)
    {
        "deadline_key": "CRA-ANNUAL-REVIEW",
        "regulation_id": "CRA-OCC-001",
        "regulator": "OCC",
        "deadline_type": "internal_review",
        "deadline_name": "CRA Annual Self-Assessment and Public File Update",
        "description": "Annual CRA self-assessment and public file maintenance review",
        "month_day": (12, 31),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31",
    },
    # Section 1071 compliance (CFPB)
    {
        "deadline_key": "CFPB-1071-COMPLIANCE",
        "regulation_id": "CFPB-1071-001",
        "regulator": "CFPB",
        "deadline_type": "compliance_effective_date",
        "deadline_name": "CFPB Section 1071 Small Business Lending Data Collection — Effective",
        "description": "Final rule compliance effective July 1, 2026 for larger covered institutions",
        "fixed_date": date(2026, 7, 1),
        "is_recurring": False,
        "recurrence_rule": None,
    },
    # FinCEN Beneficial Ownership amendment
    {
        "deadline_key": "FINCEN-BO-AMENDMENT",
        "regulation_id": "OCC-BSA-CDD-001",
        "regulator": "FinCEN",
        "deadline_type": "compliance_effective_date",
        "deadline_name": "FinCEN CDD Beneficial Ownership Amendment (20% threshold) — Effective",
        "description": "FinCEN amendment lowering beneficial ownership threshold to 20% effective April 11, 2026",
        "fixed_date": date(2026, 4, 11),
        "is_recurring": False,
        "recurrence_rule": None,
    },
    # Annual IRR review
    {
        "deadline_key": "IRR-ANNUAL-REVIEW",
        "regulation_id": "IRR-OCC-001",
        "regulator": "OCC",
        "deadline_type": "internal_review",
        "deadline_name": "Annual IRR Policy Board Review",
        "description": "Board of Directors annual approval of IRR policy with EVE/NII limits",
        "month_day": (12, 31),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31",
    },
    # FFIEC CAT annual assessment
    {
        "deadline_key": "FFIEC-CAT-ANNUAL",
        "regulation_id": "FFIEC-CAT-001",
        "regulator": "FFIEC",
        "deadline_type": "internal_assessment",
        "deadline_name": "FFIEC Cybersecurity Assessment Tool (CAT) Annual Assessment",
        "description": "Annual completion of FFIEC CAT Inherent Risk Profile and Maturity Assessment",
        "month_day": (12, 31),
        "is_recurring": True,
        "recurrence_rule": "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31",
    },
]


def _next_occurrence(month_day: tuple[int, int]) -> date:
    """Return the next occurrence of (month, day) on or after today."""
    today = date.today()
    month, day = month_day
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        candidate = date(today.year, month, 28)
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            candidate = date(today.year + 1, month, 28)
    return candidate


class DeadlineTracker:
    """Seeds and refreshes compliance deadlines in the database.

    Called from ScraperScheduler._run_scrape_cycle() to keep
    days_remaining current and emit threshold alerts.
    """

    async def run(self, session) -> int:
        """Upsert all seed deadlines and refresh days_remaining.

        Returns:
            Count of deadline rows written/updated.
        """
        from sqlalchemy import select, update
        from src.models.database import ComplianceDeadline

        today = date.today()
        touched = 0

        for seed in _SEED_DEADLINES:
            # Determine the actual deadline date
            if seed.get("fixed_date"):
                deadline_date = seed["fixed_date"]
            else:
                deadline_date = _next_occurrence(seed["month_day"])

            days_remaining = (deadline_date - today).days

            with session.no_autoflush:
                existing = await session.execute(
                    select(ComplianceDeadline).where(
                        ComplianceDeadline.deadline_key == seed["deadline_key"]
                    )
                )
                row = existing.scalar_one_or_none()

            if row is None:
                session.add(
                    ComplianceDeadline(
                        deadline_key=seed["deadline_key"],
                        regulation_id=seed.get("regulation_id"),
                        regulator=seed["regulator"],
                        deadline_type=seed["deadline_type"],
                        deadline_name=seed["deadline_name"],
                        description=seed.get("description"),
                        deadline_date=deadline_date,
                        days_remaining=days_remaining,
                        is_recurring=seed.get("is_recurring", False),
                        recurrence_rule=seed.get("recurrence_rule"),
                        is_active=True,
                    )
                )
            else:
                # For recurring deadlines, advance to next occurrence once past
                if row.is_recurring and row.deadline_date < today:
                    row.deadline_date = deadline_date
                row.days_remaining = (row.deadline_date - today).days
                row.updated_at = datetime.now(timezone.utc)

            touched += 1
            self._check_alert_thresholds(seed["deadline_key"], days_remaining, row)

        logger.info("deadlines_refreshed", count=touched, today=str(today))
        return touched

    @staticmethod
    def _check_alert_thresholds(
        deadline_key: str,
        days_remaining: int,
        row: Any,
    ) -> None:
        """Log structured alert if approaching a threshold and not yet sent."""
        for threshold in _ALERT_THRESHOLDS:
            if days_remaining <= threshold:
                already_sent = False
                if row is not None:
                    already_sent = bool(
                        (row.alert_thresholds_sent or {}).get(str(threshold))
                    )
                if not already_sent:
                    logger.warning(
                        "compliance_deadline_alert",
                        deadline_key=deadline_key,
                        days_remaining=days_remaining,
                        threshold=threshold,
                    )
                    # Mark as sent (in-memory; will be persisted on commit)
                    if row is not None:
                        alerts = dict(row.alert_thresholds_sent or {})
                        alerts[str(threshold)] = datetime.now(timezone.utc).isoformat()
                        row.alert_thresholds_sent = alerts
                break  # Only fire the closest threshold alert

    async def refresh_days_remaining(self, session) -> int:
        """Recompute days_remaining for all active future deadlines.

        Lightweight version for Pipeline 2 — called after each UpdateService
        sync cycle so the derived days_remaining column stays accurate relative
        to CURRENT_DATE without requiring P1 to recompute it.

        Returns the number of rows updated.
        """
        from sqlalchemy import text

        result = await session.execute(text("""
            UPDATE compliance_deadlines
            SET days_remaining = EXTRACT(DAY FROM deadline_date - CURRENT_DATE)::INTEGER,
                updated_at = NOW()
            WHERE is_active = TRUE
              AND deadline_date >= CURRENT_DATE
            RETURNING id
        """))
        await session.commit()
        count = result.rowcount
        logger.info("deadline_days_remaining_refreshed", count=count)
        return count
