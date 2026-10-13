"""Daily sync digest — emails one summary of the last 24h of scrape_runs.

Runs as its own ECS one-off task (same image + role as the scraper).
EventBridge fires it at 06:00 UTC — four hours after the 02:00 UTC scrape
window, which is enough for slow connectors to finish.

Subject:
  [Fortress P1] Daily sync OK     — N/N connectors    (all rows success)
  [Fortress P1] Daily sync FAILED — F/N connectors    (>=1 row success=false)
  [Fortress P1] Daily sync — NO RUNS in last 24h      (zero rows in window)

The "no rows" case is treated as a failure subject on purpose: a scraper
that didn't run at all is the failure mode the digest is meant to surface.

Body is plain text with a per-row table; failed rows are prefixed with
`Failed: <error_message>` per the request.

Env:
  DIGEST_SNS_TOPIC_ARN   SNS topic to publish to       (required)
  AWS_REGION             SNS region                    (default us-east-1)
  DIGEST_WINDOW_HOURS    lookback window               (default 24)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
from sqlalchemy import text

from src.services.database import close_db, get_db, init_db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_SUBJECT_PREFIX = "[Fortress P1]"


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


async def _fetch_rows(window_hours: int) -> list[dict]:
    sql = text(
        """
        SELECT run_at,
               connector_name,
               success,
               COALESCE(rules_changed, 0)    AS rules_changed,
               COALESCE(error_message, '')   AS error_message,
               COALESCE(duration_seconds, 0) AS duration_seconds
        FROM scrape_runs
        WHERE run_at >= NOW() - (:hours || ' hours')::interval
        ORDER BY connector_name ASC, run_at DESC
        """
    )
    async with get_db() as session:
        result = await session.execute(sql, {"hours": str(window_hours)})
        return [dict(r._mapping) for r in result.all()]


def _build_message(rows: list[dict], window_hours: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    window_line = (
        f"Window: {window_start.strftime('%Y-%m-%d %H:%M UTC')} "
        f"→ {now.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    if not rows:
        subject = f"{_SUBJECT_PREFIX} Daily sync — NO RUNS in last {window_hours}h"
        body = (
            f"{window_line}\n\n"
            f"scrape_runs has 0 rows in the last {window_hours} hours.\n"
            f"Either the EventBridge scraper schedule is disabled or the scraper task "
            f"is not reaching scrape_monitor.\n"
        )
        return subject, body

    total = len(rows)
    failed = [r for r in rows if not r["success"]]
    succeeded = total - len(failed)
    rules_changed = sum(int(r["rules_changed"]) for r in rows)
    total_duration = sum(float(r["duration_seconds"]) for r in rows)

    if failed:
        subject = f"{_SUBJECT_PREFIX} Daily sync FAILED — {len(failed)}/{total} connectors"
    else:
        subject = f"{_SUBJECT_PREFIX} Daily sync OK — {total}/{total} connectors"

    summary = (
        f"Summary:\n"
        f"  Connectors run:  {total}\n"
        f"  Succeeded:       {succeeded}\n"
        f"  Failed:          {len(failed)}\n"
        f"  Rules changed:   {rules_changed}\n"
        f"  Total duration:  {_fmt_duration(total_duration)}\n"
    )

    name_width = max(len(r["connector_name"]) for r in rows)
    lines = []
    for r in rows:
        status = "OK  " if r["success"] else "FAIL"
        base = (
            f"  {status}  {r['connector_name']:<{name_width}}  "
            f"rules_changed={int(r['rules_changed']):<4}  "
            f"duration={_fmt_duration(float(r['duration_seconds']))}"
        )
        if r["success"]:
            lines.append(base)
        else:
            reason = (r["error_message"] or "no error_message recorded").strip().splitlines()[0]
            lines.append(f"{base}\n         Failed: {reason}")

    body = f"{window_line}\n\n{summary}\nPer connector:\n" + "\n".join(lines) + "\n"
    return subject, body


def _publish(topic_arn: str, region: str, subject: str, body: str) -> str:
    # SNS Subject has a hard 100-char cap.
    sns_subject = subject if len(subject) <= 100 else subject[:97] + "..."
    client = boto3.client("sns", region_name=region)
    resp = client.publish(TopicArn=topic_arn, Subject=sns_subject, Message=body)
    return resp["MessageId"]


async def main() -> int:
    topic_arn = os.getenv("DIGEST_SNS_TOPIC_ARN")
    if not topic_arn:
        log.error("DIGEST_SNS_TOPIC_ARN is unset — refusing to run")
        return 2
    region = os.getenv("AWS_REGION", "us-east-1")
    window_hours = int(os.getenv("DIGEST_WINDOW_HOURS", "24"))

    await init_db()
    try:
        rows = await _fetch_rows(window_hours)
    finally:
        await close_db()

    subject, body = _build_message(rows, window_hours)
    message_id = _publish(topic_arn, region, subject, body)
    log.info("digest_published message_id=%s rows=%d subject=%r", message_id, len(rows), subject)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
