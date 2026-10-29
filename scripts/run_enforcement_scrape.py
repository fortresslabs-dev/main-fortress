"""One-off trigger: run the enforcement scrape into the local DB (DATABASE_URL).

There is no on-demand HTTP endpoint — the scheduler runs it on an interval — so
this lets ops force a single scrape (e.g. after a code fix) without waiting.

Usage (inside the pipeline container):
    python -m scripts.run_enforcement_scrape
"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("run_enforcement_scrape")


async def _main() -> int:
    from src.services.database import init_db, close_db, get_db
    from src.services.enforcement_actions import EnforcementActionService

    await init_db()
    try:
        async with get_db() as s:
            inserted = await EnforcementActionService().run(s)
            await s.commit()
            logger.info("enforcement scrape complete inserted=%d", inserted)
    finally:
        await close_db()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(_main()))
