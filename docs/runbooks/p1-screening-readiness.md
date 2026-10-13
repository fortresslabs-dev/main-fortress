# P1 Screening Readiness — Prerequisite for P2 `/customer-screen`

Status: audit + work plan, 2026-06-04 · Owner: P1 ingestion (Agent 2) + sync API

P2 (bank-side) screening cannot ship cleanly until P1 (Fortress Cloud) ships
authoritative SDN vectors + embeddings + deltas via the sync API. Today P2
re-derives and re-embeds locally — which works but is wasteful and risks
model-version drift across banks.

This doc audits the current P1 state, lists the gaps, and specifies the P1
work that must complete first.

---

## 1 · What P1 has today (working)

| Component | File | Status |
|---|---|---|
| OFAC SDN connector (daily scrape) | `src/connectors/regulatory/usa/ofac_sdn.py` | ✅ in prod |
| Raw XML → S3 archive | `scraper-chunks/sdn_xml_raw/YYYY/MM/DD/*.xml.gz` (KMS) | ✅ |
| `ofac_sdn_entries` (legacy, 1-row-per-SDN) | migrations 005/007 | ✅ |
| `ofac_sdn_vectors` (variants, embeddings) | migrations 021/022 | ✅ |
| Loader (entries → variants + tombstoning) | `src/services/ofac_sdn_loader.py` | ✅ |
| Embed job (Titan v2 1024-dim) | `src/jobs/embed_sdn_variants.py` | ✅ |
| Scraper → both tables on each pull | `scraper_scheduler.py:566` `upsert_sdn_variants` | ✅ |
| `regulatory_change_records` (delta log) | migration 011 | ✅ |
| Sync API `/api/sync/ofac-sdn` (entries only) | `sync_api.py:577` | ⚠️ legacy-only |
| Calibration script + gold-set runbook | `scripts/setup/calibrate_ofac_thresholds.py` + runbook | ✅ (awaiting real gold) |

P1 scrape → P1 storage → P1 embeddings already work. The gap is the sync surface.

---

## 2 · What P1 is missing (gaps)

### Gap A — Sync API does not ship `ofac_sdn_vectors` or embeddings

**Current:** `/api/sync/ofac-sdn` returns `OFACSDNEntry` rows (sdn_number, entity_name, aliases JSON, addresses, etc.). P2 calls `populate_variants` locally to expand aliases into variant rows, then `embed_pending` to call Bedrock locally.

**Why it matters:**
- Each P2 bank pays N × Bedrock Titan invoke costs for the same public OFAC data
- Model-version drift: if P1 ever upgrades the embed model, banks pinned to older versions silently produce incompatible vectors → cross-bank scoring is no longer comparable
- First-sync onboarding is slow (~10k variants × Bedrock latency)

**Fix:** Add `/api/sync/sdn-vectors` endpoint that ships pre-computed variant rows including the 1024-dim embedding (mirrors the pattern of `/api/sync/embeddings` for compliance rules).

### Gap B — No SDN delta feed

**Current:** `/api/sync/ofac-sdn` is full-dump every time. P2 has no way to ask "which SDN entries are NEW since version X". The `/api/sync/change-records` endpoint exists but filters by `compliance_rules.rule_id` — not SDN.

**Why it matters:** The continuous-monitoring API (`POST /validate/v1/monitoring/runs`) needs to rescreen the customer set against newly-added SDN entries only. Without a delta, P2 either rescreens everyone against everything daily (wasteful) or duplicates change-detection logic locally (drift risk).

**Fix:** Extend `regulatory_change_records` writes to cover SDN add/reactivate/deactivate events (`change_type` ∈ `sdn_added`, `sdn_reactivated`, `sdn_deactivated`), and add `/api/sync/sdn-changes` to surface them.

The loader (`ofac_sdn_loader.py`) already logs deactivations to `regulatory_change_records` — additions/reactivations need the same treatment.

### Gap C — No `last_scraped_at` heartbeat in sync API

**Current:** P2's `OFACScreener._enforce_freshness()` checks `MAX(last_seen_at)` on its **local** `ofac_sdn_vectors`. If P2 sync is lagging, P2 thinks the data is fresh — but the data P1 scraped 30h ago is still 30h old.

**Why it matters:** Compliance rule `OFAC-SDN-DAILY-001` is about the **source** being current, not the replica. A P2 bank with broken sync should NOT continue screening against stale data and think it's compliant.

**Fix:** Add `p1_last_scraped_at` to `/api/sync/counts` response (or new `/api/sync/sdn-health`). Pull it from `scrape_runs.completed_at WHERE connector='ofac_sdn' AND ok=TRUE`. P2 freshness gate checks the max of `(p1_last_scraped_at, local_last_seen_at)`.

### Gap D — No shipped threshold defaults

**Current:** Each P2 bank's SSM must be populated independently with `/fortress/{client}/ofac/threshold/*`. No mechanism to ship a known-good default set from P1.

**Why it matters:** A new bank onboarding has no thresholds → adapter falls back to single 0.85 → degrades to PASS/HOLD only (no REVIEW band). This is fine as a safety default, but ops would prefer P1 to ship conservative defaults out of the box.

**Fix:** Optional — bake into `/api/sync/sdn-health` response a `recommended_thresholds: {individual: {hold: 0.92, review: 0.78}, ...}` object that P2 reads on first sync and writes to its own SSM. Defer this; not blocking.

---

## 3 · Required P1 changes (in order)

### 3.1 · New alembic migration — none required for Gaps A/B/C

`ofac_sdn_vectors` (021/022) and `regulatory_change_records` (011) already have the columns we need. **No new migration on P1 for this phase.** The work is API + delta-logging code changes only.

If we add Gap D later, no migration either — the recommended defaults can come from `settings` or a static JSON.

### 3.2 · `src/services/ofac_sdn_loader.py` — extend change-record logging

Currently logs deactivations. Add log entries for:
- **Additions** (`sdn_number` first seen): `change_type='sdn_added'`
- **Reactivations** (was tombstoned, now back): `change_type='sdn_reactivated'`

Use the same `regulatory_change_records` table — set `regulator='OFAC'`, `regulation_id='SDN'`, `change_summary` with the entity name + programs, `detected_at=now()`.

### 3.3 · `src/api/sync_api.py` — new endpoints

```python
@router.post("/sync/sdn-vectors")
async def sync_sdn_vectors(
    request: SyncRequest,
    page: int = Query(1, ge=1),
    page_size: int = Query(2000, ge=100, le=10000),
    embedded_only: bool = Query(True),
):
    """Paginated sync of ofac_sdn_vectors rows WITH 1024-dim embeddings.
    P2 stores directly into its local pgvector table — no Bedrock calls."""
```

```python
@router.post("/sync/sdn-changes")
async def sync_sdn_changes(
    request: SyncRequest,
    since: datetime,
    limit: int = Query(500, ge=1, le=2000),
):
    """SDN change records since `since` timestamp.
    Drives P2 continuous-monitoring rescreens."""
```

```python
@router.post("/sync/sdn-health")
async def sync_sdn_health(request: SyncRequest):
    """Returns P1-side SDN freshness telemetry:
       {
         "p1_last_scraped_at": "...",
         "p1_entries_active":    N,
         "p1_variants_active":   M,
         "p1_variants_embedded": M',
         "embed_coverage_pct":   pct,
         "recommended_thresholds": {...}   # Gap D — optional
       }"""
```

### 3.4 · `src/services/update_service.py` (P2) — consume new endpoints

After P1 ships, P2's `_sync_ofac_sdn` switches from local re-derive/re-embed to direct pull:

```python
# Replace populate_variants + embed_pending with:
await self._sync_sdn_vectors_paginated()    # uses /sync/sdn-vectors
await self._record_sdn_changes()             # uses /sync/sdn-changes
```

`populate_variants` + `embed_pending` stay in the tree (local-dev fallback when `FORTRESS_API_URL` is unset) but are no longer the production path.

The freshness gate in `OFACScreener._enforce_freshness` reads `p1_last_scraped_at` from a cached `/sync/sdn-health` poll (refreshed every 10 min by update_service).

### 3.5 · Compliance rule registry

Add a new compliance rule row (or document) for `OFAC-SYNC-VECTOR-001` covering: "P2 must use P1-shipped vectors when `FORTRESS_API_URL` is configured." Surfaces in the dashboard readiness panel so an examiner can see the bank is on the authoritative embed.

---

## 4 · Test gates before P1 ships

| Gate | How to verify |
|---|---|
| `/sync/sdn-vectors` returns ≤ page_size rows with non-null embedding | `curl -X POST .../api/sync/sdn-vectors -d '{"customer_id":"X","api_key":"Y"}' \| jq '.rows[0].embedding \| length'` → 1024 |
| `/sync/sdn-changes` returns delta-only since timestamp | Insert test SDN row, call endpoint with `since=` 1 min ago, expect 1 row |
| `/sync/sdn-health` reports current `p1_last_scraped_at` | Compare against `SELECT max(completed_at) FROM scrape_runs WHERE connector='ofac_sdn' AND ok=TRUE` |
| Additions logged to `regulatory_change_records` | Insert variant, call `upsert_sdn_variants`, expect `change_type='sdn_added'` row |
| Sync API auth still works | Existing `/sync/ofac-sdn` regression test passes; new endpoints reject bad keys |

---

## 5 · Rollout sequencing (revised)

```
[Phase A — P1] (this doc)
  1. Loader logs sdn_added / sdn_reactivated
  2. New /sync/sdn-vectors, /sync/sdn-changes, /sync/sdn-health endpoints
  3. Deploy P1 with all three live
  4. Verify against pilot P2 in a dry-run mode (consumes endpoints but
     still uses local populate_variants — confirms parity before flip)

[Phase B — P2 update_service] (cuts over after Phase A green)
  5. update_service switches to consuming /sync/sdn-vectors
  6. Local embedding path becomes a fallback (FORTRESS_API_URL unset only)
  7. Freshness gate reads p1_last_scraped_at

[Phase C — P2 screening adapter + dashboard] (only after B is stable)
  8. /validate/v1/customer-screen endpoint (per customer-screen plan)
  9. screening_events / screening_cases writers (migration 028 already applied)
 10. Dashboard /screening pages
```

Each phase deploys independently — they can be paused between phases
without breaking anything.

---

## 6 · Action items (replaces the previous OFAC rollout list in CLAUDE.md)

### P1 changes (do these first)
- [ ] Extend `ofac_sdn_loader.upsert_sdn_variants` to write `sdn_added` / `sdn_reactivated` to `regulatory_change_records`
- [ ] Add `POST /api/sync/sdn-vectors` (paginated, embeddings inline)
- [ ] Add `POST /api/sync/sdn-changes` (delta-only, since-timestamp)
- [ ] Add `POST /api/sync/sdn-health` (heartbeat + counts + optional thresholds)
- [ ] Update `/api/sync/counts` to include `ofac_sdn_vectors` + `ofac_sdn_vectors_embedded` counts
- [ ] Backfill `regulatory_change_records` for all currently-active SDN rows so the first `/sync/sdn-changes` call from a fresh P2 returns the baseline correctly

### P2 changes (after P1 ships)
- [ ] `update_service._sync_sdn_vectors_paginated()` — new
- [ ] `update_service._record_sdn_changes()` — new
- [ ] `OFACScreener._enforce_freshness` consults cached `p1_last_scraped_at`
- [ ] Remove local `embed_pending` from production path (keep as dev fallback)

### Deferred (post-Phase C)
- [ ] Shipped threshold defaults (Gap D)
- [ ] Cross-bank embed model version registry (if Gap A drift becomes a concern)
