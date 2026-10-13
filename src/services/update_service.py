"""
Update Service — Syncs compliance data from Fortress Cloud to local replica.

Runs inside Pipeline 2 (Customer Docker / VPC).
Pulls incremental diffs daily from Fortress API and applies them to the
local PostgreSQL (pgvector) database.

Zero customer data is sent to Fortress — only sync metadata (customer_id, version).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import structlog
from sqlalchemy import text as sqla_text

logger = structlog.get_logger(__name__)

# Configurable sync schedule (hours between syncs, default 24 = daily)
_SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "24"))

# Session-scoped advisory lock key. Derived from a stable string so it's
# the same value across every P2 replica connected to the same DB.
_SYNC_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"fortress_p2_sync_all").digest()[:8],
    "big",
    signed=True,
)


@asynccontextmanager
async def _sync_lock():
    """Acquire a Postgres session-scoped advisory lock for the whole sync.

    Yields True if acquired, False if another replica already holds it.
    The lock auto-releases if the holding connection drops (crash-safe).

    Held on a dedicated session so the worker sessions used inside the
    sync (one-per-operation under NullPool) don't interfere.
    """
    from src.services.database import async_session_maker

    session = async_session_maker()
    acquired = False
    try:
        got = (await session.execute(
            sqla_text("SELECT pg_try_advisory_lock(:k)"),
            {"k": _SYNC_LOCK_KEY},
        )).scalar()
        acquired = bool(got)
        yield acquired
    finally:
        if acquired:
            try:
                await session.execute(
                    sqla_text("SELECT pg_advisory_unlock(:k)"),
                    {"k": _SYNC_LOCK_KEY},
                )
                await session.commit()
            except Exception as exc:
                logger.warning("sync_lock_unlock_failed", error=str(exc))
        await session.close()

# ComplianceRule date-typed columns that arrive as ISO strings in P1 sync payloads.
# citation_verified_at / scraped_at land as ISO-8601 with tz; publication_date /
# comment_deadline land as YYYY-MM-DD (Date columns in the model).
_RULE_DATE_FIELDS = (
    "effective_date",
    "created_at",
    "updated_at",
    "embed_updated_at",
    "citation_verified_at",
    "scraped_at",
    "publication_date",
    "comment_deadline",
)


def _coerce_datetime(val: Any) -> Any:
    if not isinstance(val, str):
        return val
    s = val.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return val


def _normalise_rule_payload(rule_data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce ISO-string date fields from P1 JSON back to datetime objects
    and uppercase the regulator string so casing dupes can't slip in."""
    cleaned = dict(rule_data)
    for field in _RULE_DATE_FIELDS:
        if field in cleaned and cleaned[field] is not None:
            cleaned[field] = _coerce_datetime(cleaned[field])
    reg = cleaned.get("regulator")
    if isinstance(reg, str) and reg:
        cleaned["regulator"] = reg.upper()
    return cleaned


class UpdateService:
    """
    Orchestrates daily data sync from Fortress Cloud → local replica.

    On first boot (version 0): runs initial full sync.
    Thereafter: incremental diffs only (~2 MB/day typical).
    """

    def __init__(self):
        self._fortress_url = os.getenv(
            "FORTRESS_API_URL", "https://api.fortresslabs.dev"
        ).rstrip("/")
        self._api_key = os.getenv("FORTRESS_API_KEY", "")
        self._customer_id = os.getenv("CUSTOMER_ID", "")
        self._interval_seconds = _SYNC_INTERVAL_HOURS * 3600
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_sync_status: Dict[str, Any] = {"status": "never_synced"}

    @property
    def last_sync_status(self) -> Dict[str, Any]:
        return self._last_sync_status

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        """Start the sync scheduler in a daemon thread.

        SYNC_INTERVAL_HOURS=0 disables the in-process daemon — production
        runs the sync from an EventBridge-fired one-shot ECS task instead
        (scripts/run_sync.py), so we don't want a duplicate loop racing the
        cron. The API still imports UpdateService for its `last_sync_status`
        property and on-demand sync_all() calls; only the timer is gated.
        """
        if self._running:
            return
        if _SYNC_INTERVAL_HOURS == 0:
            logger.info(
                "update_service_loop_disabled",
                customer_id=self._customer_id,
                reason="SYNC_INTERVAL_HOURS=0 — cron-driven",
            )
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="update-service"
        )
        self._thread.start()
        logger.info(
            "update_service_started",
            fortress_url=self._fortress_url,
            customer_id=self._customer_id,
            interval_hours=_SYNC_INTERVAL_HOURS,
        )

    def stop(self) -> None:
        self._running = False
        logger.info("update_service_stopped")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._schedule_loop())
        except Exception as exc:
            logger.error("update_service_crashed", error=str(exc))
        finally:
            loop.close()

    async def _schedule_loop(self) -> None:
        # Wait for app startup
        await asyncio.sleep(15)
        while self._running:
            try:
                await self.sync_all()
            except Exception as exc:
                logger.error("sync_all_failed", error=str(exc))
                self._last_sync_status = {
                    "status": "FAILED",
                    "error": str(exc),
                    "timestamp": datetime.utcnow().isoformat(),
                }
            await asyncio.sleep(self._interval_seconds)

    # ── Public sync methods ─────────────────────────────────────────

    async def sync_all(self) -> Dict[str, Any]:
        """Run full sync cycle: license check → postgres → pgvector analyze → telemetry."""
        logger.info("sync_all_starting", customer_id=self._customer_id)

        # 1. Verify license
        if not await self._verify_license():
            logger.error("license_verification_failed")
            return {"status": "LICENSE_FAILED"}

        # 2. Get local version
        local_version = await self._get_local_version()

        # 3. Sync based on version
        if local_version == 0:
            result = await self._initial_sync()
        else:
            result = await self._incremental_sync(local_version)

        # 4. Report telemetry
        await self._report_telemetry(result)

        self._last_sync_status = result
        return result

    # ── Internal sync logic ─────────────────────────────────────────

    async def _get_local_version(self) -> int:
        """Get the latest sync version from local DB."""
        from src.services.database import get_db
        from src.models.database import LocalSyncStatus
        from sqlalchemy import select, func

        try:
            async with get_db() as session:
                result = await session.execute(
                    select(func.max(LocalSyncStatus.sync_version))
                )
                version = result.scalar()
                return version or 0
        except Exception:
            return 0

    async def _initial_sync(self) -> Dict[str, Any]:
        """Full dump sync. Skipped if another replica is already syncing."""
        async with _sync_lock() as locked:
            if not locked:
                logger.info("initial_sync_skipped_lock_held")
                return {"status": "SKIPPED_LOCK_HELD", "sync_type": "initial"}
            return await self._initial_sync_body()

    async def _initial_sync_body(self) -> Dict[str, Any]:
        """Full dump sync for first-time setup."""
        logger.info("initial_sync_starting")

        total_added = 0
        page = 1
        current_version = 0
        p1_rule_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=120.0) as client:
            while True:
                resp = await client.post(
                    f"{self._fortress_url}/api/sync/initial",
                    params={"page": page, "page_size": 5000},
                    json={
                        "customer_id": self._customer_id,
                        "api_key": self._api_key,
                        "from_version": 0,
                    },
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()

                current_version = data["current_version"]
                rules = data["rules"]

                # Insert rules into local DB
                await self._upsert_rules(rules)
                total_added += len(rules)
                p1_rule_ids.update(r["rule_id"] for r in rules if r.get("rule_id"))

                logger.info(
                    "initial_sync_page",
                    page=page,
                    rules_in_page=len(rules),
                    total_so_far=total_added,
                )

                if not data["has_more"]:
                    break
                page += 1

        # Deactivate P2 rows whose rule_id isn't in P1's set — covers seed_demo_p2.py
        # orphans and any historical local row that P1 never owned. Only runs on
        # initial syncs (force_initial=true) since incremental syncs don't see the
        # full P1 set. is_active=FALSE rather than DELETE preserves audit history.
        orphans_deactivated = await self._reconcile_orphans(p1_rule_ids)

        # Pull embeddings (separate endpoint — /sync/initial omits the column)
        try:
            embedded = await self._sync_embeddings(from_version=0)
        except Exception as exc:
            embedded = 0
            logger.error("sync_embeddings_failed", error=str(exc))

        # Pull full OFAC SDN list — required by OFACScreener
        try:
            sdn = await self._sync_ofac_sdn()
        except Exception as exc:
            sdn = 0
            logger.error("sync_ofac_sdn_failed", error=str(exc))

        # Pull enrichment tables (none of these are part of the rule diff)
        ancillary = await self._sync_ancillary_tables(from_version=0)

        # Record sync status
        await self._record_sync_status(
            version=current_version,
            added=total_added,
            updated=0,
            deleted=0,
            sync_type="initial",
        )

        # Refresh pgvector index
        await self._refresh_vector_index(current_version)

        result = {
            "status": "SUCCESS",
            "sync_type": "initial",
            "version": current_version,
            "rules_added": total_added,
            "rules_updated": 0,
            "rules_deleted": 0,
            "rules_orphaned": orphans_deactivated,
            "rules_embedded": embedded,
            "ofac_sdn_entries": sdn,
            **ancillary,
            "timestamp": datetime.utcnow().isoformat(),
        }
        logger.info("initial_sync_complete", **result)
        return result

    async def _incremental_sync(self, from_version: int) -> Dict[str, Any]:
        """Incremental diff sync. Skipped if another replica is already syncing."""
        async with _sync_lock() as locked:
            if not locked:
                logger.info("incremental_sync_skipped_lock_held", from_version=from_version)
                return {"status": "SKIPPED_LOCK_HELD", "sync_type": "incremental"}
            return await self._incremental_sync_body(from_version)

    async def _incremental_sync_body(self, from_version: int) -> Dict[str, Any]:
        """Incremental diff sync — pulls only changes since from_version."""
        logger.info("incremental_sync_starting", from_version=from_version)

        async with httpx.AsyncClient(timeout=60.0) as client:
            # /sync/postgres can flake on transient P1 5xx; retry rather than
            # let the daemon thread crash. If it never recovers, the caller
            # logs SYNC_FAILED and re-schedules at the normal interval.
            pg_data = await self._post_with_retry(
                client,
                f"{self._fortress_url}/api/sync/postgres",
                json={
                    "customer_id": self._customer_id,
                    "api_key": self._api_key,
                    "from_version": from_version,
                },
            )

        current_version = pg_data["current_version"]
        added_rules = pg_data["added"]
        updated_rules = pg_data["updated"]
        deleted_ids = pg_data["deleted"]

        # Apply changes atomically — add+update+delete+ANALYZE land together
        # so a mid-sync exception can't leave the rules table half-applied.
        from src.services.database import async_session_maker
        try:
            async with async_session_maker() as txn:
                async with txn.begin():
                    if added_rules:
                        await self._upsert_rules(added_rules, session=txn)
                    if updated_rules:
                        await self._upsert_rules(updated_rules, session=txn)
                    if deleted_ids:
                        await self._delete_rules(deleted_ids, session=txn)
                    await txn.execute(sqla_text("ANALYZE compliance_rules"))
        except Exception as exc:
            logger.error("incremental_sync_atomic_apply_failed",
                         error=str(exc),
                         added=len(added_rules), updated=len(updated_rules),
                         deleted=len(deleted_ids))
            raise

        # Pull embedding updates for any rule that moved since from_version
        try:
            await self._sync_embeddings(from_version=from_version)
        except Exception as exc:
            logger.error("sync_embeddings_failed", error=str(exc))

        # Refresh OFAC SDN list (full overwrite — endpoint isn't version-scoped)
        try:
            await self._sync_ofac_sdn()
        except Exception as exc:
            logger.error("sync_ofac_sdn_failed", error=str(exc))

        # Refresh enrichment tables (obligations / actions / deadlines / change-records / risk / benchmarks)
        ancillary = await self._sync_ancillary_tables(from_version=from_version)

        # Record sync status
        await self._record_sync_status(
            version=current_version,
            added=len(added_rules),
            updated=len(updated_rules),
            deleted=len(deleted_ids),
            sync_type="incremental",
        )

        # Refresh pgvector index
        await self._refresh_vector_index(current_version)

        result = {
            "status": "SUCCESS",
            "sync_type": "incremental",
            "version": current_version,
            "rules_added": len(added_rules),
            "rules_updated": len(updated_rules),
            "rules_deleted": len(deleted_ids),
            **ancillary,
            "timestamp": datetime.utcnow().isoformat(),
        }
        logger.info("incremental_sync_complete", **result)
        return result

    # ── Database operations ─────────────────────────────────────────

    async def _upsert_rules(
        self,
        rules: List[Dict[str, Any]],
        *,
        session: Optional[Any] = None,
    ) -> None:
        """Insert or update rules in local PostgreSQL replica.

        Lookup matches the full uq_rule_regulator_geo unique constraint
        (rule_id, regulator, geography) — keying on rule_id alone collapses
        legitimately-distinct rows (e.g. same rule_id under OCC vs CFPB)
        onto a single row and silently loses N-1 of them.

        Pass `session=` to enroll in a caller-owned transaction so atomic
        sync cycles (incremental apply) can roll back add+update+delete as
        a unit.
        """
        if session is not None:
            await self._apply_upserts(rules, session)
            return

        from src.services.database import get_db
        async with get_db() as s:
            await self._apply_upserts(rules, s)

    async def _apply_upserts(self, rules: List[Dict[str, Any]], session: Any) -> None:
        from src.models.database import ComplianceRule
        from sqlalchemy import select

        for raw_rule in rules:
            rule_id = raw_rule.get("rule_id", "")
            if not rule_id:
                continue

            rule_data = _normalise_rule_payload(raw_rule)
            regulator = rule_data.get("regulator", "unknown")
            geography = rule_data.get("geography", "usa")

            with session.no_autoflush:
                result = await session.execute(
                    select(ComplianceRule).where(
                        ComplianceRule.rule_id == rule_id,
                        ComplianceRule.regulator == regulator,
                        ComplianceRule.geography == geography,
                    )
                )
                existing = result.scalar_one_or_none()

            if existing:
                for key, val in rule_data.items():
                    if key not in ("id",) and hasattr(existing, key) and val is not None:
                        setattr(existing, key, val)
                existing.updated_at = datetime.utcnow()
            else:
                new_rule = ComplianceRule(
                    rule_id=rule_id,
                    regulator=regulator,
                    category=rule_data.get("category", ""),
                    geography=geography,
                    domain=rule_data.get("domain"),
                    rule_type=rule_data.get("rule_type"),
                    rule_text=rule_data.get("rule_text", ""),
                    threshold_amount=rule_data.get("threshold_amount"),
                    threshold_currency=rule_data.get("threshold_currency", "USD"),
                    field_name=rule_data.get("field_name"),
                    effective_date=rule_data.get("effective_date"),
                    source_url=rule_data.get("source_url"),
                    test_template=rule_data.get("test_template"),
                    is_active=rule_data.get("is_active", True),
                    hash=rule_data.get("hash"),
                    version=rule_data.get("version"),
                    severity=rule_data.get("severity"),  # migration 051 — editorial grade

                    # Enrichment (migration 013). These were dropped by the
                    # insert path — notably cfr_citation, which a LAW row MUST
                    # carry (ck_cr_law_requires_citation). Without it the first
                    # initial-sync of any LAW rule fails the whole transaction.
                    cfr_citation=rule_data.get("cfr_citation"),
                    state_code=rule_data.get("state_code"),
                    jurisdiction_level=rule_data.get("jurisdiction_level"),
                    enforcement_risk_score=rule_data.get("enforcement_risk_score"),

                    # migrations 046/047 — new rows on first sync must carry
                    # authority + provenance or may_assert_as_law() sees
                    # authority_type=NULL and every rule degrades to advisory.
                    authority_type=rule_data.get("authority_type"),
                    authority_source=rule_data.get("authority_source"),
                    citation_verified=rule_data.get("citation_verified", False),
                    citation_verified_at=rule_data.get("citation_verified_at"),
                    citation_verified_source=rule_data.get("citation_verified_source"),
                    source_content_hash=rule_data.get("source_content_hash"),
                    scraped_at=rule_data.get("scraped_at"),
                    publication_date=rule_data.get("publication_date"),
                    comment_deadline=rule_data.get("comment_deadline"),
                    federal_register_doc=rule_data.get("federal_register_doc"),
                    rin=rule_data.get("rin"),
                    applicability_asset_min=rule_data.get("applicability_asset_min"),
                    applicability_asset_max=rule_data.get("applicability_asset_max"),
                    applicability_charter=rule_data.get("applicability_charter"),
                    applicability_activities=rule_data.get("applicability_activities"),
                    applicability_states=rule_data.get("applicability_states"),
                    is_exemption=rule_data.get("is_exemption", False),
                    verify_before_display=rule_data.get("verify_before_display", False),
                    bank_configurable_threshold=rule_data.get(
                        "bank_configurable_threshold", False
                    ),
                )
                session.add(new_rule)

    async def _delete_rules(
        self,
        rule_ids: List[str],
        *,
        session: Optional[Any] = None,
    ) -> None:
        """Soft-delete rules from local replica. Honors caller-owned session."""
        from src.models.database import ComplianceRule
        from sqlalchemy import update

        stmt = (
            update(ComplianceRule)
            .where(ComplianceRule.rule_id.in_(rule_ids))
            .values(is_active=False)
        )
        if session is not None:
            await session.execute(stmt)
            return

        from src.services.database import get_db
        async with get_db() as s:
            await s.execute(stmt)

    async def _reconcile_orphans(self, p1_rule_ids: set[str]) -> int:
        """Deactivate active P2 rules with no counterpart on P1.

        Caller passes the *complete* P1 rule_id set (active + inactive) collected
        during /sync/initial pagination. Any P2 row not in that set is a local
        artifact — typically a seed_demo_p2.py leftover or a row from a prior
        schema that P1 has since dropped — and gets is_active=FALSE.
        """
        if not p1_rule_ids:
            logger.warning("reconcile_orphans_skipped_empty_p1_set")
            return 0

        from src.services.database import get_db
        from src.models.database import ComplianceRule
        from sqlalchemy import update

        async with get_db() as session:
            res = await session.execute(
                update(ComplianceRule)
                .where(ComplianceRule.is_active.is_(True))
                .where(ComplianceRule.rule_id.notin_(p1_rule_ids))
                .values(is_active=False, updated_at=datetime.utcnow())
            )
            await session.commit()
            count = res.rowcount or 0

        logger.info("reconcile_orphans_complete", deactivated=count, p1_set_size=len(p1_rule_ids))
        return count

    async def _sync_embeddings(self, from_version: int) -> int:
        """Pull pre-computed 1024-dim vectors from P1 and write them into
        compliance_rules.embedding on the local replica.

        /api/sync/initial does not carry the embedding column, so this is a
        separate pass that targets rules already upserted by _upsert_rules().
        """
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        total_updated = 0
        cursor_version = from_version

        async with httpx.AsyncClient(timeout=120.0) as client:
            while True:
                resp = await client.post(
                    f"{self._fortress_url}/api/sync/embeddings",
                    json={
                        "customer_id": self._customer_id,
                        "api_key": self._api_key,
                        "from_version": cursor_version,
                    },
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                rules = data.get("rules", [])
                if not rules:
                    break

                async with get_db() as session:
                    for r in rules:
                        rule_id = r.get("rule_id")
                        emb = r.get("embedding")
                        if not rule_id or emb is None:
                            continue
                        await session.execute(
                            sqla_text(
                                "UPDATE compliance_rules "
                                "SET embedding = CAST(:emb AS vector) "
                                "WHERE rule_id = :rid"
                            ),
                            {"emb": emb, "rid": rule_id},
                        )
                        total_updated += 1
                    await session.commit()

                page_max = max((r.get("version") or 0) for r in rules)
                if not data.get("has_more") or page_max <= cursor_version:
                    break
                cursor_version = page_max

        logger.info("sync_embeddings_complete", rules_embedded=total_updated)
        return total_updated

    async def _sync_ofac_sdn(self) -> int:
        """Full OFAC SDN refresh from P1.

        Preferred path (Phase B): pull pre-embedded variant rows from
        /api/sync/sdn-vectors directly into ofac_sdn_vectors, then record
        lifecycle deltas from /api/sync/sdn-changes, then cache
        p1_last_scraped_at from /api/sync/sdn-health.

        Fallback (older P1 that doesn't expose the new endpoints): the legacy
        /api/sync/ofac-sdn paginated dump + local populate_variants +
        local embed_pending. Triggered by HTTP 404 on /sync/sdn-vectors so
        we don't have to flip a config flag for backward compat.

        We always run the entries-table sync underneath so any caller still
        reading ofac_sdn_entries (dashboard health card, legacy code) sees
        the same data — only the variant + embedding path changed.
        """
        # Always refresh the entries table (small, used by dashboard +
        # legacy code paths).
        entries_total = await self._sync_ofac_sdn_entries()

        # Try the new variants endpoint first.
        try:
            v_total = await self._sync_sdn_vectors_paginated()
            await self._record_sdn_changes()
            await self._poll_sdn_health()
            logger.info(
                "sync_ofac_sdn_complete",
                path="p1_vectors",
                entries=entries_total,
                variants=v_total,
            )
            return entries_total
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            logger.warning(
                "sync_sdn_vectors_404_falling_back_to_legacy",
                detail="P1 does not expose /api/sync/sdn-vectors; running local derive+embed",
            )

        # Fallback path — derive variants locally + embed locally.
        await self._sync_ofac_sdn_legacy_fallback(entries_total)
        logger.info(
            "sync_ofac_sdn_complete",
            path="legacy_fallback",
            entries=entries_total,
        )
        return entries_total

    async def _sync_ofac_sdn_entries(self) -> int:
        """Paginated full refresh of ofac_sdn_entries from P1.

        Uses pg ON CONFLICT (sdn_number) upsert so re-runs are idempotent.
        """
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        upsert_sql = sqla_text("""
            INSERT INTO ofac_sdn_entries
              (sdn_number, entity_name, entity_type,
               programs, aliases, addresses, id_numbers,
               remarks, country, nationality,
               source_updated_at, is_active, created_at, updated_at)
            VALUES
              (:sdn_number, :entity_name, :entity_type,
               CAST(:programs AS jsonb), CAST(:aliases AS jsonb),
               CAST(:addresses AS jsonb), CAST(:id_numbers AS jsonb),
               :remarks, :country, :nationality,
               :source_updated_at, TRUE, NOW(), NOW())
            ON CONFLICT (sdn_number) DO UPDATE SET
              entity_name = EXCLUDED.entity_name,
              entity_type = EXCLUDED.entity_type,
              programs    = EXCLUDED.programs,
              aliases     = EXCLUDED.aliases,
              addresses   = EXCLUDED.addresses,
              id_numbers  = EXCLUDED.id_numbers,
              remarks     = EXCLUDED.remarks,
              country     = EXCLUDED.country,
              nationality = EXCLUDED.nationality,
              source_updated_at = EXCLUDED.source_updated_at,
              is_active   = TRUE,
              updated_at  = NOW()
        """)

        import json
        page = 1
        total = 0
        async with httpx.AsyncClient(timeout=180.0) as client:
            while True:
                resp = await client.post(
                    f"{self._fortress_url}/api/sync/ofac-sdn",
                    params={"page": page, "page_size": 5000},
                    json={
                        "customer_id": self._customer_id,
                        "api_key": self._api_key,
                        "from_version": 0,
                    },
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                entries = data.get("entries", [])
                if not entries:
                    break

                async with get_db() as session:
                    for e in entries:
                        await session.execute(upsert_sql, {
                            "sdn_number":  e["sdn_number"],
                            "entity_name": e["entity_name"],
                            "entity_type": e.get("entity_type"),
                            "programs":    json.dumps(e.get("programs") or []),
                            "aliases":     json.dumps(e.get("aliases") or []),
                            "addresses":   json.dumps(e.get("addresses") or []),
                            "id_numbers":  json.dumps(e.get("id_numbers") or []),
                            "remarks":     e.get("remarks"),
                            "country":     e.get("country"),
                            "nationality": e.get("nationality"),
                            "source_updated_at": _coerce_datetime(e.get("source_updated_at")),
                        })
                    await session.commit()

                total += len(entries)
                logger.info(
                    "sync_ofac_sdn_page",
                    page=page,
                    entries_in_page=len(entries),
                    total_so_far=total,
                )
                if not data.get("has_more"):
                    break
                page += 1

        logger.info("sync_ofac_sdn_entries_complete", total_entries=total)
        return total

    async def _sync_ofac_sdn_legacy_fallback(self, entries_total: int) -> None:
        """Phase B fallback — derive variants + embed locally.

        Only used when P1 doesn't expose /api/sync/sdn-vectors (older P1
        deployments). Same behavior as pre-Phase-B production path.
        """
        from src.services.database import get_db

        if entries_total > 0:
            try:
                from src.services.ofac_sdn_loader import populate_variants
                async with get_db() as session:
                    stats = await populate_variants(session)
                    await session.commit()
                logger.info("ofac_sdn_variants_refreshed_local", **stats)
            except Exception as exc:
                logger.error("ofac_sdn_variants_failed", error=str(exc))

        per_sync_cap = int(os.getenv("OFAC_EMBED_PER_SYNC", "2000"))
        if per_sync_cap > 0:
            try:
                from src.jobs.embed_sdn_variants import embed_pending
                embedded = await embed_pending(cap=per_sync_cap)
                logger.info("ofac_sdn_embeddings_healed_local", embedded=embedded, cap=per_sync_cap)
            except Exception as exc:
                logger.error("ofac_sdn_embeddings_failed", error=str(exc))

    # ── Phase B — consume P1's new SDN sync surface ──────────────────

    async def _sync_sdn_vectors_paginated(self) -> int:
        """Pull pre-embedded SDN variant rows from /api/sync/sdn-vectors and
        upsert into the local ofac_sdn_vectors table.

        Raises httpx.HTTPStatusError(404) if P1 doesn't expose the endpoint —
        caller (_sync_ofac_sdn) catches and falls back to the legacy path.
        """
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        upsert_sql = sqla_text("""
            INSERT INTO ofac_sdn_vectors (
                sdn_number, name_variant, name_normalized, is_primary,
                entity_type, programs, country, nationality,
                source_updated_at, last_seen_at, embedded_at,
                missed_pulls, is_active, embedding
            ) VALUES (
                :sdn_number, :name_variant, :name_normalized, :is_primary,
                :entity_type, CAST(:programs AS json), :country, :nationality,
                :source_updated_at, NOW(), :embedded_at,
                0, TRUE, CAST(:embedding AS vector)
            )
            ON CONFLICT (sdn_number, name_normalized) DO UPDATE SET
                name_variant      = EXCLUDED.name_variant,
                is_primary        = EXCLUDED.is_primary,
                entity_type       = EXCLUDED.entity_type,
                programs          = EXCLUDED.programs,
                country           = EXCLUDED.country,
                nationality       = EXCLUDED.nationality,
                source_updated_at = EXCLUDED.source_updated_at,
                last_seen_at      = NOW(),
                embedded_at       = COALESCE(EXCLUDED.embedded_at, ofac_sdn_vectors.embedded_at),
                missed_pulls      = 0,
                is_active         = TRUE,
                deactivated_at    = NULL,
                embedding         = COALESCE(EXCLUDED.embedding, ofac_sdn_vectors.embedding)
        """)

        import json
        # P2 derives `name_normalized` locally from `name_variant` rather than
        # trusting P1's value. `name_variant` is authoritative (raw OFAC form);
        # `name_normalized` is a local derivation used only by the P2 screener,
        # so keeping the derivation on P2 lets us evolve normalization here
        # without a coordinated P1 deploy.
        from src.services.ofac_sdn_loader import normalize_name

        page = 1
        total = 0
        async with httpx.AsyncClient(timeout=180.0) as client:
            while True:
                resp = await client.post(
                    f"{self._fortress_url}/api/sync/sdn-vectors",
                    params={"page": page, "page_size": 2000, "embedded_only": "true"},
                    json={
                        "customer_id": self._customer_id,
                        "api_key": self._api_key,
                        "from_version": 0,
                    },
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("rows", [])
                if not rows:
                    break

                async with get_db() as session:
                    for r in rows:
                        await session.execute(upsert_sql, {
                            "sdn_number":        r["sdn_number"],
                            "name_variant":      r["name_variant"],
                            "name_normalized":   normalize_name(r["name_variant"]),
                            "is_primary":        bool(r.get("is_primary")),
                            "entity_type":       r.get("entity_type"),
                            "programs":          json.dumps(r.get("programs") or []),
                            "country":           r.get("country"),
                            "nationality":       r.get("nationality"),
                            "source_updated_at": _coerce_datetime(r.get("source_updated_at")),
                            "embedded_at":       _coerce_datetime(r.get("embedded_at")),
                            "embedding":         r.get("embedding"),
                        })
                    await session.commit()

                total += len(rows)
                logger.info(
                    "sync_sdn_vectors_page",
                    page=page,
                    rows_in_page=len(rows),
                    total_so_far=total,
                )
                if not data.get("has_more"):
                    break
                page += 1

        logger.info("sync_sdn_vectors_complete", total_variants=total)
        return total

    async def _record_sdn_changes(self) -> int:
        """Pull SDN lifecycle delta from /api/sync/sdn-changes since the last
        consumed (detected_at, change_ref) cursor; write events to local
        regulatory_change_records.

        Keyset cursor on (detected_at, change_ref) — required because the
        Phase A backfill emits ~1000 events per transaction sharing the same
        detected_at; a timestamp-only cursor would loop on the batch boundary.

        ON CONFLICT (change_ref) DO NOTHING — re-runs are idempotent.
        Advances both cursors on success.
        """
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        async with get_db() as session:
            row = (await session.execute(sqla_text("""
                SELECT
                    COALESCE(last_sdn_change_consumed_at,
                             TIMESTAMP 'epoch' AT TIME ZONE 'UTC'),
                    last_sdn_change_consumed_ref
                FROM sync_metadata
                WHERE customer_id = :cid
            """), {"cid": self._customer_id})).first()
        since = row[0] if row and row[0] is not None else datetime(1970, 1, 1)
        since_ref = row[1] if row and len(row) > 1 else None

        insert_sql = sqla_text("""
            INSERT INTO regulatory_change_records (
                id, change_ref, rule_id, regulator, change_type, status,
                change_summary, semantic_diff,
                detected_at, status_updated_at, created_at, is_active
            ) VALUES (
                gen_random_uuid(), :change_ref, :rule_id, 'OFAC',
                :change_type, COALESCE(:status, 'detected'),
                :summary, :diff,
                :detected_at, NOW(), NOW(), TRUE
            )
            ON CONFLICT (change_ref) DO NOTHING
        """)

        recorded = 0
        cursor_ts = since
        cursor_ref = since_ref
        # Safety net — even with a correct keyset cursor, a misbehaving server
        # could return non-advancing pages. Cap iterations to avoid runaway.
        for _iter in range(2000):
            params = {
                "since": cursor_ts.isoformat() if hasattr(cursor_ts, "isoformat") else cursor_ts,
                "limit": 500,
            }
            if cursor_ref:
                params["since_ref"] = cursor_ref

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._fortress_url}/api/sync/sdn-changes",
                    params=params,
                    json={
                        "customer_id": self._customer_id,
                        "api_key": self._api_key,
                        "from_version": 0,
                    },
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            changes = data.get("changes", [])
            if not changes:
                break

            async with get_db() as session:
                for c in changes:
                    await session.execute(insert_sql, {
                        "change_ref":   c["change_ref"],
                        "rule_id":      c["rule_id"],
                        "change_type":  c["change_type"],
                        "status":       c.get("status"),
                        "summary":      c.get("change_summary"),
                        "diff":         c.get("semantic_diff"),
                        "detected_at":  _coerce_datetime(c.get("detected_at")),
                    })
                await session.commit()
            recorded += len(changes)

            new_ts = _coerce_datetime(data.get("next_since")) or cursor_ts
            new_ref = data.get("next_since_ref")

            # Defensive: if neither cursor moved, we're stuck — bail out so
            # we don't loop forever even if the server is buggy.
            if new_ts == cursor_ts and new_ref == cursor_ref:
                logger.warning("sync_sdn_changes_cursor_stuck",
                               cursor_ts=str(cursor_ts), cursor_ref=cursor_ref)
                break

            cursor_ts = new_ts
            cursor_ref = new_ref
            if not data.get("has_more"):
                break

        # Persist both cursors so the next sync resumes from here.
        async with get_db() as session:
            await session.execute(sqla_text("""
                UPDATE sync_metadata
                SET last_sdn_change_consumed_at  = :ts,
                    last_sdn_change_consumed_ref = :ref
                WHERE customer_id = :cid
            """), {"ts": cursor_ts, "ref": cursor_ref, "cid": self._customer_id})
            await session.commit()

        logger.info(
            "sync_sdn_changes_recorded",
            recorded=recorded,
            cursor_ts=str(cursor_ts),
            cursor_ref=cursor_ref,
        )
        return recorded

    async def _poll_sdn_health(self) -> None:
        """Fetch /api/sync/sdn-health and cache p1_last_scraped_at into
        sync_metadata so OFACScreener._enforce_freshness can read it without
        an outbound HTTP call on every screen."""
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self._fortress_url}/api/sync/sdn-health",
                json={
                    "customer_id": self._customer_id,
                    "api_key": self._api_key,
                    "from_version": 0,
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        p1_ts = _coerce_datetime(data.get("p1_last_scraped_at"))
        async with get_db() as session:
            await session.execute(sqla_text("""
                UPDATE sync_metadata
                SET p1_sdn_last_scraped_at = :ts
                WHERE customer_id = :cid
            """), {"ts": p1_ts, "cid": self._customer_id})
            await session.commit()

        logger.info(
            "sync_sdn_health_cached",
            p1_last_scraped_at=str(p1_ts),
            hours_since=data.get("hours_since_last_scrape"),
            embed_coverage_pct=data.get("embed_coverage_pct"),
        )

    # ── Ancillary table sync ────────────────────────────────────────
    #
    # P1 exposes six small endpoints that aren't part of the rule diff
    # but are read by P2 services (deadline_tracker, validators, dashboard
    # change-management panel, peer-benchmark panel).
    #
    # Most are full-refresh upserts because the corresponding /api/sync
    # endpoints don't accept a from_version filter — they return the live
    # set. Change-records IS version-scoped.

    async def _sync_ancillary_tables(self, from_version: int) -> Dict[str, int]:
        """Run all enrichment-table syncs, swallowing per-table failures."""
        counts: Dict[str, int] = {}
        for label, coro in (
            ("obligations",          self._sync_obligations()),
            ("enforcement_actions",  self._sync_enforcement_actions()),
            ("ofac_list_versions",   self._sync_ofac_list_version()),
            ("deadlines",            self._sync_deadlines()),
            ("change_records",       self._sync_change_records(from_version=from_version)),
            ("risk_scores",          self._sync_risk_scores()),
            ("benchmarks",           self._sync_benchmarks()),
        ):
            try:
                counts[label] = await coro
            except Exception as exc:
                counts[label] = 0
                logger.error(f"sync_{label}_failed", error=str(exc))
        return counts

    def _auth_headers(self) -> Dict[str, str]:
        """Headers required by P1's require_sync_auth dep (refactor c0beac5).
        Server still accepts body customer_id/api_key for back-compat, but headers
        are the only thing actually consulted for auth."""
        return {
            "X-Customer-Id": self._customer_id,
            "X-Api-Key":     self._api_key,
        }

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_attempts: int = 4,
        base_delay_seconds: float = 1.0,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST with exponential backoff on transient failures.

        Retries on network errors and 5xx/429 responses. 4xx (other than 429)
        return immediately because they will not get healthier with a retry.
        Returns the JSON body of the first 2xx response; re-raises the last
        error otherwise so the caller can mark the sync FAILED and reschedule.
        """
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._auth_headers())
        kwargs["headers"] = headers
        last_exc: Optional[BaseException] = None
        for attempt in range(max_attempts):
            try:
                resp = await client.post(url, **kwargs)
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning(
                    "sync_http_transport_error",
                    url=url, attempt=attempt + 1, error=str(exc),
                )
            else:
                if resp.status_code < 400:
                    return resp.json()
                if resp.status_code < 500 and resp.status_code != 429:
                    resp.raise_for_status()
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}",
                    request=resp.request, response=resp,
                )
                logger.warning(
                    "sync_http_retryable_status",
                    url=url, attempt=attempt + 1, status=resp.status_code,
                )

            if attempt < max_attempts - 1:
                await asyncio.sleep(base_delay_seconds * (2 ** attempt))

        assert last_exc is not None
        raise last_exc

    async def _post_sync(self, path: str, body_extra: Optional[Dict[str, Any]] = None,
                         params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Tiny helper: POST customer creds + from_version=0 to a sync path."""
        payload = {
            "customer_id": self._customer_id,
            "api_key": self._api_key,
            "from_version": 0,
        }
        if body_extra:
            payload.update(body_extra)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._fortress_url}{path}",
                params=params,
                json=payload,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def _sync_obligations(self) -> int:
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text
        import json

        data = await self._post_sync("/api/sync/obligations")
        rows = data.get("obligations", []) or []
        if not rows:
            logger.info("sync_obligations_complete", count=0)
            return 0

        upsert = sqla_text("""
            INSERT INTO obligations
              (obligation_id, regulation_id, regulator, category,
               source_text, obligation_type, section_reference, cfr_citation,
               doc_id, has_test_coverage, test_scenario_ids, extracted_at, is_active)
            VALUES
              (:obligation_id, :regulation_id, :regulator, :category,
               :source_text, :obligation_type, :section_reference, :cfr_citation,
               :doc_id, :has_test_coverage, CAST(:test_scenario_ids AS jsonb),
               :extracted_at, TRUE)
            ON CONFLICT (obligation_id) DO UPDATE SET
              regulation_id     = EXCLUDED.regulation_id,
              regulator         = EXCLUDED.regulator,
              category          = EXCLUDED.category,
              source_text       = EXCLUDED.source_text,
              obligation_type   = EXCLUDED.obligation_type,
              section_reference = EXCLUDED.section_reference,
              cfr_citation      = EXCLUDED.cfr_citation,
              has_test_coverage = EXCLUDED.has_test_coverage,
              test_scenario_ids = EXCLUDED.test_scenario_ids,
              extracted_at      = EXCLUDED.extracted_at,
              is_active         = TRUE
        """)

        async with get_db() as session:
            for r in rows:
                await session.execute(upsert, {
                    "obligation_id":     r["obligation_id"],
                    "regulation_id":     r["regulation_id"],
                    "regulator":         r["regulator"],
                    "category":          r["category"],
                    "source_text":       r["source_text"],
                    "obligation_type":   r["obligation_type"],
                    "section_reference": r.get("section_reference"),
                    "cfr_citation":      r.get("cfr_citation"),
                    "doc_id":            r.get("doc_id"),
                    "has_test_coverage": bool(r.get("has_test_coverage", False)),
                    "test_scenario_ids": json.dumps(r.get("test_scenario_ids") or []),
                    "extracted_at":      _coerce_datetime(r.get("extracted_at")),
                })
            await session.commit()

        logger.info("sync_obligations_complete", count=len(rows))
        return len(rows)

    async def _sync_enforcement_actions(self) -> int:
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text
        import json

        # Paginated (361 rows on P1 today, but accept paging from day one)
        upsert = sqla_text("""
            INSERT INTO enforcement_actions
              (action_ref, regulator, institution_type, asset_tier,
               action_date, action_type, regulations_cited,
               violation_summary, penalty_amount_usd, source_url,
               content_hash, scraped_at, is_active, created_at)
            VALUES
              (:action_ref, :regulator, :institution_type, :asset_tier,
               :action_date, :action_type, CAST(:regulations_cited AS jsonb),
               :violation_summary, :penalty_amount_usd, :source_url,
               :content_hash, :scraped_at, TRUE, NOW())
            ON CONFLICT (action_ref) DO UPDATE SET
              regulator          = EXCLUDED.regulator,
              institution_type   = EXCLUDED.institution_type,
              asset_tier         = EXCLUDED.asset_tier,
              action_date        = EXCLUDED.action_date,
              action_type        = EXCLUDED.action_type,
              regulations_cited  = EXCLUDED.regulations_cited,
              violation_summary  = EXCLUDED.violation_summary,
              penalty_amount_usd = EXCLUDED.penalty_amount_usd,
              source_url         = EXCLUDED.source_url,
              content_hash       = EXCLUDED.content_hash,
              scraped_at         = EXCLUDED.scraped_at,
              is_active          = TRUE
        """)

        page = 1
        total = 0
        while True:
            data = await self._post_sync(
                "/api/sync/enforcement-actions",
                params={"page": page, "page_size": 1000},
            )
            rows = data.get("actions", []) or []
            if not rows:
                break

            async with get_db() as session:
                for r in rows:
                    await session.execute(upsert, {
                        "action_ref":         r["action_ref"],
                        "regulator":          r["regulator"],
                        "institution_type":   r.get("institution_type"),
                        "asset_tier":         r.get("asset_tier"),
                        "action_date":        _coerce_datetime(r.get("action_date")),
                        "action_type":        r["action_type"],
                        "regulations_cited":  json.dumps(r.get("regulations_cited") or []),
                        "violation_summary":  r.get("violation_summary"),
                        "penalty_amount_usd": r.get("penalty_amount_usd"),
                        "source_url":         r.get("source_url"),
                        "content_hash":       r.get("content_hash"),
                        "scraped_at":         _coerce_datetime(r.get("scraped_at")),
                    })
                await session.commit()

            total += len(rows)
            if not data.get("has_more"):
                break
            page += 1

        logger.info("sync_enforcement_actions_complete", count=total)
        return total

    async def _sync_ofac_list_version(self) -> int:
        """Pull the OFAC source-freshness anchor from P1.

        Feeds the ofac_attestation export (design doc §4 type 2). P1's
        endpoint returns the latest row per list_name; on P2 we upsert
        keyed by (list_name, version_id) so repeat syncs are idempotent.

        scrape_run_id is intentionally NULLed on insert — the FK targets
        P1's scrape_runs table, which on P2 stays empty (no scraper runs
        there), and the column is nullable per migration 038.

        P1 returns 404 when no version has been ingested yet (cold start);
        we treat that as "0 rows" rather than a sync failure.
        """
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        upsert = sqla_text("""
            INSERT INTO ofac_list_versions
              (list_name, version_id, published_at, ingested_at,
               source_url, sha256_of_xml, scrape_run_id)
            VALUES
              (:list_name, :version_id, :published_at, :ingested_at,
               :source_url, :sha256_of_xml, NULL)
            ON CONFLICT ON CONSTRAINT uq_ofac_list_versions_list_version
            DO UPDATE SET
              published_at  = EXCLUDED.published_at,
              ingested_at   = EXCLUDED.ingested_at,
              source_url    = EXCLUDED.source_url,
              sha256_of_xml = EXCLUDED.sha256_of_xml
        """)

        total = 0
        for list_name in ("sdn", "cons"):
            try:
                data = await self._post_sync(
                    "/api/sync/ofac-list-version",
                    params={"list_name": list_name},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.info("sync_ofac_list_version_empty",
                                list_name=list_name)
                    continue
                raise

            async with get_db() as session:
                await session.execute(upsert, {
                    "list_name":     data["list_name"],
                    "version_id":    data["version_id"],
                    "published_at":  _coerce_datetime(data.get("published_at")),
                    "ingested_at":   _coerce_datetime(data["ingested_at"]),
                    "source_url":    data["source_url"],
                    "sha256_of_xml": data["sha256_of_xml"],
                })
                await session.commit()
            total += 1

        logger.info("sync_ofac_list_version_complete", count=total)
        return total

    async def _sync_deadlines(self) -> int:
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        data = await self._post_sync("/api/sync/deadlines")
        rows = data.get("deadlines", []) or []
        if not rows:
            logger.info("sync_deadlines_complete", count=0)
            return 0

        upsert = sqla_text("""
            INSERT INTO compliance_deadlines
              (deadline_key, regulation_id, regulator,
               deadline_type, deadline_name, description,
               deadline_date, days_remaining,
               is_recurring, recurrence_rule, is_active, created_at, updated_at)
            VALUES
              (:deadline_key, :regulation_id, :regulator,
               :deadline_type, :deadline_name, :description,
               :deadline_date, :days_remaining,
               :is_recurring, :recurrence_rule, TRUE, NOW(), NOW())
            ON CONFLICT (deadline_key) DO UPDATE SET
              regulation_id    = EXCLUDED.regulation_id,
              regulator        = EXCLUDED.regulator,
              deadline_type    = EXCLUDED.deadline_type,
              deadline_name    = EXCLUDED.deadline_name,
              description      = EXCLUDED.description,
              deadline_date    = EXCLUDED.deadline_date,
              days_remaining   = EXCLUDED.days_remaining,
              is_recurring     = EXCLUDED.is_recurring,
              recurrence_rule  = EXCLUDED.recurrence_rule,
              is_active        = TRUE,
              updated_at       = NOW()
        """)

        async with get_db() as session:
            for r in rows:
                await session.execute(upsert, {
                    "deadline_key":    r["deadline_key"],
                    "regulation_id":   r.get("regulation_id"),
                    "regulator":       r["regulator"],
                    "deadline_type":   r["deadline_type"],
                    "deadline_name":   r["deadline_name"],
                    "description":     r.get("description"),
                    "deadline_date":   _coerce_datetime(r["deadline_date"]),
                    "days_remaining":  r.get("days_remaining"),
                    "is_recurring":    bool(r.get("is_recurring", False)),
                    "recurrence_rule": r.get("recurrence_rule"),
                })
            await session.commit()

        logger.info("sync_deadlines_complete", count=len(rows))
        return len(rows)

    async def _sync_change_records(self, from_version: int) -> int:
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        # version-scoped endpoint
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._fortress_url}/api/sync/change-records",
                params={"limit": 1000},
                json={
                    "customer_id":  self._customer_id,
                    "api_key":      self._api_key,
                    "from_version": from_version,
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        rows = data.get("records", []) or []
        if not rows:
            logger.info("sync_change_records_complete", count=0)
            return 0

        upsert = sqla_text("""
            INSERT INTO regulatory_change_records
              (change_ref, rule_id, regulation_id, regulator,
               detected_at, change_summary, semantic_diff, change_delta,
               impact_rating, change_type, status, signed_off_at,
               is_active, created_at)
            VALUES
              (:change_ref, :rule_id, :regulation_id, :regulator,
               :detected_at, :change_summary, :semantic_diff,
               CAST(:change_delta AS JSONB),
               :impact_rating, :change_type, :status, :signed_off_at,
               TRUE, NOW())
            ON CONFLICT (change_ref) DO UPDATE SET
              rule_id        = EXCLUDED.rule_id,
              regulation_id  = EXCLUDED.regulation_id,
              regulator      = EXCLUDED.regulator,
              detected_at    = EXCLUDED.detected_at,
              change_summary = EXCLUDED.change_summary,
              semantic_diff  = COALESCE(EXCLUDED.semantic_diff,
                                        regulatory_change_records.semantic_diff),
              change_delta   = COALESCE(EXCLUDED.change_delta,
                                        regulatory_change_records.change_delta),
              impact_rating  = EXCLUDED.impact_rating,
              change_type    = EXCLUDED.change_type,
              status         = EXCLUDED.status,
              signed_off_at  = EXCLUDED.signed_off_at,
              is_active      = TRUE
        """)

        async with get_db() as session:
            for r in rows:
                delta = r.get("change_delta")
                await session.execute(upsert, {
                    "change_ref":     r["change_ref"],
                    "rule_id":        r["rule_id"],
                    "regulation_id":  r.get("regulation_id"),
                    "regulator":      r["regulator"],
                    "detected_at":    _coerce_datetime(r.get("detected_at")),
                    "change_summary": r.get("change_summary"),
                    "semantic_diff":  r.get("semantic_diff"),
                    "change_delta":   json.dumps(delta) if delta is not None else None,
                    "impact_rating":  r.get("impact_rating"),
                    "change_type":    r.get("change_type"),
                    "status":         r.get("status") or "detected",
                    "signed_off_at":  _coerce_datetime(r.get("signed_off_at")),
                })
            await session.commit()

        logger.info("sync_change_records_complete", count=len(rows))
        return len(rows)

    async def _sync_risk_scores(self) -> int:
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        data = await self._post_sync("/api/sync/risk-scores")
        rows = data.get("risk_scores", []) or []
        if not rows:
            logger.info("sync_risk_scores_complete", count=0)
            return 0

        upsert = sqla_text("""
            INSERT INTO enforcement_risk_scores
              (regulation_id, regulator, period_months,
               action_count, avg_penalty_usd, total_penalty_usd,
               trend_direction, risk_score, computed_at)
            VALUES
              (:regulation_id, :regulator, 24,
               :action_count, :avg_penalty_usd, :total_penalty_usd,
               :trend_direction, :risk_score, :computed_at)
            ON CONFLICT (regulation_id, period_months) DO UPDATE SET
              regulator         = EXCLUDED.regulator,
              action_count      = EXCLUDED.action_count,
              avg_penalty_usd   = EXCLUDED.avg_penalty_usd,
              total_penalty_usd = EXCLUDED.total_penalty_usd,
              trend_direction   = EXCLUDED.trend_direction,
              risk_score        = EXCLUDED.risk_score,
              computed_at       = EXCLUDED.computed_at
        """)

        async with get_db() as session:
            for r in rows:
                await session.execute(upsert, {
                    "regulation_id":     r["regulation_id"],
                    "regulator":         r["regulator"],
                    "action_count":      r.get("action_count") or 0,
                    "avg_penalty_usd":   r.get("avg_penalty_usd"),
                    "total_penalty_usd": r.get("total_penalty_usd"),
                    "trend_direction":   r.get("trend_direction"),
                    "risk_score":        r.get("risk_score") or 0.0,
                    "computed_at":       _coerce_datetime(r.get("computed_at")),
                })
            await session.commit()

        logger.info("sync_risk_scores_complete", count=len(rows))
        return len(rows)

    async def _sync_benchmarks(self) -> int:
        from src.services.database import get_db
        from sqlalchemy import text as sqla_text

        data = await self._post_sync("/api/sync/benchmarks")
        rows = data.get("benchmarks", []) or []
        if not rows:
            logger.info("sync_benchmarks_complete", count=0)
            return 0

        upsert = sqla_text("""
            INSERT INTO peer_benchmarks
              (regulation_id, regulator, asset_tier, source,
               avg_coverage_pct, median_coverage_pct,
               top_quartile_pct, bottom_quartile_pct,
               data_period, updated_at)
            VALUES
              (:regulation_id, :regulator, :asset_tier, :source,
               :avg_coverage_pct, :median_coverage_pct,
               :top_quartile_pct, :bottom_quartile_pct,
               :data_period, NOW())
            ON CONFLICT (regulation_id, asset_tier, source, data_period) DO UPDATE SET
              regulator           = EXCLUDED.regulator,
              avg_coverage_pct    = EXCLUDED.avg_coverage_pct,
              median_coverage_pct = EXCLUDED.median_coverage_pct,
              top_quartile_pct    = EXCLUDED.top_quartile_pct,
              bottom_quartile_pct = EXCLUDED.bottom_quartile_pct,
              updated_at          = NOW()
        """)

        async with get_db() as session:
            for r in rows:
                await session.execute(upsert, {
                    "regulation_id":       r["regulation_id"],
                    "regulator":           r["regulator"],
                    "asset_tier":          r["asset_tier"],
                    "source":              r["source"],
                    "avg_coverage_pct":    r.get("avg_coverage_pct"),
                    "median_coverage_pct": r.get("median_coverage_pct"),
                    "top_quartile_pct":    r.get("top_quartile_pct"),
                    "bottom_quartile_pct": r.get("bottom_quartile_pct"),
                    "data_period":         _coerce_datetime(r["data_period"]),
                })
            await session.commit()

        logger.info("sync_benchmarks_complete", count=len(rows))
        return len(rows)

    async def _record_sync_status(
        self,
        version: int,
        added: int,
        updated: int,
        deleted: int,
        sync_type: str,
        error: Optional[str] = None,
    ) -> None:
        """Record sync operation in local_sync_status table."""
        from src.services.database import get_db
        from src.models.database import LocalSyncStatus

        async with get_db() as session:
            status = LocalSyncStatus(
                sync_version=version,
                rules_added=added,
                rules_updated=updated,
                rules_deleted=deleted,
                sync_status="FAILED" if error else "SUCCESS",
                error_message=error,
                sync_type=sync_type,
            )
            session.add(status)
            await session.commit()

    async def _refresh_vector_index(self, current_version: int) -> None:
        """
        Refresh pgvector HNSW index for rules updated in this sync cycle.
        pgvector on Aurora handles vector search (P2).
        The embedding column is populated by P1 and synced via _upsert_rules().
        This method ensures the HNSW index covers newly inserted embeddings.
        """
        try:
            from src.services.database import get_db
            from sqlalchemy import text

            async with get_db() as session:
                # _upsert_rules doesn't stamp a per-sync version column on
                # compliance_rules, so any `sync_version = :ver` predicate here
                # would always be empty. Count the actual signal: active rules
                # that carry an embedding.
                result = await session.execute(text("""
                    SELECT COUNT(*) FROM compliance_rules
                    WHERE embedding IS NOT NULL
                      AND is_active = TRUE
                """))
                count = result.scalar() or 0

                if count > 0:
                    # HNSW index updates automatically on INSERT/UPDATE in pgvector.
                    # Run ANALYZE to update planner statistics so Agent 2
                    # hybrid queries use the index rather than a seq scan.
                    await session.execute(text("ANALYZE compliance_rules"))
                    await session.commit()

            logger.info("pgvector_index_refreshed",
                        rules_with_embeddings=count,
                        sync_version=current_version)

        except Exception as exc:
            # Non-fatal — HNSW index still works, just with stale planner stats
            logger.warning("pgvector_analyze_failed", error=str(exc))

    # ── License verification ────────────────────────────────────────

    async def _verify_license(self) -> bool:
        """Check license status with Fortress Cloud.

        H10 — fail-closed. Previously this returned True on any
        exception (timeout, DNS, 5xx), so an expired or suspended bank
        whose license endpoint happened to be unreachable would keep
        pulling fresh compliance data indefinitely. The new contract
        is: reachability failure → sync skipped, alarm raised.

        Ops can override via `LICENSE_FAIL_OPEN_OVERRIDE=true` in
        SSM (incident response only). The override is logged at error
        level on every check so a stuck-on override is visible.
        """
        import os

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._fortress_url}/api/license/verify",
                    params={
                        "customer_id": self._customer_id,
                        "api_key": self._api_key,
                    },
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("status") == "ACTIVE"
        except Exception as exc:
            override = os.getenv("LICENSE_FAIL_OPEN_OVERRIDE", "").lower() == "true"
            if override:
                logger.error(
                    "license_check_failed_fail_open_override",
                    error=str(exc),
                    customer_id=self._customer_id,
                )
                return True
            logger.warning(
                "license_check_failed_fail_closed",
                error=str(exc),
                customer_id=self._customer_id,
            )
            return False

    # ── Telemetry ───────────────────────────────────────────────────

    async def _report_telemetry(self, result: Dict[str, Any]) -> None:
        """Report sync result to Fortress for monitoring."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self._fortress_url}/api/sync/status",
                    json={
                        "customer_id": self._customer_id,
                        "sync_version": result.get("version", 0),
                        "sync_status": result.get("status", "UNKNOWN"),
                        "rules_added": result.get("rules_added", 0),
                        "rules_updated": result.get("rules_updated", 0),
                        "rules_deleted": result.get("rules_deleted", 0),
                        "error_message": result.get("error"),
                    },
                    headers=self._auth_headers(),
                )
        except Exception as exc:
            # Telemetry failure is non-fatal
            logger.debug("telemetry_report_failed", error=str(exc))
