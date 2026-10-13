"""One-shot: re-normalize `ofac_sdn_vectors.name_normalized` in place.

Purpose
-------
The OFAC transliteration convention uses apostrophes and modifier letters
inside a syllable (`KIM, Ch'o'l` ≡ `KIM CHOL`). Prior to this change,
`normalize_name` replaced every non-word character with a space, which
fragmented those variants into `KIM CH O L`. Screening queries like
`kim chol` then missed those rows on both `tsv @@ plainto_tsquery`
(no `chol` token in the tsv) and `similarity(name_normalized, :q)`
(low trigram overlap on fragmented text).

This script re-computes `name_normalized` for every active row using the
updated `normalize_name`. The `BEFORE UPDATE OF name_normalized` trigger
from migration 021 refreshes `tsv` automatically; the GIN trigram and
tsv indexes are then correct without any DDL.

Duplicate handling
------------------
The unique constraint is `(sdn_number, name_normalized)`. When the new
normalization collapses a fragmented row onto a value another row
already holds (i.e. the loader wrote both `KIM CH O L` and `KIM CHOL`
as two variants of the same SDN entry), the fragmented row is deleted
rather than updated — the canonical row already exists.

Usage
-----
    PIPELINE_MODE=pipeline1 python -m scripts.setup.renormalize_sdn_vectors --dry-run
    PIPELINE_MODE=pipeline1 python -m scripts.setup.renormalize_sdn_vectors
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from sqlalchemy import text

from src.services.database import close_db, get_db, init_db
from src.services.ofac_sdn_loader import normalize_name

logger = structlog.get_logger(__name__)


async def _run(dry_run: bool) -> int:
    await init_db()
    updated = 0
    deleted = 0
    unchanged = 0
    scanned = 0

    async with get_db() as session:
        rows = (await session.execute(text("""
            SELECT id, sdn_number, name_variant, name_normalized
            FROM ofac_sdn_vectors
            WHERE is_active = TRUE
        """))).all()

        for r in rows:
            scanned += 1
            new_norm = normalize_name(r.name_variant or "")
            if not new_norm or new_norm == r.name_normalized:
                unchanged += 1
                continue

            # Would the new value collide with another row for the same SDN?
            dup = await session.scalar(text("""
                SELECT 1 FROM ofac_sdn_vectors
                WHERE sdn_number = :sdn
                  AND name_normalized = :norm
                  AND id <> :id
                LIMIT 1
            """), {"sdn": r.sdn_number, "norm": new_norm, "id": r.id})

            if dup:
                if not dry_run:
                    await session.execute(text(
                        "DELETE FROM ofac_sdn_vectors WHERE id = :id"
                    ), {"id": r.id})
                deleted += 1
            else:
                if not dry_run:
                    await session.execute(text(
                        "UPDATE ofac_sdn_vectors "
                        "SET name_normalized = :norm WHERE id = :id"
                    ), {"norm": new_norm, "id": r.id})
                updated += 1

        if not dry_run:
            await session.commit()

    logger.info(
        "renormalize_sdn_complete",
        scanned=scanned,
        updated=updated,
        deleted_as_duplicate=deleted,
        unchanged=unchanged,
        dry_run=dry_run,
    )
    await close_db()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Report counts without mutating rows.")
    args = p.parse_args()
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
