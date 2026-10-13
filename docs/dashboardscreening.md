# Dashboard Screening — Status, Fixes, Gaps

**Scope:** the OFAC/SDN screening tabs of the P2 CCO dashboard
(`/dashboard/search`, `/dashboard/cases`, `/dashboard/cases/:id`,
`/dashboard/rules-perf`, `/ofac`) and the engine that backs them
(`OFACScreener` in `src/services/validators.py`).

Audience: CCO, compliance ops, and engineering on-call.

Last reviewed: 2026-06-15.

---

## 1. Current status

### Deployment context
- P2 is **one bank per ECS service** — no `client_id` filter is
  needed on the screening queries because each container only sees its
  own bank's data.
- OFAC SDN data flows P1 → P2 via `update_service.py` daily pull. P2
  reads `ofac_sdn_entries` (canonical rows) and `ofac_sdn_vectors`
  (one row per name variant, with optional pgvector embedding).
- Two engine modes live behind `OFAC_SCREENER_MODE`:
  - `legacy` — `SequenceMatcher` ratio against full SDN list (in-memory).
  - `vector` — pgvector ANN + pg_trgm + tsvector hybrid (rolled out
    via migrations 021/022; embedding backfill required before HNSW).

### What works end-to-end today
| Capability | Where it lives | State |
|---|---|---|
| Screen a party from the dashboard | `POST /api/v1/dashboard/screening/search` → `OFACScreener.screen()` | ✅ writes `screening_events` audit row; opens `screening_cases` row on REVIEW/HOLD |
| Case list, filter, paginate | `GET /screening/cases` | ✅ |
| Case detail + decision history | `GET /screening/cases/{id}` | ✅ |
| Record a decision (Pass/Review/Escalate) | `POST /screening/cases/{id}/decision` | ✅ role-gated: Pass = CCO (`can_edit`), Review/Escalate = Analyst+ (`can_screen`) |
| Rules performance & alert volume | `GET /screening/rules-perf` | ✅ |
| Vector intelligence (subscore distribution) | `GET /screening/vector-intel` | ✅ Admin write / CRO read |
| OFAC engine health | `GET /dashboard/ofac-health` | ✅ |

### Role matrix (from `dashboard/src/App.jsx`)
| Role | `canScreen` | `canEdit` (Pass) | `canApprove` (rules-perf) | `canGovernModel` (edit weights) |
|---|---|---|---|---|
| Viewer   | ✗ | ✗ | ✗ | ✗ |
| Analyst  | ✓ | ✗ | ✗ | ✗ |
| CCO      | ✓ | ✓ | ✓ | ✗ |
| CRO      | ✓ | ✗ | ✓ | ✗ |
| Admin    | ✓ | ✓ | ✓ | ✓ |

### Decision-history example (from prod-like testbank)
```
2026-06-06 18:50:05   dev                   HOLD  100  legacy   Kim Jong Un
2026-06-06 18:53:11   dev@local             HOLD  100  legacy   Kim Jong Un
2026-06-06 18:56:50   admin@testbank.dev    HOLD  100  legacy   Kim Jong Un
Resolved by admin@testbank.dev on 2026-06-15 19:19:34
```
This is the workflow the role split enforces: multiple analysts flag
HOLD; CCO/Admin closes via Pass override.

---

## 2. What was fixed (this session, 2026-06-15)

### Fix 1 — OFAC embedding-coverage denominator
**File:** `src/api/dashboard_api.py:306-308`

`/ofac-health` reported `embedding_coverage_pct = embedded /
variants_active`, but `embedded` counted *all* rows with a non-null
embedding, including tombstoned/inactive variants. If the loader
doesn't null embeddings on N-strikes tombstone (it doesn't), the ratio
could exceed 100%.

Now both numerator and denominator filter on `is_active = TRUE`. The
`vector_mode_ready` flag (≥99% coverage) is now trustworthy.

### Fix 2 — SdnSearch decision buttons POST directly
**Files:**
`src/services/validators.py:94`, `:101-110`, `:351-371`, `:569-732`;
`src/api/dashboard_api.py:1276-1283`;
`dashboard/src/pages/SdnSearch.jsx`

Previously, the Pass/Review/Escalate buttons on a fresh search
**didn't POST**. They redirected the analyst to the Cases list to find
the just-created case. Result: audit-trail closure depended on a
manual hunt; many flagged screenings stayed open.

What changed:
- `ValidationResult` now carries a `case_id` field alongside
  `screening_id`.
- `_write_audit()` returns `(screening_id, case_id)` from both the
  existing-case branch and the new-case insert branch.
- `POST /screening/search` response now includes the `case_id`.
- The three buttons call `POST /cases/{case_id}/decision` directly.
  Pass is disabled in the UI for non-CCO and rejected server-side at
  `dashboard_api.py:1446` (defense in depth).
- After a decision posts, the page shows the recorded action, the new
  case status, and the actor — with a link to the case detail page.
- The Cases-list redirect remains only as a fallback if `case_id` is
  somehow missing.

### Fix 3 — `_RULES_INVENTORY` promoted to DB
**Files:** new `alembic/versions/034_add_screening_rules.py`;
`src/api/dashboard_api.py:1489-1535`

The four OFAC screening rules shown on `/dashboard/rules-perf` were
hardcoded in a Python list. Any add/rename/reweight required a code
deploy; compliance had no visibility or audit on changes.

New table `screening_rules`:
```
rule_id      VARCHAR(64) PRIMARY KEY
name         TEXT NOT NULL
description  TEXT
weight       NUMERIC(4,2) NOT NULL DEFAULT 1.0    -- CHECK 0..10
version      INTEGER NOT NULL DEFAULT 1
is_active    BOOLEAN NOT NULL DEFAULT TRUE
updated_by   VARCHAR(128)
created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()   -- maintained by trigger
```
Seeded with today's four rules (`OFAC-SCREEN-FUZZY-001`,
`OFAC-SCREEN-CLEAN-001`, `OFAC-SCREEN-OWN50-001`,
`OFAC-SCREEN-FRESH-001`). `/rules-perf` reads `WHERE is_active = TRUE`.
FE shape unchanged (`rule_id`, `name`, `weight`), plus new optional
fields (`description`, `version`, `updated_at`).

Apply with: `python -m alembic upgrade head`.

---

## 3. Open gaps — business flow

### G1 — No in-app editor for `screening_rules` (yet)
The table exists and the API reads from it, but there is no admin
endpoint to insert / update / deactivate rows. Compliance still has to
edit via SQL (with `updated_by` captured for audit). A small `PATCH
/screening/rules/{rule_id}` + a `canGovernModel`-gated FE form would
close this.

**Risk:** medium. Acceptable interim because changes are rare and
SQL-with-audit beats redeploy.

### G2 — OFAC weights aren't read at decision time
`screening_rules.weight` is **display-only** today. The actual
PASS/REVIEW/HOLD logic is in `_derive_decision()`
(`validators.py:143-163`) and uses a single `OFAC_HOLD_THRESHOLD` plus
the per-class thresholds being calibrated in SSM (`/fortress/p2/ofac/
threshold/{class}`). Compliance sees a "weight" column they can't
actually tune.

**Risk:** high if not flagged. Either (a) wire the engine to read
weights from `screening_rules`, or (b) re-label the column "policy
weight" and document that scoring is governed by the SSM thresholds.
Recommendation: do (a) for FRESH/CLEAN (cheap, no calibration impact)
and document (b) for FUZZY/OWN50 until calibration is signed off.

### G3 — Pass override has no required-notes / 4-eyes step
`POST /cases/{id}/decision` accepts an optional `notes` field but does
**not** require it on Pass. A CCO can clear a flagged party in one
click with no rationale recorded. Most bank examiners expect a
written justification + dual control on overrides of a HOLD.

**Risk:** high for examination readiness. Two cheap fixes:
1. Server-side: require `notes` (≥20 chars?) when `action == 'pass'`
   and the case's `decision_at_open == 'HOLD'`.
2. Add a `requires_second_approver` flag on `screening_cases` and
   a `co_approver_user_id` column for dual control on HOLD overrides.

### G4 — SdnSearch decision attaches to the most recent open case, not necessarily *this* screening
`_write_audit` (`validators.py:646-672`) attaches an event to an
existing open case if `(external_customer_id, sdn_number)` matches —
or, for anonymous searches, on a 24h `criteria_hash` window. That's
correct for continuous monitoring, but on the dashboard it means: if
an analyst screens "Kim Jong Un" twice in 30 minutes and the first one
opened a case, the second screen's "Pass" closes the *same* case.
That's actually the desired behavior, but it isn't documented in the
UI — the analyst sees their score and might think they're working a
fresh case.

**Risk:** low (correct behavior, surprising UX). Mitigation: surface
"This screen is attached to case CASE-XXXX (opened 2026-06-06)" in the
search result panel.

### G5 — `screening_cases` has no `client_id` / `bank_id`
Because of "one bank per container" this is fine **today**. If we ever
collapse banks into a shared P2 (cost optimization for small pilots),
the lack of a tenant column is a hard blocker — not just a filter
gap. Mark this explicitly so it doesn't get overlooked in any future
multi-tenant refactor.

**Risk:** low now, blocking later. Action: add a memory + ADR
referencing the assumption so any future "let's share the P2 cluster"
discussion surfaces this.

### G6 — Rules performance metrics conflate engine modes
`/rules-perf` totals (`alerts`, `tp`, `fp`, `precision`) are computed
across **all** `screening_cases` regardless of `engine_mode`. During
the legacy → vector A/B (one of the open action items), precision
will swing for reasons unrelated to rule quality.

**Risk:** medium during rollout. Add a `?engine_mode=legacy|vector`
query param to `/rules-perf` so compliance can compare.

---

## 4. Open gaps — data

### D1 — `ofac_sdn_vectors.embedding` not nulled on tombstone
`ofac_sdn_loader.py` flips `is_active=FALSE` on the N-strikes
tombstone path but leaves the embedding in place. This is why fix #1
(embedding-coverage denominator) was a real bug — the data is
inconsistent. Acceptable for now (embedding is space, not signal, and
HNSW only indexes active rows if the index has a `WHERE` predicate —
**check this**), but should be cleaned up in the next loader pass.

**Action:** verify `idx_ofac_sdn_vectors_embedding_hnsw` (migration
022) has `WHERE is_active=TRUE`. If not, ANN queries are scanning
tombstoned vectors.

### D2 — `screening_cases.reviewer_action` enum is enforced by CHECK, not by FK to rules
`reviewer_action` lives in a `CHECK (... IN
('cleared_false_positive','confirmed_true_positive','escalated',
'needs_more_info'))` constraint (migration 028). It doesn't reference
`screening_rules.rule_id`, so a future rule change to "actions
inventory" requires a CHECK migration, not a row insert. Same code-
deploy problem as G1 but harder to spot.

**Risk:** low (these four actions are stable per the API contract).
Flag if compliance asks for a fifth action ("escalated to OFAC",
"closed without action", etc.).

### D3 — No `matched_rule_id` on `screening_cases`
`screening_cases` records `decision_at_open` and the matched SDN
(`sdn_number`, `matched_name`) but **not which rule fired** (fuzzy,
vector, ownership, staleness). `/rules-perf` therefore can't actually
attribute TP/FP to a specific rule — the per-rule precision table
displayed in the FE is misleading: the totals are global.

**Risk:** medium. Examiners may ask "what's the precision of your
fuzzy-match rule vs your ownership rule?" and we can't answer.

**Fix sketch:** add `matched_rule_id VARCHAR(64) REFERENCES
screening_rules(rule_id)` (nullable for backfill); have the screener
record which rule fired highest. Then `/rules-perf` joins on that
column.

### D4 — `decision_at_open` is captured but never compared to final outcome
We log the decision at the moment a case opened (REVIEW or HOLD) but
the `screening_cases` row never gets a `final_decision` column. If a
HOLD is later Pass'd by CCO, the analytics show
`reviewer_action='cleared_false_positive'` — but the original
`decision_at_open` is what alert volume was sized against.

**Risk:** low (the API contract is consistent), but worth a column to
make the bookkeeping legible: `final_decision VARCHAR(8)` set on
resolve.

### D5 — SDN data freshness isn't surfaced to the analyst
`OFACScreener` knows `sdn_data_age_hours` (logged into
`screening_events`) and bumps the decision if data is stale
(`_SDN_STALENESS_SOFT_HOURS`). The dashboard search result shows the
final decision and score but **doesn't surface freshness**. An
analyst can't tell "this is REVIEW because of low confidence" from
"this is REVIEW because the SDN list is 30 hours old."

**Risk:** medium for explainability. Add a line: "SDN data refreshed
N hours ago — staleness bumped decision: yes/no".

---

## 5. Recommended next actions (priority order)

1. **G3 (require notes on HOLD→Pass)** — small server-side change,
   high examination value. Half a day.
2. **D3 (record `matched_rule_id`)** — unlocks meaningful per-rule
   precision. ~1 day (schema + screener + rules-perf join).
3. **G2 (clarify what `weight` does)** — either wire it through or
   relabel; do not leave compliance confused. ~half a day either way.
4. **G6 (engine_mode filter on /rules-perf)** — needed before the
   legacy→vector A/B in the OFAC SDN rollout list. ~2 hours.
5. **G1 (admin CRUD for `screening_rules`)** — convenience, can wait
   until the first rule edit request comes in.
6. **D1 (HNSW index predicate verification)** — 15 minutes of
   `\d+ ofac_sdn_vectors` and possibly a follow-up migration.
7. **D5 (surface freshness on search result)** — UX polish; bundle
   with the next dashboard change.

---

## 6. Related references
- Audit: `docs/runbooks/ofac-screening-api-contract.md`
- Engine rollout plan: project memory
  `project_ofac_sdn_vector_rollout.md`
- Calibration: `docs/runbooks/ofac-calibration-gold-set.md`
- Schema: migrations 021, 022, 028, 030, 031, 032, 034
- Constants: `src/services/compliance_rule_resolver.py` (per-class
  thresholds — keep in sync with SSM `/fortress/p2/ofac/threshold/*`)
