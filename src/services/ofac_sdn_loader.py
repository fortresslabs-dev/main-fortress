"""OFAC SDN → ofac_sdn_vectors loader.

Called from scraper_scheduler.py after the OFAC connector parses
SDN_ADVANCED.XML. Responsibilities:

  1. Normalize each name variant (primary + every alias) — single source
     of truth for normalization shared with OFACScreener.
  2. Validate the pull is not corrupt (count drop, zero parties) — refuses
     to run the diff/deactivate path if the scrape looks broken.
  3. Upsert variants into ofac_sdn_vectors on (sdn_number, name_normalized).
     Embeddings are intentionally NOT touched here — the embed_sdn_variants
     job populates them. New rows get embedding=NULL.
  4. Tombstone (soft-deactivate) variants absent from this pull, but only
     after N consecutive misses (default 2), and log every deactivation to
     regulatory_change_records for the CCO dashboard.
  5. Log lifecycle events to regulatory_change_records: 'added' for first-
     seen variants and 'reactivated' for variants that were tombstoned and
     came back. Deactivations ('removed') were already logged in step 4.
     Drives P2 continuous-monitoring rescreens via /api/sync/sdn-changes.

Why a separate module: keeps the connector free of DB code and makes the
loader testable without spinning up the full scrape cycle.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# Refuse to deactivate if the pull dropped by more than this fraction. OFAC
# delists ~0.5% of entities per year — a >2% drop in a single day is almost
# certainly a corrupt scrape.
_MAX_DROP_FRACTION = 0.02

# Number of consecutive misses before a row is soft-deactivated.
_TOMBSTONE_AFTER_MISSES = 2


# ── Normalization ─────────────────────────────────────────────────────────

# Intra-syllable marks used in OFAC transliterations (`Ch'o'l` ≡ `Chol`,
# `Hyo'k-ch'o'l` ≡ `HYOK CHOL`). Stripped WITHOUT a space so the syllables
# fuse — else a two-token query like "kim chol" would never match the
# fragmented "KIM CH O L".
_INTRA_WORD_MARKS_RE = re.compile("[\u0027\u0060\u2018\u2019\u201B\u02BC\u02BB]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_name(name: str) -> str:
    """NFD-decompose, strip combining marks (accents), uppercase, strip punct, collapse ws.

    Shared with src/services/validators.py:OFACScreener — both sides MUST
    use the same function or matches will silently miss.
    """
    if not name:
        return ""
    nfd = unicodedata.normalize("NFD", name)
    no_accent = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    upper = no_accent.upper()
    no_marks = _INTRA_WORD_MARKS_RE.sub("", upper)
    no_punct = _PUNCT_RE.sub(" ", no_marks)
    return " ".join(no_punct.split())


# ── Parsing helpers ───────────────────────────────────────────────────────

def _coerce_programs(raw: Any) -> list[str]:
    """Accept list, str, or None — mirror scheduler defensive parsing."""
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    if isinstance(raw, str):
        # Existing scheduler handles "[SDGT][IRAN]"-style payloads
        return [p.strip() for p in raw.replace("][", ",").strip("[]").split(",") if p.strip()]
    return []


def _iter_variants(entry: dict) -> Iterable[dict]:
    """Yield one dict per (sdn_number, name_variant). variant 0 = primary."""
    sdn_number = str(entry.get("sdn_number", "")).strip()
    if not sdn_number:
        return

    common = {
        "sdn_number": sdn_number,
        "entity_type": entry.get("entity_type") or "entity",
        "programs": _coerce_programs(entry.get("programs")),
        "country": (entry.get("country") or "")[:100] or None,
        "nationality": (entry.get("nationality") or "")[:100] or None,
        "source_updated_at": _parse_dt(entry.get("source_updated_at")),
    }

    primary = (entry.get("entity_name") or "").strip()
    seen_norm: set[str] = set()
    if primary:
        norm = normalize_name(primary)
        if norm:
            seen_norm.add(norm)
            yield {**common, "name_variant": primary, "name_normalized": norm, "is_primary": True}

    for alias in entry.get("aliases") or []:
        alias_str = str(alias).strip()
        if not alias_str:
            continue
        norm = normalize_name(alias_str)
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        yield {**common, "name_variant": alias_str, "name_normalized": norm, "is_primary": False}


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw)[:19])
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ── Integrity gate ────────────────────────────────────────────────────────

class PullIntegrityError(Exception):
    """Raised when a scrape looks too suspicious to trust for diff/deactivation."""


async def validate_pull_integrity(session: AsyncSession, parsed_count: int) -> None:
    """Raise if today's pull is implausibly smaller than yesterday's.

    The caller MUST catch this and skip the deactivation step, but should
    still upsert (additive is always safe — at worst we keep a row alive
    one cycle longer).
    """
    if parsed_count == 0:
        raise PullIntegrityError("zero SDN entries parsed — XML likely truncated")

    row = (await session.execute(text(
        "SELECT count(DISTINCT sdn_number) FROM ofac_sdn_vectors WHERE is_active = TRUE"
    ))).first()
    last_known = int(row[0]) if row else 0
    if last_known == 0:
        return  # first-ever load

    drop = (last_known - parsed_count) / last_known
    if drop > _MAX_DROP_FRACTION:
        raise PullIntegrityError(
            f"count dropped {drop:.1%} ({last_known} → {parsed_count}); "
            f"refusing to deactivate. Threshold: {_MAX_DROP_FRACTION:.1%}"
        )


# ── Upsert ────────────────────────────────────────────────────────────────

async def upsert_sdn_variants(
    session: AsyncSession,
    entries: list[dict],
) -> tuple[int, set[tuple[str, str]]]:
    """Upsert every variant. Returns (rows_processed, set of (sdn_number, name_normalized)).

    The returned set is the "seen today" set used by tombstone_missing_variants.
    Embeddings are preserved when name_normalized is unchanged. When
    name_normalized changes (it shouldn't — uniqueness key — but defensive),
    a new row is inserted and the embedding starts NULL.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        for variant in _iter_variants(entry):
            seen.add((variant["sdn_number"], variant["name_normalized"]))
            rows.append(variant)

    if not rows:
        return 0, seen

    # Single INSERT … ON CONFLICT, fed via execute_values-equivalent.
    # Use jsonb for programs to match the JSON column type.
    now = datetime.now(timezone.utc)
    stmt = text("""
        INSERT INTO ofac_sdn_vectors (
            sdn_number, name_variant, name_normalized, is_primary,
            entity_type, programs, country, nationality,
            source_updated_at, last_seen_at, missed_pulls, is_active
        ) VALUES (
            :sdn_number, :name_variant, :name_normalized, :is_primary,
            :entity_type, CAST(:programs AS json), :country, :nationality,
            :source_updated_at, :last_seen_at, 0, TRUE
        )
        ON CONFLICT (sdn_number, name_normalized) DO UPDATE SET
            name_variant      = EXCLUDED.name_variant,
            is_primary        = EXCLUDED.is_primary,
            entity_type       = EXCLUDED.entity_type,
            programs          = EXCLUDED.programs,
            country           = EXCLUDED.country,
            nationality       = EXCLUDED.nationality,
            source_updated_at = EXCLUDED.source_updated_at,
            last_seen_at      = EXCLUDED.last_seen_at,
            missed_pulls      = 0,
            is_active         = TRUE,
            deactivated_at    = NULL
        -- embedding preserved on conflict
    """)

    import json as _json
    batch_size = 500
    added = reactivated = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]

        # Snapshot prior state for change-record classification BEFORE the
        # upsert overwrites is_active. Missing keys = brand-new rows.
        prior_map = await _snapshot_prior_state(session, batch)

        params = [
            {**r, "programs": _json.dumps(r["programs"]), "last_seen_at": now}
            for r in batch
        ]
        await session.execute(stmt, params)

        for r in batch:
            prior_active = prior_map.get((r["sdn_number"], r["name_normalized"]))
            if prior_active is None:
                await _log_sdn_change(session, "added", r)
                added += 1
            elif prior_active is False:
                await _log_sdn_change(session, "reactivated", r)
                reactivated += 1

    logger.info(
        "ofac_sdn_variants_upserted",
        count=len(rows),
        entities=len({r["sdn_number"] for r in rows}),
        added=added,
        reactivated=reactivated,
    )
    return len(rows), seen


async def _snapshot_prior_state(
    session: AsyncSession,
    batch: list[dict],
) -> dict[tuple[str, str], bool]:
    """Return {(sdn_number, name_normalized): prior_is_active} for keys in
    `batch` that already exist in ofac_sdn_vectors. Keys absent from the
    return = rows that are about to be INSERTed (no prior state)."""
    if not batch:
        return {}
    sdn_numbers = [r["sdn_number"] for r in batch]
    norms = [r["name_normalized"] for r in batch]
    result = await session.execute(text("""
        SELECT v.sdn_number, v.name_normalized, v.is_active
        FROM ofac_sdn_vectors v
        JOIN UNNEST(CAST(:sdns AS text[]), CAST(:norms AS text[])) AS k(sdn_number, name_normalized)
          ON v.sdn_number = k.sdn_number
         AND v.name_normalized = k.name_normalized
    """), {"sdns": sdn_numbers, "norms": norms})
    return {(r.sdn_number, r.name_normalized): r.is_active for r in result.all()}


# ── Tombstone ─────────────────────────────────────────────────────────────

async def tombstone_missing_variants(
    session: AsyncSession,
    seen_today: set[tuple[str, str]],
) -> tuple[int, int]:
    """N-strikes soft deactivation. Returns (incremented, newly_deactivated).

    Logs every transition to is_active=FALSE in regulatory_change_records.
    """
    # Build a temp table from the seen set so we can do set-based SQL.
    # Faster than NOT IN on a huge IN list.
    await session.execute(text("""
        CREATE TEMP TABLE IF NOT EXISTS _seen_today (
            sdn_number text,
            name_normalized text,
            PRIMARY KEY (sdn_number, name_normalized)
        ) ON COMMIT DROP
    """))
    await session.execute(text("TRUNCATE _seen_today"))

    if seen_today:
        rows = [{"s": s, "n": n} for s, n in seen_today]
        # chunk to keep parameter count reasonable
        for i in range(0, len(rows), 1000):
            await session.execute(
                text("INSERT INTO _seen_today (sdn_number, name_normalized) VALUES (:s, :n)"),
                rows[i : i + 1000],
            )

    # Increment missed_pulls for active rows not seen today
    result = await session.execute(text("""
        UPDATE ofac_sdn_vectors v
        SET missed_pulls = missed_pulls + 1
        WHERE v.is_active = TRUE
          AND NOT EXISTS (
            SELECT 1 FROM _seen_today s
            WHERE s.sdn_number = v.sdn_number
              AND s.name_normalized = v.name_normalized
          )
        RETURNING v.id
    """))
    incremented = len(result.all())

    # Now deactivate anything that's crossed the threshold. Capture rows for
    # change-record logging in the same query.
    deactivated = (await session.execute(text("""
        WITH to_kill AS (
            SELECT id, sdn_number, name_variant, programs, country, entity_type
            FROM ofac_sdn_vectors
            WHERE is_active = TRUE
              AND missed_pulls >= :strikes
        ),
        upd AS (
            UPDATE ofac_sdn_vectors
            SET is_active = FALSE,
                deactivated_at = now()
            WHERE id IN (SELECT id FROM to_kill)
            RETURNING id
        )
        SELECT sdn_number, name_variant, programs, country, entity_type
        FROM to_kill
    """), {"strikes": _TOMBSTONE_AFTER_MISSES})).all()

    # Log to regulatory_change_records (one event per deactivated variant)
    for r in deactivated:
        await _log_sdn_change(session, "removed", {
            "sdn_number": r.sdn_number,
            "name_variant": r.name_variant,
            "name_normalized": None,  # not selected — _log_sdn_change derives ref from variant
            "is_primary": None,
            "programs": r.programs,
            "country": r.country,
            "entity_type": r.entity_type,
        })

    logger.info(
        "ofac_sdn_tombstone",
        incremented=incremented,
        deactivated=len(deactivated),
        strike_threshold=_TOMBSTONE_AFTER_MISSES,
    )
    return incremented, len(deactivated)


# ── List-version anchor (migration 038) ──────────────────────────────────

# Recognised list names — must stay in lock-step with the
# ck_ofac_list_versions_list_name CHECK constraint.
_VALID_LIST_NAMES = {"sdn", "cons"}

# Best-effort Publish_Date regex: matches root-element attributes like
#   <sdnList Publish_Date="06/18/2026" ...>
# or per-OFAC newer formats nested in <Publish_Information>.
_PUBLISH_DATE_RE = re.compile(
    rb'Publish[_-]?Date\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE
)
_VERSION_RE = re.compile(
    rb'<Version>\s*([^<\s]+)\s*</Version>', re.IGNORECASE
)


def _parse_publish_metadata(
    xml_bytes: bytes, last_modified_header: str | None
) -> tuple[str, datetime | None]:
    """Extract (version_id, published_at) from the raw XML, falling back to
    the source URL's Last-Modified header when the XML omits both Publish_Date
    and Version elements.

    Returns (version_id, published_at). version_id is non-empty; published_at
    is best-effort and may be None.
    """
    version_match = _VERSION_RE.search(xml_bytes)
    publish_match = _PUBLISH_DATE_RE.search(xml_bytes)

    version_raw: str | None = None
    if version_match:
        version_raw = version_match.group(1).decode("utf-8", "replace").strip()
    elif publish_match:
        version_raw = publish_match.group(1).decode("utf-8", "replace").strip()

    if not version_raw:
        # Fall back to Last-Modified — verbatim string, not parsed, so we
        # attest to what the source actually said.
        version_raw = (last_modified_header or "").strip()

    # If we still have nothing, the caller has no usable freshness anchor;
    # synthesise a deterministic placeholder from the XML hash prefix so
    # the (list_name, version_id) UNIQUE key still works.
    if not version_raw:
        version_raw = "unknown-" + hashlib.sha256(xml_bytes).hexdigest()[:12]

    published_at: datetime | None = None
    candidates: list[str] = []
    if publish_match:
        candidates.append(publish_match.group(1).decode("utf-8", "replace").strip())
    if last_modified_header:
        candidates.append(last_modified_header.strip())

    for raw in candidates:
        # OFAC publish format: MM/DD/YYYY. HTTP date: RFC 7231.
        for fmt in ("%m/%d/%Y", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                published_at = dt.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if published_at is not None:
            break

    return version_raw[:64], published_at


async def record_list_version(
    session: AsyncSession,
    *,
    list_name: str,
    xml_bytes: bytes,
    source_url: str,
    last_modified_header: str | None = None,
    scrape_run_id: str | None = None,
) -> str:
    """Write the OFAC-source freshness anchor for this pull.

    Called by `scraper_scheduler.py` immediately after the connector fetches
    SDN_ADVANCED.XML, before parsing. Idempotent: same XML hash + same
    (list_name, version_id) is a no-op via ON CONFLICT.

    Returns the SHA-256 hex digest so the caller can log it alongside the
    scrape result.
    """
    if list_name not in _VALID_LIST_NAMES:
        raise ValueError(
            f"unknown OFAC list_name {list_name!r}; expected one of {_VALID_LIST_NAMES}"
        )

    sha256_hex = hashlib.sha256(xml_bytes).hexdigest()
    version_id, published_at = _parse_publish_metadata(xml_bytes, last_modified_header)

    await session.execute(text("""
        INSERT INTO ofac_list_versions (
            list_name, version_id, published_at, ingested_at,
            source_url, sha256_of_xml, scrape_run_id
        ) VALUES (
            :list_name, :version_id, :published_at, now(),
            :source_url, :sha256_of_xml, :scrape_run_id
        )
        ON CONFLICT (list_name, version_id) DO NOTHING
    """), {
        "list_name": list_name,
        "version_id": version_id,
        "published_at": published_at,
        "source_url": source_url[:2048],
        "sha256_of_xml": sha256_hex,
        "scrape_run_id": scrape_run_id,
    })

    logger.info(
        "ofac_list_version_recorded",
        list_name=list_name,
        version_id=version_id,
        sha256=sha256_hex,
        published_at=published_at.isoformat() if published_at else None,
        scrape_run_id=scrape_run_id,
    )
    return sha256_hex


async def populate_variants(session: AsyncSession) -> dict[str, int]:
    """Rebuild ofac_sdn_vectors from the live ofac_sdn_entries table.

    Called by P2's UpdateService after `ofac_sdn_entries` finishes syncing
    from P1 (P2 has no scraper of its own). The entries table is the source
    of truth; this function fans each active entry out into its name variants
    and feeds the standard upsert + N-strikes tombstone pipeline.

    Returns a stats dict suitable for `logger.info("...", **stats)`. Always
    commits via the caller — we run inside their `async with get_db()` block.

    On PullIntegrityError the upsert still runs (additive is always safe),
    but tombstoning is skipped — never let one bad pull deactivate real SDN
    rows.
    """
    rows = (await session.execute(text("""
        SELECT sdn_number, entity_name, entity_type,
               programs, aliases, country, nationality, source_updated_at
        FROM ofac_sdn_entries
        WHERE is_active = TRUE
    """))).mappings().all()

    entries = [dict(r) for r in rows]
    integrity_ok = True
    try:
        await validate_pull_integrity(session, len(entries))
    except PullIntegrityError as exc:
        integrity_ok = False
        logger.warning("ofac_sdn_integrity_skip_tombstone", reason=str(exc))

    variants_upserted, seen = await upsert_sdn_variants(session, entries)

    incremented = deactivated = 0
    if integrity_ok:
        incremented, deactivated = await tombstone_missing_variants(session, seen)

    return {
        "entries": len(entries),
        "variants_upserted": variants_upserted,
        "incremented": incremented,
        "deactivated": deactivated,
        "tombstone_skipped": 0 if integrity_ok else 1,
    }


# change_ref prefix scheme — kept stable so existing 'OFAC-DEACT-' rows
# remain idempotent under ON CONFLICT. Reactivations and additions get
# their own prefixes so a re-add of a previously-removed variant lands
# as a distinct event, not silently deduped against the old DEACT row.
_REF_PREFIX = {"added": "ADD", "reactivated": "REACT", "removed": "DEACT"}


def format_sdn_change_text(change_type: str, variant: dict) -> tuple[str, str]:
    """Build (change_summary, semantic_diff) for a variant lifecycle event.

    `change_summary` is a single human sentence rendered as the title of
    each row in the CCO dashboard's regulatory-change inbox.
    `semantic_diff` is a short ` · `-separated detail line for the row body.

    Kept as a module-level helper so the backfill script can rewrite legacy
    rows with the same formatting that the loader emits going forward.
    """
    verb = {"added": "added", "reactivated": "reactivated", "removed": "removed"}.get(
        change_type, change_type
    )
    name = (variant.get("name_variant") or "(unknown)").strip() or "(unknown)"
    raw_programs = variant.get("programs")
    if isinstance(raw_programs, list):
        programs = [str(p).strip() for p in raw_programs if str(p).strip()]
    elif isinstance(raw_programs, str):
        programs = [p.strip() for p in raw_programs.replace("][", ",").strip("[]").split(",") if p.strip()]
    else:
        programs = []
    progs_str = ", ".join(programs) if programs else "no program tag"
    entity_type = (variant.get("entity_type") or "").strip() or "entity"
    country = (variant.get("country") or "").strip()

    summary = f"SDN {verb}: {name} — {entity_type}, programs: {progs_str}"
    if change_type == "removed":
        summary += f" (absent from {_TOMBSTONE_AFTER_MISSES} consecutive pulls)"

    role = "primary name" if variant.get("is_primary") else "alias"
    detail_parts = [role, entity_type, f"programs: {progs_str}", f"country: {country or '—'}"]
    return summary, " · ".join(detail_parts)


async def _log_sdn_change(
    session: AsyncSession,
    change_type: str,
    variant: dict,
) -> None:
    """Append an SDN-variant lifecycle event to regulatory_change_records.

    `change_type` must be one of: 'added', 'reactivated', 'removed'. The
    constraint ck_change_record_type (migration 029) enforces this on the
    DB side as well.

    One event per variant (not per SDN). Downstream consumers — P2's
    continuous-monitoring API — should dedupe by (sdn_number, change_type)
    per scrape cycle if they want per-entity events. Per-variant emission
    is intentional so alias-only changes remain visible in the audit log.

    `change_summary` is a human sentence and `semantic_diff` carries the
    structured detail. See format_sdn_change_text() for the format —
    the CCO dashboard renders summary as the row title and diff as the body.
    """
    prefix = _REF_PREFIX.get(change_type)
    if prefix is None:
        raise ValueError(f"unknown SDN change_type: {change_type!r}")

    # Hash key uses (sdn_number, name_variant) so the ref is stable for the
    # same variant across re-runs in the same lifecycle phase, but a later
    # reactivation gets a new ref because the prefix changed.
    change_ref = f"OFAC-{prefix}-" + hashlib.sha1(
        f"{variant['sdn_number']}|{variant.get('name_variant') or ''}".encode()
    ).hexdigest()[:12]

    summary, diff = format_sdn_change_text(change_type, variant)

    await session.execute(text("""
        INSERT INTO regulatory_change_records (
            id, change_ref, rule_id, regulator, change_type, status,
            change_summary, semantic_diff,
            detected_at, status_updated_at, created_at, is_active
        ) VALUES (
            gen_random_uuid(), :change_ref, :rule_id, 'OFAC', :change_type, 'detected',
            :summary, :diff,
            now(), now(), now(), TRUE
        )
        ON CONFLICT (change_ref) DO NOTHING
    """), {
        "change_ref": change_ref,
        "rule_id": f"OFAC-SDN-{variant['sdn_number']}",
        "change_type": change_type,
        "summary": summary,
        "diff": diff,
    })
