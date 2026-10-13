"""Pipeline 1 — one-shot scrape entry point.

Runs the full regulatory connector cycle once and exits. Designed to be invoked
by an EventBridge → ECS RunTask schedule (see EcsTaskDefScraperP1 in
infra/cloudformation/pipeline1-compute.yml) or manually via `aws ecs run-task`.

The long-running API container (main_pipeline1.py) does NOT trigger scrapes in
production — gate `SCRAPER_IN_PROCESS=true` if you want the legacy in-process
scheduler. EventBridge → this script is the authoritative path.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("run_scrape")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline 1 one-shot scrape")
    parser.add_argument(
        "--connector",
        action="append",
        default=None,
        help=(
            "Restrict cycle to the named connector (suffix-matched against "
            "regulatory.<name>). Repeat to allow several. Also settable via "
            "SCRAPER_ONLY_CONNECTORS env var (comma-separated)."
        ),
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    only_env = os.environ.get("SCRAPER_ONLY_CONNECTORS", "").strip()
    only = list(args.connector) if args.connector else (
        [c.strip() for c in only_env.split(",") if c.strip()] if only_env else None
    )
    if only:
        os.environ["SCRAPER_ONLY_CONNECTORS"] = ",".join(only)
        logger.info("one_shot_scrape_filter only=%s", only)

    from src.services.database import init_db, close_db
    from src.services.redis_service import init_redis, close_redis
    from src.services.scraper_scheduler import ScraperScheduler

    logger.info("one_shot_scrape_starting")
    await init_db()
    await init_redis()
    try:
        scheduler = ScraperScheduler()
        await scheduler._run_scrape_cycle()
        logger.info("one_shot_scrape_complete")
        return 0
    except Exception:
        logger.exception("one_shot_scrape_failed")
        return 1
    finally:
        await close_redis()
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
