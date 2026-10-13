"""One-off backfill: populate 046/047 authority + provenance columns on
existing compliance_rules rows from the connectors' static rule dicts.

Why this exists
---------------
Migrations 046/047 added ``authority_type``, ``authority_source``,
``citation_verified*``, ``applicability_*``, and the provenance fields.
The scheduler's UPDATE/INSERT branches in
``ScraperScheduler._run_scrape_cycle`` do not yet carry these through,
so pre-047 rules on populated environments have every new column at
NULL/default. Every LAW rule then silently degrades to advisory at the
``authority_gate.may_assert_as_law`` check.

This script re-reads the same static rule dicts each connector already
returns and UPDATEs matching rows in place. It never inserts, never
tombstones — the scheduler owns inventory management.

Safety
------
* No external agency traffic — each connector's ``_scrape_live`` (and any
  ``_scrape_*`` sibling) is monkey-patched to return [] before extraction.
* Dry-run by default. Pass ``--apply`` to commit.
* Match key is (rule_id, regulator, geography) reproducing
  ``ScraperScheduler._infer_regulator`` verbatim so no rule gets
  mis-matched.
* ``citation_verified`` is left untouched. Only the verification pass may
  flip it; a scraper backfill must not lie about verification.

Usage (inside pipeline1 container / one-shot ECS run-task)::

    python -m scripts.setup.backfill_authority_type            # dry-run
    python -m scripts.setup.backfill_authority_type --apply    # commit
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.connectors.manager import PluginManager
from src.models.database import ComplianceRule
from src.services.scraper_scheduler import ScraperScheduler
from src.utils.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("backfill_authority_type")

# Columns this backfill is allowed to write. Keep in sync with
# migrations 046 / 047 — every value here must exist as a column on
# compliance_rules.
_BACKFILL_COLUMNS = (
    "authority_type",
    "authority_source",
    "citation_verified_source",
    "source_content_hash",
    "publication_date",
    "comment_deadline",
    "federal_register_doc",
    "rin",
    "applicability_asset_min",
    "applicability_asset_max",
    "applicability_charter",
    "applicability_activities",
    "applicability_states",
    "is_exemption",
    "verify_before_display",
    "bank_configurable_threshold",
)

# Rule-id fallback chain, mirroring ScraperScheduler._run_scrape_cycle.
_ID_KEYS = ("rule_id", "requirement_id", "kpi_id", "scenario_id", "regulation_id", "rule")

# Categories the scheduler skips during rule persistence.
_SKIP_CATEGORIES = {"scraped_documents", "sdn_entries"}

# Connectors whose extract_all_rules() unconditionally pulls a large external
# artifact before returning any static rule dicts. The ofac_sdn connector
# downloads + parses SDN_ADVANCED.XML (~120 MB) inside connect()/extract, and
# its only 046/047-relevant output lives in the sdn_entries category which
# _SKIP_CATEGORIES drops anyway — so the pull is pure waste (and a Treasury
# HTTP hit that _neuter_live_scrape can't intercept).
_SKIP_CONNECTORS = {"regulatory.ofac_sdn"}


def _neuter_live_scrape(connector: Any) -> None:
    """Replace every ``_scrape_*`` method on the connector with a no-op.

    The scheduler tolerates live-scrape failures (they only feed the
    ``scraped_documents`` bucket which this backfill skips), but even a
    silent success emits external HTTP. A dry-safe backfill must not
    talk to any agency endpoint.
    """
    for attr in list(vars(type(connector))) + list(vars(connector)):
        if attr.startswith("_scrape") and callable(getattr(connector, attr, None)):
            setattr(connector, attr, lambda *a, **kw: [])


def _extract_rule_key(rule_data: dict) -> tuple[str | None, str, str]:
    """Return (rule_id, regulator, geography) matching the scheduler."""
    rule_id = None
    for k in _ID_KEYS:
        if rule_data.get(k):
            rule_id = str(rule_data[k])
            break
    if not rule_id:
        return None, "", ""
    regulator = ScraperScheduler._infer_regulator(rule_id, rule_data)
    geography = rule_data.get("geography", "usa")
    return rule_id, regulator, geography


def _collect_rule_updates() -> dict[tuple[str, str, str], dict]:
    """Return { (rule_id, regulator, geography): values_dict } from every
    enabled regulatory connector, static path only."""
    manager = PluginManager(geography="usa")
    manager.load_connectors()

    regulatory = {
        name: conn
        for name, conn in manager.connectors.items()
        if name.startswith("regulatory.") and name not in _SKIP_CONNECTORS
    }
    skipped = sorted(_SKIP_CONNECTORS & set(manager.connectors))
    log.info(
        "connectors_loaded count=%d names=%s skipped=%s",
        len(regulatory),
        sorted(regulatory),
        skipped,
    )

    out: dict[tuple[str, str, str], dict] = {}
    for name, connector in regulatory.items():
        _neuter_live_scrape(connector)
        try:
            connector.connect()
            rules = connector.extract_all_rules()
        except Exception as exc:
            log.error("connector_extract_failed connector=%s error=%s", name, exc)
            continue

        n_seen = 0
        for category, rule_list in rules.items():
            if category in _SKIP_CATEGORIES:
                continue
            for rule_data in rule_list:
                if not isinstance(rule_data, dict):
                    continue
                rule_id, regulator, geography = _extract_rule_key(rule_data)
                if not rule_id:
                    continue
                values = {
                    col: rule_data[col]
                    for col in _BACKFILL_COLUMNS
                    if col in rule_data
                }
                if not values:
                    continue
                key = (rule_id, regulator, geography)
                # Later categories should not overwrite earlier ones for the
                # same rule — the regulations bucket is canonical.
                out.setdefault(key, values)
                n_seen += 1

        log.info("connector_scanned connector=%s rules_seen=%d", name, n_seen)

    return out


async def _apply_updates(
    session: AsyncSession,
    updates: dict[tuple[str, str, str], dict],
    apply: bool,
) -> dict[str, int]:
    stats = {
        "connector_keys": len(updates),
        "matched": 0,
        "updated": 0,
        "unchanged": 0,
        "unmatched_in_db": 0,
    }

    for (rule_id, regulator, geography), values in updates.items():
        row = (
            await session.execute(
                select(ComplianceRule).where(
                    ComplianceRule.rule_id == rule_id,
                    ComplianceRule.regulator == regulator,
                    ComplianceRule.geography == geography,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            stats["unmatched_in_db"] += 1
            log.debug("no_db_row rule_id=%s regulator=%s", rule_id, regulator)
            continue

        stats["matched"] += 1
        diff = {k: v for k, v in values.items() if getattr(row, k, None) != v}
        if not diff:
            stats["unchanged"] += 1
            continue

        stats["updated"] += 1
        if apply:
            await session.execute(
                update(ComplianceRule)
                .where(
                    ComplianceRule.rule_id == rule_id,
                    ComplianceRule.regulator == regulator,
                    ComplianceRule.geography == geography,
                )
                .values(**diff)
            )
        log.info(
            "row_%s rule_id=%s regulator=%s diff_keys=%s",
            "updated" if apply else "would_update",
            rule_id,
            regulator,
            sorted(diff),
        )

    if apply:
        await session.commit()
    return stats


async def main(apply: bool) -> int:
    updates = _collect_rule_updates()
    log.info("connector_keys_collected count=%d", len(updates))
    if not updates:
        log.error("no_connector_rules_extracted — refusing to proceed")
        return 2

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with Session() as session:
            stats = await _apply_updates(session, updates, apply=apply)
    finally:
        await engine.dispose()

    mode = "APPLY" if apply else "DRY-RUN"
    log.info(
        "backfill_summary mode=%s connector_keys=%d matched=%d updated=%d "
        "unchanged=%d unmatched_in_db=%d",
        mode,
        stats["connector_keys"],
        stats["matched"],
        stats["updated"],
        stats["unchanged"],
        stats["unmatched_in_db"],
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit updates. Without this flag the script runs a dry-run.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
