"""One-shot: populate ofac_sdn_vectors from existing ofac_sdn_entries.

Use when the variant table is empty but entries are present (e.g. after a
fresh migration 021/022 deploy on a DB that was already synced). After the
next /sync/ofac-sdn cycle, update_service auto-runs the same loader so this
script is not needed regularly.

ECS RunTask:

  aws ecs run-task --cluster fortress-pipeline2-<bank> \\
    --task-definition fortress-pipeline2-<bank> \\
    --launch-type FARGATE --network-configuration ... \\
    --overrides '{"containerOverrides":[{"name":"pipeline2",
        "command":["python","-m","scripts.backfill_ofac_sdn_vectors"]}]}'
"""
from __future__ import annotations

import asyncio

from src.services.database import get_db
from src.services.ofac_sdn_loader import populate_variants


async def main() -> None:
    async with get_db() as session:
        stats = await populate_variants(session)
        await session.commit()
    print("done:", stats)


if __name__ == "__main__":
    asyncio.run(main())
