# OFAC SDN Ingestion — Pipeline 1 → Pipeline 2

End-to-end trace of how a Treasury SDN entry moves from
`sanctionslistservice.ofac.treas.gov` into P1 storage, gets embedded, syncs to
each pilot bank's P2 mirror, and lands in the OFAC screener at wire time.

**Scope:** ingestion + storage + sync. For screening semantics see
`ofac-screening-api-contract.md`; for threshold calibration see
`ofac-calibration-gold-set.md`; for the manual analyst UI see
`sdn-search-manual-screening.md`.

**Schema sources.** Every DDL block below was extracted from the alembic
migration that created it (`alembic/versions/007_*`, `021_*`, `022_*`,
`028_*`, `034_*`, `042_*`) and cross-checked against the live P1 schema in
`fortress_dev`. Column defaults and constraints match `\d <table>` output as
of migration head `043`. If you change a table, update this doc in the same
PR.

---

## 1. Upstream source

Treasury publishes the SDN list in several formats. Fortress uses **only**
`SDN_ADVANCED.XML` — the CSV and basic `SDN.XML` omit alias names, addresses,
and ID numbers, so they are not screening-compliant.

- URL: `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML`
- Namespace: `.../ADVANCED_XML` (element lookups must be namespace-qualified)
- Update cadence: daily, typically 2–4 PM ET
- HTML index at `https://sanctionslist.ofac.treas.gov/Home/SdnList` is **not**
  scraped — it wraps the same XML and adds a JS gate.

Refs: `src/connectors/regulatory/usa/ofac_sdn.py:1–26`.

---

## 2. Pipeline 1 — scrape → structured store

### 2.1 Connector

`OfacSdnConnector` (`src/connectors/regulatory/usa/ofac_sdn.py:45–567`) is a
`RegulatoryConnector` subclass. It:

1. GETs `SDN_ADVANCED.XML` with `User-Agent: TextMind-Compliance/1.0`.
2. Streams the raw XML to S3 for audit (see 2.2).
3. Parses each `<sdnEntry>` — namespace-qualified — into a dict with
   `sdn_number`, `entity_name`, `entity_type`, `programs[]`, `aliases[]`,
   `addresses[]`, `id_numbers[]`, `remarks`, `country`, `nationality`.
4. Hands the list to `ofac_sdn_loader.py` for canonical upsert.

Scheduling: driven by `ScraperScheduler` (`src/services/scraper_scheduler.py`)
on a `SCRAPER_INTERVAL_HOURS` loop (default 24h). Set to `0` on ECS to disable
the in-process daemon and let EventBridge / one-shot `run-task` fire the
scrape.

### 2.2 Raw XML archive (S3)

Every successful pull is gzipped and written KMS-encrypted to:

```
s3://<scraper-chunks-bucket>/sdn_xml_raw/{YYYY}/{MM}/{DD}/sdn_advanced_{unix_ts}.xml.gz
```

- `ContentEncoding: gzip`
- `ServerSideEncryption: aws:kms`
- Object metadata carries the source `Last-Modified` header for chain-of-custody.

This raw archive is the authoritative replay source — if the loader ever
corrupts a canonical row, we re-parse from S3 rather than re-hitting Treasury.

Refs: `src/connectors/regulatory/usa/ofac_sdn.py:266–298`.

### 2.3 Canonical table — `ofac_sdn_entries`

One row per SDN number. Populated by `OFACSDNLoader.upsert_entries()`
(`src/services/ofac_sdn_loader.py`).

DDL (from `src/models/database.py:123–142` + migration `007_add_ofac_country.py`):

```sql
CREATE TABLE ofac_sdn_entries (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sdn_number        VARCHAR(50)  NOT NULL UNIQUE,   -- Treasury stable ID
    entity_name       VARCHAR(500) NOT NULL,
    entity_type       VARCHAR(50),                    -- individual|entity|vessel|aircraft
    programs          JSON DEFAULT '[]',              -- e.g., ["IRAN","SDGT"]
    aliases           JSON DEFAULT '[]',              -- denormalised copy for snapshots
    addresses         JSON DEFAULT '[]',              -- full address blocks
    id_numbers        JSON DEFAULT '[]',              -- passports, IMO, tax IDs
    remarks           TEXT,
    is_active         BOOLEAN DEFAULT TRUE,           -- flipped by N-strikes tombstone
    source_updated_at TIMESTAMP,                      -- XML publication date
    created_at        TIMESTAMP DEFAULT now(),
    updated_at        TIMESTAMP DEFAULT now(),
    country           VARCHAR(100),                   -- migration 007
    nationality       VARCHAR(100)                    -- migration 007
);

CREATE UNIQUE INDEX ix_ofac_sdn_entries_sdn_number ON ofac_sdn_entries (sdn_number);
CREATE INDEX        ix_ofac_sdn_entries_entity_name ON ofac_sdn_entries (entity_name);
CREATE INDEX        idx_sdn_country                 ON ofac_sdn_entries (country);  -- migration 007
```

Note: `created_at` / `updated_at` are `TIMESTAMP` (naïve) here — a deliberate
inconsistency with the newer TIMESTAMPTZ columns on `ofac_sdn_vectors`. When
comparing timestamps across the two tables, coerce.

**Integrity gate.** If a daily pull's row count drops >2% vs the previous
successful load, the loader **aborts before deactivation**. A single corrupt
Treasury response must not silently unblock a real SDN.

**N-strikes soft-deactivation.** An entry missing from the current pull has
its `missed_pulls` counter incremented. After
`_TOMBSTONE_AFTER_MISSES` (currently 2) consecutive misses it is flipped to
`is_active=false` with `deactivated_at=now()` — never hard-deleted. Reappearance
resets the counter and re-activates.

Refs: model `src/models/database.py:123–142`; loader
`src/services/ofac_sdn_loader.py` (integrity gate ~L138–160, N-strikes
~L271–352).

### 2.4 Vector table — `ofac_sdn_vectors`

**One row per name variant** (primary + every alias). This is the table the
runtime screener actually queries; `ofac_sdn_entries` is only used for
snapshotting and dashboard lookups.

DDL (migrations `021_ofac_sdn_vectors.py`, `022_ofac_sdn_hnsw.py`; ORM
`src/models/database.py:144–181`):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE ofac_sdn_vectors (
    id                BIGSERIAL PRIMARY KEY,
    sdn_number        VARCHAR(50)  NOT NULL,           -- ref → ofac_sdn_entries.sdn_number
    name_variant      TEXT         NOT NULL,           -- raw form ("I.P. Volkov")
    name_normalized   TEXT         NOT NULL,           -- NFD + uppercase + punct-strip
    is_primary        BOOLEAN      NOT NULL DEFAULT FALSE,
    entity_type       VARCHAR(50),                     -- denormalised from parent
    programs          JSON         DEFAULT '[]',       -- denormalised
    country           VARCHAR(100),                    -- denormalised
    nationality       VARCHAR(100),                    -- denormalised
    tsv               TSVECTOR,                        -- auto-maintained (trigger below)
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    missed_pulls      INTEGER      NOT NULL DEFAULT 0, -- N-strikes counter
    last_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deactivated_at    TIMESTAMPTZ,
    embedded_at       TIMESTAMPTZ,                     -- set by embed_sdn_variants
    source_updated_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    embedding         vector(1024),                    -- Titan v2, unit-normalised. NULL until embedder runs.
    CONSTRAINT uq_sdn_vectors_sdn_norm UNIQUE (sdn_number, name_normalized)
);

CREATE INDEX ix_sdn_vectors_sdn_number ON ofac_sdn_vectors (sdn_number);
CREATE INDEX ix_sdn_vectors_active     ON ofac_sdn_vectors (sdn_number) WHERE is_active = TRUE;
CREATE INDEX ix_sdn_vectors_trgm       ON ofac_sdn_vectors USING GIN (name_normalized gin_trgm_ops);
CREATE INDEX ix_sdn_vectors_tsv        ON ofac_sdn_vectors USING GIN (tsv);
CREATE INDEX ix_sdn_vectors_embedding  ON ofac_sdn_vectors                    -- migration 022
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- tsv trigger — simple config, no stemming (proper nouns)
CREATE FUNCTION ofac_sdn_vectors_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('simple', NEW.name_normalized);
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trig_ofac_sdn_vectors_tsv
    BEFORE INSERT OR UPDATE OF name_normalized ON ofac_sdn_vectors
    FOR EACH ROW EXECUTE FUNCTION ofac_sdn_vectors_tsv_trigger();
```

Index notes:
- `ix_sdn_vectors_active` is **partial** — the screener always filters
  `is_active = TRUE`, so the tombstoned rows stay out of the hot scan path.
- `ix_sdn_vectors_trgm` is **only used with the `%` operator** — bare
  `similarity()` calls will NOT hit the GIN index. The screener query uses
  `name_normalized %% :q` and sets `pg_trgm.similarity_threshold` per-request.
- `ix_sdn_vectors_hnsw` is built **CONCURRENTLY** in migration 022, and must
  run **after** the initial embed backfill — HNSW on an empty table then
  incremental per-row insert is orders of magnitude slower than one-shot
  bulk-build. Tune `hnsw.ef_search` per-session at screen time
  (`SET LOCAL hnsw.ef_search = 100`).

Population: `OFACSDNLoader.upsert_sdn_variants()` does a single
`INSERT ... ON CONFLICT (sdn_number, name_normalized) DO UPDATE ...`. It
**never** touches the `embedding` column — new/reactivated rows land with
`embedding IS NULL` and get picked up by the embedder later.

Refs: migration `alembic/versions/021_ofac_sdn_vectors.py`; HNSW migration
`022_ofac_sdn_hnsw.py`; loader `src/services/ofac_sdn_loader.py:165–246`.

### 2.5 Name normalisation — must stay bit-identical across pipelines

`normalize_name()` (`src/services/ofac_sdn_loader.py:58–71`) is the single
source of truth. Steps:

1. NFD-decompose accents.
2. Strip combining marks (`Chó` → `Cho`).
3. Uppercase.
4. Strip intra-syllable marks (`'`, backtick, smart quotes — matters for
   transliterations like `Ch'ol` → `CHOL`).
5. Strip other punctuation.
6. Collapse whitespace.

P2's `OFACScreener._normalise_name()` (`src/services/validators.py:369–383`)
**must** produce byte-identical output. Divergence silently zeros out matches.
See memory `feedback_ofac_normalize_pair`.

### 2.6 Embedding worker

`src/jobs/embed_sdn_variants.py` — standalone ECS one-shot task, not inline
in the scraper.

- Model: `amazon.titan-embed-text-v2:0`, 1024-dim, `normalize=true`
- Query: `SELECT ... WHERE embedding IS NULL AND is_active = TRUE`
- Concurrency: 5 (env `SDN_EMBED_CONCURRENCY`)
- Commit granularity: every 50 rows — crash-safe restart
- Sets `embedded_at = now()`; leaves `embedding` non-null

Run cadence: fired after each scrape completes and any time
`variants_embedded / variants_active < 1.0` (see `/sync/sdn-health` below).

### 2.7 Change records

Every new / reactivated / removed variant emits a row into
`regulatory_change_records`:

| Field | Value |
|---|---|
| `change_ref` | `OFAC-ADD-<hash>` / `OFAC-REACT-<hash>` / `OFAC-DEACT-<hash>` |
| `rule_id` | `OFAC-SDN-<sdn_number>` |
| `change_type` | `added` \| `reactivated` \| `removed` |
| `change_summary` | Human sentence — "SDN added: RIHS — entity, programs: IRAN-EO13902" |
| `semantic_diff` | Structured JSON diff (dashboard rendering) |
| `status` | `detected` |
| `detected_at` | tz-aware timestamp |

These drive both the CCO Regulatory Changes panel and the P2 rescreen loop
(§3.3).

Refs: `src/services/ofac_sdn_loader.py:565–615`.

---

## 3. Pipeline 1 → Pipeline 2 sync

P2 keeps a **full mirror** of both `ofac_sdn_entries` and `ofac_sdn_vectors`
in each bank's Aurora. No per-bank customisation of the SDN list — the mirror
is byte-for-byte the P1 snapshot at sync time. The reason we mirror instead of
proxy: the screener runs on the hot wire path and cannot afford a network hop
per wire.

### 3.1 Sync endpoints (P1 side)

All defined in `src/api/sync_api.py:610–969`. All require sync HMAC auth.

| Endpoint | Purpose |
|---|---|
| `GET /sync/ofac-sdn` | Paginated dump of `ofac_sdn_entries` (page/page_size cursor). |
| `GET /sync/sdn-vectors` | Paginated dump of `ofac_sdn_vectors` **with pre-computed embeddings**. `embedded_only=true` (default) skips `embedding IS NULL` rows. `since=<ISO8601>` returns only rows with `embedded_at >= since` for refresh cycles. |
| `GET /sync/sdn-changes` | Keyset-paginated lifecycle deltas. Cursor is `(detected_at, change_ref)` — necessary so backfills sharing a timestamp paginate correctly. |
| `GET /sync/sdn-health` | P1 telemetry: `p1_last_scraped_at`, `hours_since_last_scrape`, `entries_active`, `variants_active`, `variants_embedded`, `embed_coverage_pct`. |

### 3.2 P2 sync path

`UpdateService._sync_ofac_sdn()`
(`src/services/update_service.py:605–767`) runs on every sync cycle.

1. **Entries** — always paginated full refresh via `/sync/ofac-sdn` into local
   `ofac_sdn_entries`.
2. **Vectors** — preferred path calls `/sync/sdn-vectors` and writes rows
   (embedding included) into local `ofac_sdn_vectors`. Fallback (P1 returns
   404, i.e., old P1 build): `populate_variants()` locally from
   `ofac_sdn_entries`, then `embed_pending()` locally via Titan.
3. **Change log** — `/sync/sdn-changes` seeds
   `regulatory_change_records` on the P2 side for the CCO dashboard.
4. **Health** — `/sync/sdn-health` value cached on P2 so the screener can
   enforce staleness (§4.3).

After each successful sync, `_refresh_vector_index()` runs `ANALYZE
ofac_sdn_vectors` so the pgvector planner has current stats.

### 3.3 Continuous-monitoring rescreen

Lifecycle deltas pulled via `/sync/sdn-changes` feed a background task that
re-runs OFAC screening against the past N days of wires touching any
newly-added or reactivated variant. Removal events do **not** unblock past
holds — those decisions are final and audited.

---

## 4. Pipeline 2 — storage + screener

### 4.1 Screener modes

`OFACScreener` (`src/services/validators.py:386–871`) supports two modes,
selected by env `OFAC_SCREENER_MODE`:

| Mode | Path | Notes |
|---|---|---|
| `legacy` | `_screen_legacy` L483–540 | Fetches whole SDN list, in-memory `difflib.SequenceMatcher`. Primary names only. Single global threshold 0.85. Retained for A/B and rollback. |
| `vector` (default target) | `_screen_vector` L544–647 | GIN trgm candidate filter (`%` operator, `pg_trgm.similarity_threshold = match_threshold - 0.10`) + `tsv @@ plainto_tsquery` fallback. Top-50 candidates re-ranked via rapidfuzz `token_sort_ratio`; optional pgvector cosine re-rank if the caller supplies a query embedding. Screens primary + alias rows. Dedupes to the highest-scoring variant per `sdn_number`. |

### 4.2 Thresholds & config

Global (`src/services/validators.py:34–44`):

- `OFAC_SIMILARITY_THRESHOLD = 0.85` — gate to emit a violation
- `OFAC_HOLD_THRESHOLD = 0.92` — auto-block above; REVIEW below
- `_SDN_STALENESS_SOFT_HOURS = 24` — warn
- `_SDN_STALENESS_HOURS = 36` — refuse to screen (hard cutoff)

Per-class thresholds (individual / entity / vessel / aircraft / alias) are
being computed via `scripts/setup/calibrate_ofac_thresholds.py` and will be
loaded from SSM under `/fortress/p2/ofac/threshold/<class>`. Until then the
screener uses the global 0.85. Runbook: `ofac-calibration-gold-set.md`.

### 4.3 Staleness enforcement

The cached `/sync/sdn-health` value gates screening. If
`hours_since_last_scrape > _SDN_STALENESS_HOURS`, the screener returns a hard
failure — no wire clears against a stale SDN list. If between soft and hard,
the wire clears but the screening event carries a staleness flag surfaced in
the CCO dashboard.

### 4.4 Audit tables (P2 only)

Four P2 tables record OFAC activity — do **not** confuse them.

**4.4.1 `screening_events` — authoritative per-screen audit** (migration 028)

Written by `_write_audit()` on every `OFACScreener.screen()` call. One row per
screen. Holds PII (name, DOB, address in `criteria`) and the full engine
response.

```sql
CREATE TABLE screening_events (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    screening_id          VARCHAR(64)   NOT NULL UNIQUE,
    request_kind          VARCHAR(32)   NOT NULL,       -- CHECK IN customer|transaction|bulk|monitoring|dashboard
    request_group_id      VARCHAR(128),
    external_customer_id  VARCHAR(128),
    party_role            VARCHAR(32),
    party_name            TEXT          NOT NULL,
    party_country         VARCHAR(8),
    criteria              JSONB         NOT NULL,       -- PII: name, DOB, address, id_numbers
    criteria_hash         CHAR(64)      NOT NULL,       -- SHA-256 for idempotency lookups
    decision              VARCHAR(8)    NOT NULL,       -- CHECK IN PASS|REVIEW|HOLD
    final_score           NUMERIC(5,4)  NOT NULL DEFAULT 0,
    match_count           INTEGER       NOT NULL DEFAULT 0,
    engine_mode           VARCHAR(16)   NOT NULL,       -- legacy|vector
    engine_response       JSONB         NOT NULL,       -- full match list + subscores
    sdn_data_age_hours    NUMERIC(7,2),
    latency_ms            NUMERIC(8,2)  NOT NULL DEFAULT 0,
    actor_kind            VARCHAR(16)   NOT NULL,       -- CHECK IN api|dashboard|bulk|monitoring
    actor_user_id         VARCHAR(128),
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX ix_screening_events_customer       ON screening_events (external_customer_id) WHERE external_customer_id IS NOT NULL;
CREATE INDEX ix_screening_events_group          ON screening_events (request_group_id)     WHERE request_group_id IS NOT NULL;
CREATE INDEX ix_screening_events_created_at     ON screening_events (created_at DESC);
CREATE INDEX ix_screening_events_actionable     ON screening_events (decision, created_at DESC) WHERE decision IN ('REVIEW','HOLD');
CREATE INDEX ix_screening_events_criteria_hash  ON screening_events (criteria_hash);
```

Retention: 7yr (configurable per institution), enforced out-of-band by a
sweeper. Append-only by convention — no trigger guard so ops can backfill /
re-anchor during incident response.

**PII boundary.** `screening_events` and `screening_cases` live in P2 Aurora
**only** and MUST NEVER be synced upstream to Fortress Cloud. Migration 028
runs on P1 too (alembic is global) but the tables stay empty there.

**4.4.2 `screening_cases` — workflow row** (migration 028)

Opened when an event's decision is `REVIEW` or `HOLD`. One open case per
`(external_customer_id, sdn_number)` pair enforced by a partial unique index.

```sql
CREATE TABLE screening_cases (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id               VARCHAR(64)   NOT NULL UNIQUE,
    external_customer_id  VARCHAR(128),
    sdn_number            VARCHAR(50),
    matched_name          TEXT,
    status                VARCHAR(16)   NOT NULL DEFAULT 'open',   -- CHECK IN open|in_review|cleared|escalated|blocked|closed
    priority              VARCHAR(8)    NOT NULL,                  -- CHECK IN high|medium
    decision_at_open      VARCHAR(8)    NOT NULL,                  -- CHECK IN REVIEW|HOLD
    assignee_user_id      VARCHAR(128),
    first_event_id        UUID          NOT NULL REFERENCES screening_events(id),
    last_event_id         UUID          NOT NULL REFERENCES screening_events(id),
    event_count           INTEGER       NOT NULL DEFAULT 1,
    reviewer_action       VARCHAR(32),                             -- CHECK IN cleared_false_positive|confirmed_true_positive|escalated|needs_more_info
    reviewer_notes        TEXT,
    reviewer_evidence     JSONB,
    resolved_at           TIMESTAMPTZ,
    resolved_by_user_id   VARCHAR(128),
    matched_rule_id       VARCHAR(64) REFERENCES screening_rules(rule_id) ON DELETE SET NULL,  -- D3
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX        ix_cases_status              ON screening_cases (status, priority, created_at DESC);
CREATE INDEX        ix_cases_assignee            ON screening_cases (assignee_user_id) WHERE assignee_user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_cases_open_customer_sdn   ON screening_cases (external_customer_id, sdn_number)
    WHERE external_customer_id IS NOT NULL AND sdn_number IS NOT NULL
      AND status NOT IN ('cleared','closed');

-- updated_at maintained by trigger screening_cases_touch_updated_at()

CREATE TABLE screening_case_events (
    case_id    UUID NOT NULL REFERENCES screening_cases(id) ON DELETE CASCADE,
    event_id   UUID NOT NULL REFERENCES screening_events(id),
    linked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (case_id, event_id)
);
```

**4.4.3 `screening_rules` — rule inventory** (migration 034)

Source of truth for `/api/v1/dashboard/screening/rules-perf`. Replaces the
hard-coded `_RULES_INVENTORY` — compliance can add / rename / reweight /
deactivate without a code deploy.

```sql
CREATE TABLE screening_rules (
    rule_id     VARCHAR(64) PRIMARY KEY,
    name        TEXT         NOT NULL,
    description TEXT,
    weight      NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    version     INTEGER      NOT NULL DEFAULT 1,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    updated_by  VARCHAR(128),
    created_at  TIMESTAMPTZ  DEFAULT now(),
    updated_at  TIMESTAMPTZ  DEFAULT now()
);
```

Seeded rules: `OFAC-SCREEN-FRESH-001` (freshness), `OFAC-SCREEN-FUZZY-001`
(fuzzy match ≥ threshold), `OFAC-SCREEN-OWN50-001` (50% beneficial-owner
propagation).

**4.4.4 `ofac_validations` — Pillar-4 periodic attestations** (migration 042)

One row per bank per periodic independent OFAC list-validation event. Feeds
Report 2 §6 of the examination evidence bundle.

```sql
CREATE TABLE ofac_validations (
    validation_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_id                VARCHAR(80) NOT NULL,
    performed_on           DATE        NOT NULL,
    performed_by_person_id UUID REFERENCES bank_officers(person_id) ON DELETE SET NULL,
    scope                  TEXT        NOT NULL,       -- e.g., "SDN + non-SDN sanctions lists"
    methodology            TEXT,
    findings_summary       TEXT,
    next_due               DATE,
    attestation_s3_uri     TEXT,                       -- signed PDF in WORM S3
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_ofac_val_bank_perf ON ofac_validations (bank_id, performed_on DESC);
```

The `validation_id UUID` on this row is what tripped the canonical serialiser
bug fixed 2026-06-23 — the exports worker's `_default` handler didn't cover
UUID until then. See memory `feedback_canonical_uuid_serialize`.

**4.4.5 `ofac_screenings` — legacy summary log**

Still written for backwards compatibility. New consumers should read
`screening_events` instead — this table lacks the PII criteria, engine mode,
actor attribution, and freshness field the audit trail needs.

```sql
CREATE TABLE ofac_screenings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action          VARCHAR(20) NOT NULL,   -- clear|block
    match_count     INTEGER DEFAULT 0,
    matched_entries JSON DEFAULT '[]',      -- SDN numbers only — no customer names
    latency_ms      DOUBLE PRECISION,
    created_at      TIMESTAMP DEFAULT now() -- NOT tz-aware (matches ofac_sdn_entries convention)
);
```

Refs: `src/models/database.py:218–227`.

---

## 5. What's stored where — quick reference

| Data | P1 storage | P2 storage |
|---|---|---|
| Raw XML | S3 `sdn_xml_raw/YYYY/MM/DD/*.xml.gz` (KMS) | not mirrored |
| Canonical entries | `ofac_sdn_entries` (20-ish cols, JSON blobs for aliases/addresses/id_numbers) | Full mirror `ofac_sdn_entries` |
| Screening variants + embeddings | `ofac_sdn_vectors` (1 row per name variant, Titan v2 `vector(1024)`, tsvector) | Full mirror `ofac_sdn_vectors`, embeddings copied verbatim from P1 |
| Lifecycle deltas | `regulatory_change_records` (`OFAC-ADD/REACT/DEACT-*`) | Mirror `regulatory_change_records` |
| Per-screen audit (PII) | — | `screening_events` (authoritative) + `screening_case_events` |
| Case workflow | — | `screening_cases` |
| Rule inventory | — | `screening_rules` (migration 034) |
| Legacy summary log | — | `ofac_screenings` (kept for BC) |
| Periodic list attestation | — | `ofac_validations` (migration 042) |
| Scrape provenance | `scrape_runs` (migration 018) | not mirrored |

---

## 6. Failure modes to know

- **Divergent normalisation P1 vs P2** — silently zeros matches. Always change
  `normalize_name` + `_normalise_name` in the same PR.
- **HNSW built on empty table** — orders of magnitude slower to converge.
  Migration 022 must run **after** the first embed backfill.
- **Loader reads a corrupt XML** — integrity gate (>2% row-count drop)
  aborts before deactivation. Alert fires; ops replays from S3.
- **New task-def points at `:latest`** — `make push-p1` only pushes the semver
  tag, so `force-new-deployment` re-pulls old code. See memory
  `feedback_p2_makefile_latest_tag`.
- **Stale SDN in P2** — `_SDN_STALENESS_HOURS=36` hard-cutoff refuses to
  screen. Check `/sync/sdn-health` on the P1 side and P2 UpdateService logs.

---

## 7. Related runbooks

- `ofac-screening-api-contract.md` — request/response shapes for the P2
  screening API
- `ofac-calibration-gold-set.md` — per-class threshold derivation
- `sdn-search-manual-screening.md` — analyst UI, treasury.gov parity
- `sdn-dashboard-completion.md`, `sdn-dashboard-ba-verification.md` — CCO
  dashboard SDN panel
