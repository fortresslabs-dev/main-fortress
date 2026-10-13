# SDN Search — Manual Analyst Screening

**Status:** design, 2026-07-03
**Owners:** P2 dashboard (Agent 4) · validators (Agent 2)
**Applies to:** the SDN Search / Investigation-Case screens in the P2 dashboard
**Related docs (do not duplicate):**
- `ofac-screening-api-contract.md` — backend endpoint contract
- `customer-screen-implementation-plan.md` — `POST /validate/v1/customer-screen` build
- `sdn-dashboard-completion.md` — wider dashboard shell + role-gating
- `ofac-calibration-gold-set.md` — per-class threshold tuning

This document is scoped to the **manual analyst path**: an operator searches a
party against the OFAC SDN list, reviews matches, and records a decision. It is
distinct from the automated wire-screening path (`OFACScreener` gate inside
`validators.py`), which is untouched by anything described here.

---

## 1. Purpose

Give compliance analysts a lookup tool inside the P2 dashboard that:

1. Screens a party (customer, counterparty, wire beneficiary, investigation
   subject) against the OFAC SDN list.
2. Presents matches in a table with the same subscores the engine already
   computes (`trgm / fuzz / vector / final`).
3. Records every screen for audit — who searched for what, when, with which
   filters, and what they decided.
4. Auto-opens a case (or attaches to an existing open case for the same
   `customer_id + sdn_number` pair) whenever the decision is `REVIEW` or
   `HOLD`.
5. Reaches feature parity with the public treasury.gov Sanctions Search
   (https://sanctionssearch.ofac.treas.gov/) so analysts do not need to leave
   the dashboard to double-check a result.

Non-goals in v1: batch CSV screening, Consolidated Sanctions List
(`CONS_ADVANCED.XML`), FinCEN 314(a), multi-jurisdiction lists (EU/UN/HMT).

---

## 2. Users and triggers

Screen is exposed to Cognito groups `Analyst`, `CCO`, `CRO` (see
`sdn-dashboard-completion.md` §1). Decision buttons are role-gated (§5).

Typical triggers:

- KYC officer running a new-customer name at account opening
- Counterparty check on a manual outbound wire
- Investigating an alert escalated from automated screening
- Ad-hoc "does this person appear on the list" from front-office

Every trigger flows through the same UI and writes the same audit row.

---

## 3. Search inputs — parity with treasury.gov

Left-hand column of the search panel. Fields present in v1 are marked "ship";
fields deferred to Phase 2 (address / ID facets) or Phase 3 (Consolidated
list) are labelled explicitly.

| Field | v1 status | Source column | Notes |
|---|---|---|---|
| **Name** (required) | ship | `name_variant` / `name_normalized` | Passes through `normalize_name` from `ofac_sdn_loader.py`. |
| **Country (ISO-2)** | ship | filter, applied post-match | Refines match set. Not part of the scoring engine in v1. |
| **Date of birth** | ship (audit-only) | `screening_events.criteria.dob` | Stored on the audit row; **not** used in score (see §7). |
| **Customer ID** | ship | `external_customer_id` | Ties screening to the bank's customer key; drives case attach. |
| **Type** | ship | `entity_type` | Individual · Entity · Vessel · Aircraft. Dropdown filter. |
| **Program** | ship | `programs` (jsonb) | Multi-select; 90+ OFAC programs. Filter is `programs ?\| ARRAY[:selected]`. |
| **Minimum Name Score** | ship | slider, 0–100 | Default 85 (matches `OFAC_FUZZY_THRESHOLD`). Slider clamps `final_score` filter. |
| **Address · City · State / Province** | Phase 2 | `ofac_sdn_details.address` (new table) | Requires parent-SDN loader extension. |
| **ID # / Digital Currency Address** | Phase 2 | `ofac_sdn_details.identifiers` (new table) | Same. |
| **List** | Phase 3 | `list_source` column | SDN only in v1; Consolidated requires ingesting `CONS_ADVANCED.XML`. |

Deferred fields render disabled in v1 with a "Phase 2" tooltip so the layout
matches treasury.gov and analysts learn where they will land.

---

## 4. Result table columns

Right-hand pane of the search panel. Columns mirror the `MatchDetail` fields
already returned by `OFACScreener.screen()` (`validators.py:41`).

| Column | Source | Meaning |
|---|---|---|
| **SDN** | `ofac_sdn_vectors.sdn_number` | Treasury's authoritative ID; click through opens the treasury.gov detail page. |
| **Matched name** | `name_variant` | The variant that hit — not always the primary. Preserves clue about *why* the row matched. |
| **Programs** | `programs` (jsonb) | Comma-joined; each pill links back to the program's page on the OFAC site. |
| **Country** | `ofac_sdn_details.country` (Phase 2) | Shown as "—" in v1 until parent-record loader ships. |
| **Fuzzy** | `MatchDetail.trgm` × 100 | pg_trgm similarity on `name_normalized`. |
| **Alias** | derived | 0 if `is_primary`, else the fuzz score of the alias hit. |
| **Vector** | `MatchDetail.vector` × 100 | pgvector cosine on Titan v2 embeddings. |
| **Final** | `MatchDetail.final` × 100 | Composite; threshold-gated. Sort default: desc. |

Result set is bounded to the top 25 by `final` score. Empty result set does
**not** short-circuit the audit write — every screen produces a row, including
zero-match ones, because that is itself a compliance decision.

---

## 5. Decision actions

Three buttons appear beneath the result table. Buttons are role-gated and each
writes a decision to the case if one exists (or auto-opens one — see §6).

| Button | Roles | What it does |
|---|---|---|
| **Pass (CCO override)** | `CCO` only | Analyst attests no true match despite a `REVIEW`/`HOLD` decision. Writes `reviewer_action = cleared_false_positive` on the case, sets `status = cleared`, requires a `reviewer_notes` reason. |
| **Send to review** | `Analyst`, `CCO`, `CRO` | Leaves case in `open` status; assigns to a reviewer queue (defaults to `null` for pull-based ops). Writes `reviewer_action = needs_more_info`. |
| **Escalate** | `Analyst`, `CCO`, `CRO` | Sets `status = escalated`, writes `reviewer_action = escalated`, and posts a JIRA ticket (see `jira_client.py`). Escalation is the trigger for SAR-review, not the SAR filing itself. |

All three require a free-text reason (`reviewer_notes` on the case) — cannot
be blank. Audit stores actor identity from the JWT (`actor.user_id` in the
screening request).

---

## 6. Case attach / auto-open

Backed by migration `028_add_screening_events_and_cases.py`. No new schema
needed for v1.

**Rule:** one open case per `(external_customer_id, sdn_number)` pair,
enforced by the partial unique index `uq_cases_open_customer_sdn`.

Flow on a `REVIEW` or `HOLD` decision:

1. Backend upserts against `uq_cases_open_customer_sdn` for the top match.
2. If no open case exists → create one, populate `first_event_id` and
   `last_event_id` with the current screening event.
3. If an open case exists → increment `event_count`, update `last_event_id`,
   append a row to `screening_case_events`.
4. UI banner reads: *"Attached to existing case `CASE-<hex>` (opened
   `<timestamp>`) — any decision here applies to that case."*

`PASS` decisions never auto-open a case. Zero-match screens never auto-open a
case. Both still write to `screening_events`.

Cases move to `cleared` / `escalated` / `blocked` per §5. Once cleared, a new
alert on the same `(customer, sdn)` pair opens a **new** case (the partial
index excludes `status IN ('cleared','closed')`), preserving each cycle as its
own audit unit.

---

## 7. Staleness banner

Shown above the result table. Uses `screening_events.sdn_data_age_hours`
returned by `OFACScreener` (source: max `updated_at` on `ofac_sdn_vectors`).

| Age | Banner colour | Score effect | Trigger |
|---|---|---|---|
| < 24 h | green | none | — |
| 24–48 h | yellow | none | — |
| \> 48 h | red | +5 pts to `final_score` on every match | Fires an on-demand `update_service._sync_ofac_sdn()` in the background. |

Rationale: 48 h is the SLA agreed with pilot compliance ops. The +5 point
bump is a defensive nudge — it makes a borderline match cross threshold rather
than silently pass on stale data.

The banner in the current UI ("SDN list refreshed 4.1 h ago. Within freshness
window — no staleness bump.") reflects the < 24 h case.

---

## 8. Audit — what gets written

Every screen writes one row to `screening_events`:

```
screening_id       SCR-<ulid>
request_kind       'dashboard'
party_name         (as typed)
party_country      (ISO-2, from Country field)
criteria           {name, country, dob, customer_id, filters}
decision           PASS | REVIEW | HOLD
final_score        (0.0000 – 1.0000, engine-native scale)
match_count        (top-25 table row count)
engine_mode        legacy | vector  (from OFAC_SCREENER_MODE)
engine_response    (full matches array with subscores)
sdn_data_age_hours (as displayed in the banner)
latency_ms         (round-trip)
actor_kind         'dashboard'
actor_user_id      (Cognito sub)
created_at         now()
```

`criteria_hash` (SHA-256 of the criteria JSON) is written alongside so ops can
group repeated identical screens (e.g. re-runs during monitoring).

Retention: 7 years (compliance-driven, per migration 028 header). Enforced by
an out-of-band sweeper — this UI does not delete.

Decisions and case state changes are written to `screening_cases` (fields
`reviewer_action`, `reviewer_notes`, `reviewer_evidence`, `resolved_at`,
`resolved_by_user_id`). Every decision therefore leaves two rows: one on
`screening_events` (immutable) and one update on the linked case row.

---

## 9. Parity vs treasury.gov — closure plan

Reference query used for parity testing: `Name = "kim chol"`, all other
fields default. Treasury returns 23 rows across DPRK, DPRK2, DPRK3, DPRK4,
NPWMD. Our v1 returns the same rows (post-021-token-fix + backfill
2026-07-02, commit `7e91505`).

| Facet | Ours (v1) | Treasury | Gap plan |
|---|---|---|---|
| Name search | pg_trgm + tsvector + pgvector composite | fuzz on primary + AKA table | Ours is strictly stronger (variants pre-materialised) |
| Type filter | ship | ship | — |
| Program filter | ship | ship | — |
| Min score slider | ship | ship (`100` default) | Match default is 85 not 100 — configurable, see `OFAC_FUZZY_THRESHOLD`. |
| Country filter | ship (post-filter) | ship (indexed) | Once Phase 2 lands, moves into the SQL predicate. |
| DOB | audit-only | ignored | Phase 2: tiebreaker for individual matches. |
| Address / City / State | not shown | ship | Phase 2 — `ofac_sdn_details` table + parent-record loader. |
| ID # / Digital Currency Address | not shown | ship | Phase 2 — same table, `identifiers` array. |
| List (SDN vs Consolidated) | SDN only | both | Phase 3 — separate loader + `list_source` column. |
| Case attach / audit | ship | n/a | Ours-only capability; not in treasury.gov's remit. |

---

## 10. Ours vs treasury.gov — analyst adoption strategy

The parity table in §9 is facet-by-facet with a closure phase. This section
is the strategic view — where analysts default to today, why, and what makes
the dashboard the more compliant choice. Goal: get every manual OFAC screen
funnelled through our platform rather than through
`https://sanctionssearch.ofac.treas.gov/`.

### 10.1 Capability side-by-side

| Capability | Ours | treasury.gov | Winner |
|---|---|---|---|
| Name matching | pg_trgm + tsvector + pgvector composite; variants pre-materialised | proprietary fuzz + AKA table | **Ours** — stronger recall; catches transliterations (`Ch'o'l` → `Chol`) |
| Explainability | `trgm` / `fuzz` / `alias` / `vector` / `final` shown per match | one score | **Ours** |
| Fields exposed | Name, Country, DOB, Customer ID | Name, Type, Address, City, State, Country, ID#, Digital Currency Address, Program, List, Min Score | **Treasury** |
| Type / Program filter | data present on `ofac_sdn_vectors`, UI not yet wired (Phase 1) | full dropdowns | **Treasury** today · **ours** after Phase 1 |
| List coverage | SDN only | SDN + Consolidated (FSE, NS-PLC, CAPTA, …) | **Treasury** |
| Freshness signal | banner + auto-sync when > 48 h stale | always current | Tie (different mechanism) |
| Audit trail | every screen → `screening_events` (immutable, 7-yr retention) | none | **Ours** — regulator-grade |
| Case memory | auto-attach to open case for `(customer, sdn)` | none | **Ours** |
| Decision recording | Pass (CCO override) / Send to review / Escalate, reason required | none | **Ours** |
| Screening history for a party | queryable across all prior screens; drives risk view | none | **Ours** |
| Integration | hooks into wire screen, KYC, JIRA escalation | export-only | **Ours** |
| Perceived authority | "our tool" | "the government" | **Treasury** (bias, not fact) |

Net: ours is the stronger *compliance tool*; treasury.gov is the stronger
*search interface today*. Analysts default to whichever feels more complete,
so the adoption gap is optical, not technical.

### 10.2 Why analysts still open treasury.gov (and how to fix each)

1. **Field breadth optics.** Treasury's form shows 10+ inputs; ours shows 4.
   Even when analysts never enter Address or ID#, the *presence* of the
   fields signals completeness.
   *Fix:* ship Phase 1 (Type + Program + min-score) this sprint and render
   Phase-2 fields as disabled inputs with a "coming in Phase 2" tooltip so
   the form visually matches treasury.gov.

2. **"Is it really the same data?"** Analysts do not instinctively trust a
   mirror. This is a trust problem, not a data problem.
   *Fix:* on each result row show `Last synced: <hours> h ago from treas.gov`
   and an **Open in treasury.gov** link per SDN number. One-click cross-check
   builds trust faster than any documentation.

3. **Consolidated list gap.** Real hole for a subset of pilot banks that
   screen against FSE / NS-PLC / CAPTA.
   *Fix:* Phase 3 — but only if pilot compliance ops confirm the need. Worth
   asking on the next pilot call, not assuming.

### 10.3 What makes ours indispensable

Every treasury.gov visit is an **undocumented screening**. If a regulator
asks "show me every SDN screening your bank ran in June with the analyst,
the query, the matches, and the decision," the answer is:

```sql
SELECT actor_user_id, party_name, criteria, engine_response,
       decision, final_score, created_at
FROM   screening_events
WHERE  created_at >= '2026-06-01'
  AND  created_at <  '2026-07-01';
```

That query has no equivalent if analysts used treasury.gov — the government
portal keeps no session state, no history, and no audit hook. This is the
single strongest reason to funnel every manual screen through the dashboard,
and it should be the lead pitch in analyst training and CCO onboarding.

### 10.4 Adoption sequencing

1. **Phase 1 (this sprint)** — Type / Program / min-score filters, disabled
   Phase-2 fields with tooltips, **Open in treasury.gov** cross-check link
   on each result row, party screening-history sidebar. Closes the optics
   gap without touching the schema.
2. **Phase 2 (next sprint)** — `ofac_sdn_details` table + Address / City /
   State / Country / ID# facets. See §11 for the shape.
3. **Phase 3 (deferred; gate on compliance-ops confirmation)** —
   Consolidated Sanctions List ingest. See §12.

---

## 11. Phase 2 — parent-record facets (proposed shape)

New table (migration to be assigned):

```sql
CREATE TABLE ofac_sdn_details (
    sdn_number     VARCHAR(50) PRIMARY KEY REFERENCES ofac_sdn_vectors(sdn_number),
    entity_type    VARCHAR(32),
    country        VARCHAR(2),
    address        TEXT[],
    city           TEXT[],
    state_province TEXT[],
    postal_code    TEXT[],
    identifiers    JSONB,      -- [{type, number, country}]
    digital_addr   JSONB,      -- [{chain, address}]
    dob            DATE[],
    place_of_birth TEXT[],
    nationality    VARCHAR(2)[],
    citizenship    VARCHAR(2)[],
    dbas           TEXT[],
    remarks        TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Loader change: `ofac_sdn_loader.py` currently parses only the name-variant
subtree of `SDN_ADVANCED.XML`. Phase 2 adds the parent-record parse in the
same pass — no additional HTTP or S3 cost. Address arrays are because SDN
entries frequently list multiple aliases-of-address.

UI change: enable the disabled Address / City / State / Country / ID # inputs
and add a details drawer opened by clicking an SDN row.

---

## 12. Phase 3 — Consolidated Sanctions List (deferred)

Scoping notes only; not committed. Consolidated adds FSE, PLC, NS-PLC, 13599,
CAPTA. Data source: `CONS_ADVANCED.XML` from
`https://sanctionslistservice.ofac.treas.gov`. Ingest strategy: separate table
`ofac_consolidated_vectors` with identical shape to `ofac_sdn_vectors`;
`list_source` column keeps them queryable together with a UNION in the search
API. Decision required from compliance ops: do pilot banks screen against
Consolidated today, or only SDN?

---

## 13. Out of scope for this document

- Automated wire screening — see `validators.py::OFACScreener`.
- Batch CSV upload — Phase 4 candidate; ties into `bulk-screen` API.
- Multi-jurisdictional lists (EU, UN, HMT) — future.
- Sanctions on cryptocurrency wallets beyond OFAC's own `Digital Currency
  Address` field — needs its own doc.
- Fuzzy-match retraining / recalibration — see `ofac-calibration-gold-set.md`.
