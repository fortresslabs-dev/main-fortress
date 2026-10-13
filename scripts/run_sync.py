"""Pipeline 2 — one-shot sync entry point.

Runs `UpdateService.sync_all()` once and exits. Designed to be invoked by an
EventBridge → ECS RunTask schedule (see EcsTaskDefSyncP2 + SyncSchedule in
infra/cloudformation/pipeline2-compute.yml) or manually via `aws ecs run-task`.

The long-running API container (main_pipeline2.py) still imports
UpdateService, but its background loop is suppressed when
SYNC_INTERVAL_HOURS=0 — EventBridge → this script is the authoritative path
in production, so we don't double-sync.
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("run_sync")


async def _main() -> int:
    from src.services.database import init_db, close_db
    from src.services.redis_service import init_redis, close_redis
    from src.services.update_service import UpdateService

    logger.info("one_shot_sync_starting")
    await init_db()
    await init_redis()
    try:
        result = await UpdateService().sync_all()
        status = (result or {}).get("status", "UNKNOWN")
        logger.info("one_shot_sync_complete status=%s", status)
        # Anything other than the happy-path "SUCCESS" / "PARTIAL" should
        # surface as a non-zero exit so the EventBridge retry policy fires
        # and the rolloutState reflects the failure.
        return 0 if status in ("SUCCESS", "PARTIAL") else 1
    except Exception:
        logger.exception("one_shot_sync_failed")
        return 1
    finally:
        await close_redis()
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
