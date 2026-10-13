#!/usr/bin/env python3
"""
One-shot trigger for a full Pipeline 1 scrape cycle.

Runs the same logic as ScraperScheduler._run_scrape_cycle but immediately,
without the 30-second startup delay.  Populates:
  - PostgreSQL compliance_rules table
  - JSON chunk files in chunks/ directory (and S3 if S3_BUCKET is set)

OpenSearch indexing is handled by the full scraper_scheduler path, not by
this one-shot trigger.

Usage (inside container):
    python -m scripts.trigger_scrape
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import structlog

logger = structlog.get_logger(__name__)


async def run_once() -> None:
    from src.connectors.manager import PluginManager
    from src.services.database import get_db, init_db
    from src.models.database import ComplianceRule, SyncVersion, DeletedRule
    from src.utils.chunk_exporter import export_chunks
    from sqlalchemy import select, update
    from datetime import datetime

    print("=== Pipeline 1 — One-shot scrape cycle ===\n")

    await init_db()

    # ── Load connectors ────────────────────────────────────────────────
    print("Loading USA regulatory connectors...")
    manager = PluginManager(geography="usa")
    manager.load_connectors()
    all_rules = manager.get_compliance_rules()

    if not all_rules:
        print("ERROR: No rules returned by connectors. Aborting.")
        return

    categories = {k: len(v) for k, v in all_rules.items()}
    print(f"Connectors returned: {categories}\n")

    # ── PostgreSQL upsert ──────────────────────────────────────────────
    print("Writing rules to PostgreSQL...")
    async with get_db() as session:
        ver_result = await session.execute(
            select(SyncVersion).where(SyncVersion.id == 1)
        )
        sync_ver = ver_result.scalar_one_or_none()
        if not sync_ver:
            sync_ver = SyncVersion(id=1, current_version=0)
            session.add(sync_ver)
            await session.flush()

        new_version = sync_ver.current_version + 1

        existing_result = await session.execute(
            select(ComplianceRule.rule_id).where(ComplianceRule.is_active == True)
        )
        existing_rule_ids = {row[0] for row in existing_result.all()}

        scraped_rule_ids: set[str] = set()
        upserted = 0

        for category, rule_list in all_rules.items():
            for rule_data in rule_list:
                rule_id = rule_data.get("rule_id") or rule_data.get("rule", "")
                if not rule_id:
                    continue

                scraped_rule_ids.add(rule_id)
                rule_hash = hashlib.sha256(
                    json.dumps(rule_data, sort_keys=True, default=str).encode()
                ).hexdigest()

                check = await session.execute(
                    select(ComplianceRule).where(ComplianceRule.rule_id == rule_id)
                )
                existing = check.scalar_one_or_none()

                if existing:
                    if existing.hash != rule_hash:
                        existing.rule_text = rule_data.get("rule_text", existing.rule_text)
                        existing.hash = rule_hash
                        existing.version = new_version
                        existing.updated_at = datetime.utcnow()
                        upserted += 1
                else:
                    new_rule = ComplianceRule(
                        rule_id=rule_id,
                        regulator=rule_data.get("regulator", "unknown"),
                        category=rule_data.get("category", category),
                        geography=rule_data.get("geography", "usa"),
                        domain=rule_data.get("domain"),
                        rule_type=rule_data.get("rule_type"),
                        rule_text=rule_data.get("rule_text", ""),
                        threshold_amount=rule_data.get("threshold_amount"),
                        threshold_currency=rule_data.get("threshold_currency", "USD"),
                        field_name=rule_data.get("field_name"),
                        effective_date=rule_data.get("effective_date"),
                        source_url=rule_data.get("source_url"),
                        test_template=rule_data.get("test_template"),
                        is_active=True,
                        hash=rule_hash,
                        version=new_version,
                    )
                    session.add(new_rule)
                    upserted += 1

        deleted_ids = existing_rule_ids - scraped_rule_ids
        for del_id in deleted_ids:
            await session.execute(
                update(ComplianceRule)
                .where(ComplianceRule.rule_id == del_id)
                .values(is_active=False, version=new_version)
            )
            session.add(DeletedRule(rule_id=del_id, deleted_at_version=new_version))

        sync_ver.current_version = new_version
        sync_ver.updated_at = datetime.utcnow()
        await session.commit()

    print(f"  ✓ Postgres: version={new_version}, upserted={upserted}, deleted={len(deleted_ids)}\n")

    # ── JSON chunk export ─────────────────────────────────────────────
    print("Exporting chunks to JSON / S3...")
    for category, rule_list in all_rules.items():
        export_chunks(rule_list, category=category)

    print("\n=== Scrape cycle complete ===")


if __name__ == "__main__":
    asyncio.run(run_once())
