"""
Scraper Scheduler — Background task that runs regulatory scrapers on a cron
schedule (default: daily at midnight) and increments the global sync version.

Runs inside Pipeline 1 (Fortress Cloud) only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from datetime import datetime, date, timezone
from typing import Optional

from src.utils.chunk_exporter import export_chunks

import structlog
import traceback
from sqlalchemy import select, update

logger = structlog.get_logger(__name__)

# Default: run at midnight UTC daily.  Override via SCRAPER_SCHEDULE env var.
_DEFAULT_INTERVAL_HOURS = int(os.getenv("SCRAPER_INTERVAL_HOURS", "24"))


class ScraperScheduler:
    """Periodically runs regulatory scraper agents and bumps the sync version."""

    _ENFORCEMENT_INTERVAL = 7 * 24 * 3600  # run enforcement scrape weekly, not daily

    def __init__(self, interval_hours: int = _DEFAULT_INTERVAL_HOURS):
        self._interval_seconds = interval_hours * 3600
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None
        self._last_enforcement_run: float = 0.0

    def start(self) -> None:
        """Start the scheduler in a background thread with its own event loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="scraper-scheduler")
        self._thread.start()
        logger.info(
            "scraper_scheduler_started",
            interval_hours=self._interval_seconds // 3600,
        )

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("scraper_scheduler_stopped")

    def _run_loop(self) -> None:
        """Internal: runs in a daemon thread with its own DB engine."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._schedule_loop())
        except Exception as exc:
            logger.error("scraper_scheduler_crashed", error=str(exc))
        finally:
            loop.close()

    @staticmethod
    def _make_local_get_db():
        """Return a get_db context manager backed by a fresh engine for this loop."""
        from contextlib import asynccontextmanager
        from sqlalchemy.ext.asyncio import (
            create_async_engine,
            AsyncSession,
            async_sessionmaker,
        )
        from src.utils.config import settings

        db_url = settings.DATABASE_URL.replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        local_engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
        local_session_maker = async_sessionmaker(
            local_engine, class_=AsyncSession, expire_on_commit=False
        )

        @asynccontextmanager
        async def _get_db():
            async with local_session_maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        return _get_db, local_engine

    async def _schedule_loop(self) -> None:
        """Async loop: sleep → scrape → bump version → repeat."""
        from src.utils.config import settings

        # Check if startup scrape is enabled (default: off)
        run_on_startup = settings.SCRAPER_RUN_ON_STARTUP
        startup_delay = settings.SCRAPER_STARTUP_DELAY  # default 300s (5 min)

        if not run_on_startup:
            logger.info(
                "scraper_startup_skipped",
                reason="SCRAPER_RUN_ON_STARTUP=false",
                next_run_in_seconds=self._interval_seconds,
            )
            await asyncio.sleep(self._interval_seconds)
        else:
            # Wait for DB and services to initialise before first scrape
            logger.info("scraper_startup_delay", delay_seconds=startup_delay)
            await asyncio.sleep(startup_delay)
        while self._running:
            # Circuit breaker: check system load before starting a scrape
            if not self._system_healthy():
                logger.warning(
                    "scrape_cycle_skipped_high_load",
                    reason="CPU or memory above safe threshold, backing off 60s",
                )
                await asyncio.sleep(60)
                continue
            try:
                await self._run_scrape_cycle()
            except Exception as exc:
                logger.error("scrape_cycle_failed", error=str(exc))
            # Sleep until next cycle
            await asyncio.sleep(self._interval_seconds)

    @staticmethod
    def _infer_regulator(rule_id: str, rule_data: dict) -> str:
        """Resolve a canonical regulator name from rule_data or rule_id prefix."""
        _MAP = {
            "OCC": "OCC", "FDIC": "FDIC", "CFPB": "CFPB",
            "FINCEN": "FinCEN", "FFIEC": "FFIEC",
            "OFAC": "OFAC", "FRB": "FRB", "FED": "FRB",
            "NCUA": "NCUA", "HUD": "HUD",
            # Connector-specific prefixes
            "CRA": "CRA",
            "IRR": "OCC",
            "CRE": "FFIEC",
            "CECL": "FFIEC",
            "LIQ": "OCC",
            "OPRISK": "FFIEC",
            "PEER": "FFIEC",
            # State regulators — identity entries so explicit values pass through
            # unchanged; bare prefixes fall back to the state's primary regulator.
            "NYDFS": "NYDFS",
            "CA-DFPI": "CA-DFPI", "CA": "CA-DFPI",
            "TX-DOB":  "TX-DOB",  "TX": "TX-DOB",
            "FL-OFR":  "FL-OFR",  "FL": "FL-OFR",
            "IL-IDFPR": "IL-IDFPR", "IL": "IL-IDFPR",
        }
        explicit = rule_data.get("regulator", "").strip().upper()
        if explicit and explicit not in ("", "UNKNOWN"):
            # Preserve uppercase canonical codes (e.g. CA-DFPI) when not in the map
            # rather than mangling them through .title() (which produced "Ca-Dfpi").
            return _MAP.get(explicit, explicit)
        parts = rule_id.upper().split("-")
        prefix = parts[0] if parts else ""
        # TC-AGENCY-nnn → use second segment; bare TC-nnn → OCC (BSA/AML content)
        if prefix == "TC":
            second = parts[1] if len(parts) >= 2 else ""
            return _MAP.get(second, "OCC")
        return _MAP.get(prefix, "unknown")

    @staticmethod
    def _parse_date(value) -> datetime | None:
        """Convert a date string (YYYY-MM-DD) or date/datetime to datetime, or return None."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _system_healthy(cpu_threshold: float = 80.0, mem_threshold: float = 85.0) -> bool:
        """Return False if CPU or memory usage exceeds safe thresholds.

        Uses psutil if available, otherwise falls back to /proc/meminfo.
        When resource info is unavailable, returns True (fail-open).
        """
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            if cpu > cpu_threshold or mem > mem_threshold:
                logger.warning(
                    "system_high_load",
                    cpu_percent=cpu,
                    mem_percent=mem,
                    cpu_threshold=cpu_threshold,
                    mem_threshold=mem_threshold,
                )
                return False
            logger.info("system_health_ok", cpu_percent=cpu, mem_percent=mem)
            return True
        except ImportError:
            pass

        # Fallback: check /proc/meminfo on Linux
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        info[parts[0].strip()] = int(parts[1].strip().split()[0])
                total = info.get("MemTotal", 1)
                available = info.get("MemAvailable", total)
                mem_pct = 100.0 * (1.0 - available / total)
                if mem_pct > mem_threshold:
                    logger.warning("system_high_memory", mem_percent=round(mem_pct, 1))
                    return False
        except Exception:
            pass

        return True

    @staticmethod
    def _log_connector_failure(
        connector_name: str,
        error: BaseException,
        phase: str,
    ) -> None:
        """Emit CloudWatch metric + S3 audit log + structured ERROR log for a failed connector.

        Mirrors the helpers in src/mcp_servers/scrape_monitor/server.py so the existing
        Fortress/Scraper/ConnectorFailure alarm fires. All side channels are non-fatal —
        a failure here must not cascade into a second failure.
        """
        now = datetime.now(timezone.utc)
        tb_text = traceback.format_exc()
        region = os.getenv("AWS_REGION", "us-east-1")

        payload = {
            "event": "connector_failure",
            "timestamp": now.isoformat() + "Z",
            "connector_name": connector_name,
            "phase": phase,  # "extract" or "db_write"
            "error_type": type(error).__name__,
            "error_message": str(error)[:1000],
            "traceback": tb_text[:4000],
        }

        try:
            import boto3
            cw = boto3.client("cloudwatch", region_name=region)
            cw.put_metric_data(
                Namespace="Fortress/Scraper",
                MetricData=[
                    {
                        "MetricName": "ConnectorFailure",
                        "Dimensions": [{"Name": "Connector", "Value": connector_name}],
                        "Value": 1,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                    {
                        "MetricName": "ConnectorFailure",
                        "Value": 1,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                ],
            )
        except Exception as cw_exc:
            logger.warning(
                "connector_failure_metric_emit_failed",
                connector=connector_name,
                error=str(cw_exc),
            )

        bucket = os.getenv("S3_BUCKET")
        if bucket:
            try:
                import boto3
                s3 = boto3.client("s3", region_name=region)
                ts = now.strftime("%Y%m%d_%H%M%S")
                key = (
                    f"scrape-logs/{now.strftime('%Y/%m/%d')}/"
                    f"FAILURE_{connector_name.replace('.', '_')}_{ts}.json"
                )
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=json.dumps(payload, default=str).encode(),
                    ContentType="application/json",
                    ServerSideEncryption="aws:kms",
                )
            except Exception as s3_exc:
                logger.warning(
                    "connector_failure_s3_write_failed",
                    connector=connector_name,
                    error=str(s3_exc),
                )

        logger.error(
            "connector_failed",
            connector=connector_name,
            phase=phase,
            error_type=type(error).__name__,
            error=str(error)[:500],
        )

    @staticmethod
    def _log_connector_success(connector_name: str, upserted: int) -> None:
        """Emit Fortress/Scraper/ConnectorSuccess so the no-run alarm has a heartbeat.

        Called after a connector's DB commit succeeds. Emits TWO data points:
        one with the Connector dimension (per-connector visibility) and one
        with no dimensions (aggregate heartbeat). The no-run alarm watches
        the dimensionless series; CloudWatch treats dimensioned and
        undimensioned MetricNames as separate series, so emitting both is
        required for the alarm to clear.
        """
        try:
            import boto3
            now = datetime.now(timezone.utc)
            cw = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "us-east-1"))
            cw.put_metric_data(
                Namespace="Fortress/Scraper",
                MetricData=[
                    {
                        "MetricName": "ConnectorSuccess",
                        "Dimensions": [{"Name": "Connector", "Value": connector_name}],
                        "Value": 1,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                    {
                        "MetricName": "ConnectorSuccess",
                        "Value": 1,
                        "Unit": "Count",
                        "Timestamp": now,
                    },
                ],
            )
        except Exception as cw_exc:
            logger.warning(
                "connector_success_metric_emit_failed",
                connector=connector_name,
                error=str(cw_exc),
            )

    async def _run_scrape_cycle(self) -> None:
        """Run all regulatory connectors and increment the sync version.

        Writes rules to DB after EACH connector completes so data is persisted
        progressively — even if the container is killed mid-cycle.
        """
        from src.connectors.manager import PluginManager
        from src.models.database import ComplianceRule, SyncVersion, DeletedRule, OFACSDNEntry
        from src.utils.config import settings

        logger.info("scrape_cycle_starting")

        manager = PluginManager(geography="usa")
        manager.load_connectors()

        regulatory_connectors = {
            name: conn for name, conn in manager.connectors.items()
            if name.startswith("regulatory.")
        }

        # Optional filter (one-shot debug / targeted backfills). Matches by
        # suffix so `--connector ofac_sdn` selects regulatory.usa.ofac_sdn.
        only_csv = os.environ.get("SCRAPER_ONLY_CONNECTORS", "").strip()
        if only_csv:
            wanted = {c.strip() for c in only_csv.split(",") if c.strip()}
            regulatory_connectors = {
                name: conn for name, conn in regulatory_connectors.items()
                if any(name.endswith(w) or name == w for w in wanted)
            }
            logger.info("scrape_cycle_filtered",
                        only=sorted(wanted),
                        kept=sorted(regulatory_connectors.keys()))

        if not regulatory_connectors:
            logger.warning("scrape_cycle_no_rules")
            return

        connector_delay = settings.SCRAPER_CONNECTOR_DELAY

        get_db, local_engine = self._make_local_get_db()
        total_upserted = 0
        all_scraped_rule_ids: set[str] = set()
        inserted_this_cycle: set[str] = set()   # dedup across all connectors
        changed_rules_log: list[dict] = []
        all_rules_for_opensearch: dict = {}
        failed_connectors: list[dict] = []      # collected per-connector failure summary

        try:
            # ── Resolve new version + existing rule map (once, before loop) ──
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
                    select(ComplianceRule.rule_id, ComplianceRule.hash)
                    .where(ComplianceRule.is_active == True)
                )
                existing_map = {row[0]: row[1] for row in existing_result.all()}
                existing_rule_ids = set(existing_map.keys())

            # ── Per-connector extract → immediate DB write ────────────────────
            for idx, (connector_name, connector) in enumerate(regulatory_connectors.items()):
                if idx > 0 and connector_delay > 0:
                    logger.info(
                        "connector_stagger_delay",
                        next_connector=connector_name,
                        delay_seconds=connector_delay,
                    )
                    await asyncio.sleep(connector_delay)

                try:
                    rules = connector.extract_all_rules()
                except Exception as exc:
                    self._log_connector_failure(connector_name, exc, phase="extract")
                    failed_connectors.append({
                        "connector": connector_name,
                        "phase": "extract",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                    })
                    continue

                _skip_cats = {"scraped_documents", "sdn_entries"}
                logger.info(
                    "rules_extracted",
                    connector=connector_name,
                    categories=len(rules),
                    total_rules=sum(
                        len(v) for k, v in rules.items() if k not in _skip_cats
                    ),
                    sdn_entries=len(rules.get("sdn_entries", [])),
                    scraped_documents=len(rules.get("scraped_documents", [])),
                )

                # Per-connector staging — only merged into cycle-wide state on
                # successful DB commit. If the DB write fails, these are discarded
                # along with the rolled-back transaction so tombstoning and dedup
                # stay consistent with what's actually persisted.
                connector_scraped_ids: set[str] = set()
                connector_new_inserted: set[str] = set()
                connector_changes_log: list[dict] = []

                # Write this connector's rules to DB immediately. One bad row from
                # any single connector must not abort the cycle — catch DBAPI/other
                # errors, log the failure to S3 + CloudWatch + structlog, and move
                # on to the next connector.
                upserted = 0
                try:
                    async with get_db() as session:
                        # ── SDN entries → dedicated ofac_sdn_entries table ────────
                        if "sdn_entries" in rules and rules["sdn_entries"]:
                            sdn_list = rules["sdn_entries"]
                            existing_sdn = {
                                row[0] for row in (await session.execute(
                                    select(OFACSDNEntry.sdn_number)
                                )).all()
                            }
                            sdn_new = sdn_updated = 0
                            for entry in sdn_list:
                                sdn_num = str(entry.get("sdn_number", "")).strip()
                                if not sdn_num:
                                    continue
                                programs = entry.get("programs") or []
                                if isinstance(programs, str):
                                    programs = [p.strip() for p in programs.replace("][", ",").split(",") if p.strip()]
                                now = datetime.utcnow()
                                source_updated = None
                                raw_date = entry.get("source_updated_at")
                                if raw_date:
                                    try:
                                        source_updated = datetime.fromisoformat(str(raw_date)[:19])
                                    except (ValueError, TypeError):
                                        pass
                                if sdn_num not in existing_sdn:
                                    session.add(OFACSDNEntry(
                                        sdn_number=sdn_num,
                                        entity_name=entry.get("entity_name", ""),
                                        entity_type=entry.get("entity_type", "entity"),
                                        programs=programs,
                                        aliases=entry.get("aliases", []),
                                        addresses=entry.get("addresses", []),
                                        id_numbers=entry.get("id_numbers", []),
                                        remarks=entry.get("remarks", ""),
                                        country=entry.get("country", ""),
                                        nationality=entry.get("nationality", ""),
                                        is_active=True,
                                        source_updated_at=source_updated,
                                        created_at=now,
                                        updated_at=now,
                                    ))
                                    existing_sdn.add(sdn_num)
                                    sdn_new += 1
                                else:
                                    await session.execute(
                                        update(OFACSDNEntry)
                                        .where(OFACSDNEntry.sdn_number == sdn_num)
                                        .values(
                                            entity_name=entry.get("entity_name", ""),
                                            entity_type=entry.get("entity_type", "entity"),
                                            programs=programs,
                                            aliases=entry.get("aliases", []),
                                            addresses=entry.get("addresses", []),
                                            id_numbers=entry.get("id_numbers", []),
                                            remarks=entry.get("remarks", ""),
                                            country=entry.get("country", ""),
                                            nationality=entry.get("nationality", ""),
                                            source_updated_at=source_updated,
                                            updated_at=now,
                                        )
                                    )
                                    sdn_updated += 1
                            await session.flush()
                            logger.info(
                                "ofac_sdn_entries_written",
                                new=sdn_new,
                                updated=sdn_updated,
                                total=len(sdn_list),
                            )
                            upserted += sdn_new + sdn_updated

                            # ── Fan out to ofac_sdn_vectors (per-variant table)
                            # for the new screener path. Pull integrity is
                            # checked first; on failure we still upsert (additive
                            # is safe) but skip the deactivation sweep.
                            from src.services.ofac_sdn_loader import (
                                upsert_sdn_variants,
                                tombstone_missing_variants,
                                validate_pull_integrity,
                                PullIntegrityError,
                            )
                            integrity_ok = True
                            try:
                                await validate_pull_integrity(session, len(sdn_list))
                            except PullIntegrityError as exc:
                                integrity_ok = False
                                logger.error(
                                    "ofac_pull_integrity_failed",
                                    error=str(exc),
                                    action="upsert_only_no_tombstone",
                                )
                            _, seen_today = await upsert_sdn_variants(session, sdn_list)
                            if integrity_ok:
                                await tombstone_missing_variants(session, seen_today)

                            # Record the OFAC source-freshness anchor (one row
                            # per (list_name, version_id)) so the P2 ofac_-
                            # attestation export can show "published_at +
                            # SHA-256 of XML + ingestion lag". The connector
                            # stashes the raw bytes on itself during the fetch
                            # so we don't have to re-download here. Idempotent
                            # via ON CONFLICT (list_name, version_id).
                            xml_bytes = getattr(connector, "_last_xml_bytes", None)
                            if xml_bytes:
                                from src.services.ofac_sdn_loader import (
                                    record_list_version,
                                )
                                try:
                                    await record_list_version(
                                        session,
                                        list_name="sdn",
                                        xml_bytes=xml_bytes,
                                        source_url=getattr(
                                            connector,
                                            "_last_xml_source_url", "",
                                        ),
                                        last_modified_header=getattr(
                                            connector,
                                            "_last_xml_last_modified", None,
                                        ),
                                        scrape_run_id=None,
                                    )
                                except Exception as exc:
                                    # Freshness anchor is best-effort — must
                                    # not block the rest of the SDN write.
                                    logger.warning(
                                        "ofac_list_version_record_failed",
                                        error=str(exc),
                                    )

                        for _category, rule_list in rules.items():
                            if _category in {"scraped_documents", "sdn_entries"}:
                                continue
                            for rule_data in rule_list:
                                rule_id = (
                                    rule_data.get("rule_id")
                                    or rule_data.get("requirement_id")
                                    or rule_data.get("kpi_id")
                                    or rule_data.get("scenario_id")
                                    or rule_data.get("regulation_id")
                                    or rule_data.get("rule", "")
                                )
                                if not rule_id:
                                    continue

                                rule_text = (
                                    rule_data.get("rule_text")
                                    or rule_data.get("requirement_text")
                                    or rule_data.get("regulation_name")
                                    or rule_data.get("content")
                                    or ""
                                )
                                if not rule_text and _category == "kpis":
                                    name = rule_data.get("kpi_name", "")
                                    formula = rule_data.get("kpi_formula", "")
                                    rule_text = f"{name}: {formula}" if formula else name
                                if not rule_text and _category == "test_scenarios":
                                    parts = [
                                        rule_data.get("scenario_name", ""),
                                        rule_data.get("given", ""),
                                        rule_data.get("when_step", ""),
                                        rule_data.get("then_step", ""),
                                    ]
                                    rule_text = " | ".join(p for p in parts if p)
                                if not rule_text:
                                    continue

                                connector_scraped_ids.add(rule_id)

                                hash_data = {
                                    k: v for k, v in rule_data.items()
                                    if k not in {"last_updated", "scraped_at", "updated_at", "fetched_at"}
                                }
                                rule_hash = hashlib.sha256(
                                    json.dumps(hash_data, sort_keys=True, default=str).encode()
                                ).hexdigest()

                                if rule_id in existing_map:
                                    if existing_map[rule_id] != rule_hash:
                                        connector_changes_log.append({
                                            "rule_id": rule_id,
                                            "regulator": ScraperScheduler._infer_regulator(rule_id, rule_data),
                                            "old_text": None,
                                            "new_text": rule_text,
                                            "old_hash": existing_map[rule_id],
                                            "new_hash": rule_hash,
                                            "is_new_rule": False,
                                        })
                                        await session.execute(
                                            update(ComplianceRule)
                                            .where(ComplianceRule.rule_id == rule_id)
                                            .values(
                                                rule_text=rule_text,
                                                hash=rule_hash,
                                                version=new_version,
                                                updated_at=datetime.utcnow(),
                                                cfr_citation=rule_data.get("cfr_citation"),
                                                state_code=rule_data.get("state_code"),
                                                jurisdiction_level=rule_data.get(
                                                    "jurisdiction_level",
                                                    "state" if rule_data.get("state_code") else "federal",
                                                ),
                                                enforcement_risk_score=rule_data.get("enforcement_risk_score"),
                                                severity=rule_data.get("severity"),
                                                change_type=rule_data.get("change_type"),
                                            )
                                        )
                                        upserted += 1
                                elif rule_id not in inserted_this_cycle and rule_id not in connector_new_inserted:
                                    connector_changes_log.append({
                                        "rule_id": rule_id,
                                        "regulator": ScraperScheduler._infer_regulator(rule_id, rule_data),
                                        "old_text": None,
                                        "new_text": rule_text,
                                        "old_hash": None,
                                        "new_hash": rule_hash,
                                        "is_new_rule": True,
                                    })
                                    connector_new_inserted.add(rule_id)
                                    session.add(ComplianceRule(
                                        rule_id=rule_id,
                                        regulator=self._infer_regulator(rule_id, rule_data),
                                        category=rule_data.get("category", _category),
                                        geography=rule_data.get("geography", "usa"),
                                        domain=rule_data.get("domain"),
                                        rule_type=rule_data.get("rule_type"),
                                        rule_text=rule_text,
                                        threshold_amount=rule_data.get("threshold_amount"),
                                        threshold_currency=rule_data.get("threshold_currency", "USD"),
                                        field_name=rule_data.get("field_name"),
                                        effective_date=self._parse_date(rule_data.get("effective_date")),
                                        source_url=rule_data.get("source_url"),
                                        test_template=rule_data.get("test_template"),
                                        is_active=True,
                                        hash=rule_hash,
                                        version=new_version,
                                        cfr_citation=rule_data.get("cfr_citation"),
                                        state_code=rule_data.get("state_code"),
                                        jurisdiction_level=rule_data.get(
                                            "jurisdiction_level",
                                            "state" if rule_data.get("state_code") else "federal",
                                        ),
                                        enforcement_risk_score=rule_data.get("enforcement_risk_score"),
                                        severity=rule_data.get("severity"),
                                    ))
                                    upserted += 1
                except Exception as exc:
                    self._log_connector_failure(connector_name, exc, phase="db_write")
                    failed_connectors.append({
                        "connector": connector_name,
                        "phase": "db_write",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:200],
                    })
                    continue

                # Commit succeeded — merge per-connector staged state into the
                # cycle-wide accumulators. OpenSearch + S3 only get this
                # connector's rules now that Aurora has accepted them.
                all_scraped_rule_ids.update(connector_scraped_ids)
                inserted_this_cycle.update(connector_new_inserted)
                changed_rules_log.extend(connector_changes_log)
                for cat, rule_list in rules.items():
                    all_rules_for_opensearch.setdefault(cat, []).extend(rule_list)

                logger.info(
                    "connector_rules_written",
                    connector=connector_name,
                    upserted=upserted,
                    total_so_far=total_upserted + upserted,
                )
                self._log_connector_success(connector_name, upserted)
                total_upserted += upserted

            # ── Tombstone deleted rules + bump global version ─────────────────
            # Skip tombstoning if any connector failed — we can't tell whether
            # a missing rule_id means "regulator removed it" or "our connector
            # crashed before extracting it". Bumping sync_version always runs
            # so downstream consumers see fresh content from the connectors
            # that did succeed.
            skip_tombstoning = bool(failed_connectors)
            async with get_db() as session:
                if skip_tombstoning:
                    deleted_ids: set[str] = set()
                    logger.warning(
                        "tombstoning_skipped_partial_cycle",
                        failed_connectors=[f["connector"] for f in failed_connectors],
                        reason="cannot distinguish removed rules from extraction failures",
                    )
                else:
                    deleted_ids = existing_rule_ids - all_scraped_rule_ids
                for del_id in deleted_ids:
                    await session.execute(
                        update(ComplianceRule)
                        .where(ComplianceRule.rule_id == del_id)
                        .values(is_active=False, version=new_version)
                    )
                    session.add(DeletedRule(rule_id=del_id, deleted_at_version=new_version))

                await session.execute(
                    update(SyncVersion)
                    .where(SyncVersion.id == 1)
                    .values(current_version=new_version, updated_at=datetime.utcnow())
                )

        finally:
            await local_engine.dispose()

        all_rules = all_rules_for_opensearch

        # Post-write phases. Aurora already has this cycle's rules committed;
        # treat each downstream step as best-effort so a failure here can't
        # crash the container after a successful scrape.

        # Step A: embed rules with Bedrock Titan v2, write vectors to Aurora
        try:
            await self._embed_and_store_rules(get_db, new_version)
        except Exception as exc:
            logger.error("embedding_phase_failed", error=str(exc))

        # Step B: index into OpenSearch Serverless (BM25 + k-NN vector).
        # Pilot deployments omit OpenSearch and rely on Aurora pgvector + tsvector;
        # skip cleanly when OPENSEARCH_ENDPOINT is absent rather than spamming warnings.
        if os.environ.get("OPENSEARCH_ENDPOINT"):
            try:
                self._sync_rules_to_opensearch(all_rules)
            except Exception as exc:
                logger.error("opensearch_indexing_failed", error=str(exc))
        else:
            logger.info("opensearch_indexing_skipped_pilot_mode")

        # Step C: export raw chunks to S3 for audit trail
        # chunk_exporter now writes S3 primary, local disk fallback
        try:
            self._export_all_chunks_to_s3(all_rules)
        except Exception as exc:
            logger.error("s3_chunk_export_failed", error=str(exc))

        # Phase 4 enrichment services (REQ-010, REQ-011, REQ-012, REQ-013)
        get_db2, local_engine2 = self._make_local_get_db()
        run_enforcement = (time.time() - self._last_enforcement_run) >= self._ENFORCEMENT_INTERVAL
        try:
            await self._run_enrichment_services(
                get_db2, changed_rules_log, run_enforcement=run_enforcement
            )
            if run_enforcement:
                self._last_enforcement_run = time.time()
        except Exception as exc:
            logger.warning("enrichment_services_failed", error=str(exc))
        finally:
            await local_engine2.dispose()

        connectors_total = len(regulatory_connectors)
        connectors_succeeded = connectors_total - len(failed_connectors)
        logger.info(
            "scrape_cycle_complete",
            new_version=new_version,
            rules_upserted=total_upserted,
            rules_deleted=len(deleted_ids),
            connectors_total=connectors_total,
            connectors_succeeded=connectors_succeeded,
            connectors_failed=len(failed_connectors),
            failed_connectors=failed_connectors,
            tombstoning_applied=not bool(failed_connectors),
        )

        # If every connector failed, surface a non-zero exit to the caller.
        # Partial success returns normally so EventBridge sees the run as OK and
        # only the connector_failure alarm fires.
        if failed_connectors and connectors_succeeded == 0:
            raise RuntimeError(
                f"All {connectors_total} connectors failed this cycle: "
                f"{[f['connector'] for f in failed_connectors]}"
            )

    @staticmethod
    async def _run_enrichment_services(
        get_db, changed_rules_log: list, run_enforcement: bool = True
    ) -> None:
        """REQ-010–013: obligation extraction, enforcement scoring, deadlines, change management.

        Each service gets its own session so a poisoned transaction in one
        cannot roll back the writes of another. The previous implementation
        shared a single session — when obligation_extractor raised, autoflush
        on the next service surfaced "transaction has been rolled back", and
        the context-manager exit discarded all four services' work.
        """
        try:
            async with get_db() as session:
                from src.services.obligation_extractor import ObligationExtractor
                extractor = ObligationExtractor()
                created, updated = await extractor.run(session)
                await extractor.compute_coverage_snapshots(session)
                logger.info("obligation_extractor_done", created=created, updated=updated)
        except Exception as exc:
            logger.warning("obligation_extractor_failed", error=str(exc))

        if run_enforcement:
            try:
                async with get_db() as session:
                    from src.services.enforcement_actions import EnforcementActionService
                    svc = EnforcementActionService()
                    inserted = await svc.run(session)
                    logger.info("enforcement_actions_done", inserted=inserted)
            except Exception as exc:
                logger.warning("enforcement_action_service_failed", error=str(exc))
        else:
            logger.info("enforcement_action_scrape_skipped", reason="weekly interval not elapsed")

        try:
            async with get_db() as session:
                from src.services.enforcement_risk_scorer import EnforcementRiskScorer
                scorer = EnforcementRiskScorer()
                scored = await scorer.run(session)
                logger.info("enforcement_risk_scorer_done", scored=scored)
        except Exception as exc:
            logger.warning("enforcement_risk_scorer_failed", error=str(exc))

        try:
            async with get_db() as session:
                from src.services.deadline_tracker import DeadlineTracker
                tracker = DeadlineTracker()
                refreshed = await tracker.run(session)
                logger.info("deadline_tracker_done", refreshed=refreshed)
        except Exception as exc:
            logger.warning("deadline_tracker_failed", error=str(exc))

        if changed_rules_log:
            try:
                async with get_db() as session:
                    from src.services.change_manager import ChangeManager
                    cm = ChangeManager()
                    records = await cm.bulk_record_changes(session, changed_rules_log)
                    logger.info("change_manager_done", records=records)
            except Exception as exc:
                logger.warning("change_manager_failed", error=str(exc))

    @staticmethod
    async def _embed_and_store_rules(get_db, sync_version: int) -> None:
        """Call Bedrock Titan Embed v2 on rules missing embeddings, store in Aurora.

        Runs after DB writes so only rules from this cycle or previously
        un-embedded rules are processed. Batches 20 rules per Bedrock call
        to stay within token limits.
        """
        import boto3, json as _json
        from sqlalchemy import text

        region = os.getenv("AWS_REGION", "us-east-1")
        bedrock = boto3.client("bedrock-runtime", region_name=region)
        TITAN_MODEL = "amazon.titan-embed-text-v2:0"

        async with get_db() as session:
            # compliance_rules has no regulation_name column; category is the
            # closest semantic match (e.g. "bsa_aml", "section_1071"). cfr_citation
            # is included when present to give Titan more context per chunk.
            result = await session.execute(text("""
                SELECT rule_id, rule_text, cfr_citation, category
                FROM compliance_rules
                WHERE embedding IS NULL
                  AND is_active = TRUE
                  AND rule_text IS NOT NULL
                LIMIT 500
            """))
            rows = result.fetchall()

        if not rows:
            logger.info("embedding_skipped_all_current")
            return

        logger.info("embedding_start", count=len(rows))
        embedded = 0

        for row in rows:
            text_to_embed = " ".join(filter(None, [
                row.rule_text or "",
                row.cfr_citation or "",
                row.category or "",
            ]))[:8000]

            try:
                resp = bedrock.invoke_model(
                    modelId=TITAN_MODEL,
                    body=_json.dumps({
                        "inputText": text_to_embed,
                        "dimensions": 1024,
                        "normalize": True,
                    }),
                )
                embedding = _json.loads(resp["body"].read())["embedding"]

                async with get_db() as session:
                    await session.execute(text("""
                        UPDATE compliance_rules
                        SET embedding = CAST(:emb AS vector), updated_at = now()
                        WHERE rule_id = :rid
                    """), {"emb": str(embedding), "rid": row.rule_id})

                embedded += 1
            except Exception as exc:
                logger.warning("embedding_failed", rule_id=row.rule_id, error=str(exc))

        logger.info("embedding_complete", embedded=embedded, total=len(rows))

    @staticmethod
    def _sync_rules_to_opensearch(all_rules: dict) -> None:
        """Index scraped rules into OpenSearch Serverless for BM25 + k-NN search.

        Reads pre-computed embeddings from Aurora and includes them in the
        OpenSearch document so the index has both BM25 text and k-NN vectors.
        """
        try:
            from src.services.opensearch_client import bulk_index_rules
            from src.utils.chunk_exporter import export_chunks
        except Exception as exc:
            logger.warning("opensearch_import_unavailable", error=str(exc))
            return

        try:
            docs = []
            skip_cats = {"scraped_documents", "sdn_entries"}
            for category, rule_list in all_rules.items():
                if category in skip_cats:
                    continue
                for rule_data in rule_list:
                    rule_text = (
                        rule_data.get("rule_text")
                        or rule_data.get("requirement_text")
                        or rule_data.get("content")
                        or ""
                    )
                    if not rule_text:
                        continue
                    docs.append({
                        "rule_id":              rule_data.get("rule_id", ""),
                        "rule_text":            rule_text[:10000],
                        "regulator":            rule_data.get("regulator", ""),
                        "regulation_name":      rule_data.get("regulation_name", ""),
                        "cfr_citation":         rule_data.get("cfr_citation", ""),
                        "jurisdiction":         rule_data.get("jurisdiction", "federal"),
                        "state_code":           rule_data.get("state_code"),
                        "doc_type":             rule_data.get("doc_type", "regulation"),
                        "is_obligation":        rule_data.get("is_obligation", False),
                        "obligation_type":      rule_data.get("obligation_type"),
                        "effective_date":       str(rule_data.get("effective_date", "")),
                        "enforcement_risk_score":rule_data.get("enforcement_risk_score", 0.0),
                        "sync_version":         rule_data.get("sync_version", 0),
                        "is_active":            rule_data.get("is_active", True),
                        # embedding populated by _embed_and_store_rules — not in rule_data yet
                        # OpenSearch will have it on next cycle; first cycle is BM25-only
                    })

            if docs:
                indexed = bulk_index_rules(docs)
                logger.info("opensearch_index_complete",
                            submitted=len(docs), indexed=indexed)
            else:
                logger.info("opensearch_index_skipped_no_docs")

        except Exception as exc:
            logger.warning("opensearch_sync_failed", error=str(exc))

    @staticmethod
    def _export_all_chunks_to_s3(all_rules: dict) -> None:
        """Export all scraped chunks to S3 for audit trail and future reprocessing.

        chunk_exporter now writes S3 primary (if S3_BUCKET env var set),
        local disk fallback. This replaces the old local-only chunk exports
        that were lost when the ECS task exited.
        """
        from src.utils.chunk_exporter import export_chunks

        skip_cats = {"sdn_entries"}  # OFAC already in Aurora — no S3 needed

        for category, rule_list in all_rules.items():
            if category in skip_cats or not rule_list:
                continue
            try:
                export_chunks(category, rule_list, label=category)
            except Exception as exc:
                logger.warning("s3_chunk_export_failed",
                               category=category, error=str(exc))
