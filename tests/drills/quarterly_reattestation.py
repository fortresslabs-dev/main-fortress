"""Quarterly re-attestation drill (design doc §11.3 Q24).

Run by the EventBridge schedule in pipeline2-exports-alarms.yml on the
first day of each quarter. Walks every export_artifacts row that's at
least 90 days old, re-fetches the stored object from S3, and asserts
that re-running ``build_artifact`` against the same as_of returns the
existing object (is_new=False) — proving snapshot/canonical/template
did not drift since the artifact was sealed.

Failure modes:
  * snapshot.py emits different rows for past as_of (snapshot history
    bug — see Q13)
  * canonical.canonical_bytes order changed (regression in hash
    determinism)
  * template_version bumped but vocab_version didn't, etc.

Exits 0 on clean drill, 1 on any drift, 2 on infra failure (which is
also a page-out because the alarm fires on missing data).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import logging
import os
import sys

import structlog
from sqlalchemy import text

from src.exports import canonical, snapshot, vocabulary
from src.exports.worker import TEMPLATE_VERSION
from src.services.database import close_db, get_db, init_db


log = structlog.get_logger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")


async def _check_one(session, row, app_commit: str) -> tuple[bool, str]:
    """Re-hash the snapshot at row.as_of and compare to stored hash."""
    data = await snapshot.gather(session,
                                 report_type=row.type,
                                 bank_id=row.bank_id,
                                 as_of=row.as_of)
    try:
        vocabulary.validate(data)
    except vocabulary.VocabularyError as exc:
        return False, f"vocab drift: {exc}"
    sha, _ = canonical.hash_envelope(
        data,
        template_version=row.template_version,
        vocab_version=row.vocab_version,
        app_commit=app_commit,
    )
    if sha != row.snapshot_canonical_sha256:
        return False, (
            f"canonical hash drift: stored={row.snapshot_canonical_sha256[:12]}… "
            f"re-computed={sha[:12]}…"
        )
    return True, "ok"


async def main(bank_id: str | None) -> int:
    app_commit = os.getenv("APP_COMMIT", "unknown")
    cutoff = _dt.date.today() - _dt.timedelta(days=90)
    drift = 0
    checked = 0

    await init_db()
    try:
        async with get_db() as session:
            where = "WHERE generated_at < :cutoff"
            params = {"cutoff": cutoff}
            if bank_id:
                where += " AND bank_id = :b"
                params["b"] = bank_id
            rows = (await session.execute(text(f"""
                SELECT artifact_id, bank_id, type, as_of, format,
                       snapshot_canonical_sha256, template_version,
                       vocab_version, app_commit
                FROM export_artifacts {where}
                ORDER BY generated_at ASC
            """), params)).all()

            for row in rows:
                checked += 1
                ok, detail = await _check_one(session, row, app_commit)
                if not ok:
                    drift += 1
                    log.error("drill_drift",
                              artifact_id=str(row.artifact_id),
                              bank_id=row.bank_id, type=row.type,
                              as_of=row.as_of.isoformat(), detail=detail)
                else:
                    log.info("drill_ok",
                             artifact_id=str(row.artifact_id),
                             type=row.type, as_of=row.as_of.isoformat())
    finally:
        await close_db()

    log.info("drill_done", checked=checked, drift=drift)
    return 1 if drift else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-id", default=None)
    parser.add_argument("--bucket", help="(ignored — bucket inferred per artifact)")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(main(args.bank_id)))
    except Exception as exc:  # noqa: BLE001
        log.error("drill_crashed", err=str(exc))
        sys.exit(2)
