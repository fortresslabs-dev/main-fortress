# SDN Screening Dashboard — Completion TODO

**Status:** scoped, not started
**Owner:** Agent 4 (P2 sync + workflow)
**Last updated:** 2026-06-06

This document tracks the work needed to ship the two surfaces over the OFAC/SDN
screening engine:

1. **Production API** — `POST /api/v1/screen/sdn` called by bank KYC / wire systems.
2. **Compliance dashboard** — 5 React screens for Analyst / CCO / CRO / Admin.

Both surfaces share one engine call, one audit trail, and one decision enum.

---

## 0. Current state (what exists)

### Backend
| Component | File | State |
|---|---|---|
| Engine | `src/services/validators.py` `OFACScreener.screen()` | Feature-complete: trgm + fuzz + vector + freshness. Returns `action="block"\|"clear"`. |
| Match subscores | `MatchDetail` (validators.py:41) | Exposes `trgm / fuzz / vector / final` per match. |
| Existing API | `src/api/validation_api.py` `POST /validate/ofac-screen` | Works, **no `screening_id`**, **no audit write**. |
| Audit schema | migration `028_add_screening_events_and_cases.py` | Tables exist (`screening_events`, `screening_cases`, `screening_case_events`), **no SA models, no writes**. |
| Engine health | `src/api/dashboard_api.py` `GET /api/v1/dashboard/ofac-health` | Live — used by current `OfacHealth.jsx`. |

### Frontend
| Component | File | State |
|---|---|---|
| SPA shell | `dashboard/src/App.jsx` | Cognito Amplify auth; role-gating only recognizes `CCO / CRO / admin`. |
| Navbar | `dashboard/src/components/Navbar.jsx` | 8 tabs. No SDN Search / Cases / Rules-Perf / Vector / Executive. |
| API client | `dashboard/src/services/api.js` | `dashboardApi` only — no `screeningApi`. |
| Existing OFAC page | `dashboard/src/pages/OfacHealth.jsx` | Engine health & coverage. **Not** a search UI. |

### Auth
| Component | File | State |
|---|---|---|
| Cognito groups | `infra/cloudformation/pipeline2-dashboard.yml:131-150` | Only `CCO` (Prec 1) / `CRO` (Prec 2) / `CIO` (Prec 3). |
| Backend deps | `src/api/auth.py` | `require_cco`, `require_approver` only. No `require_analyst`, no `require_admin`. |
| Trial deploy | `infra/scripts/deploy-p2.sh:74,366-373` | Defaults `BANK_DOMAIN=trial.invalid`; creates `cco@ / cro@ / cio@`. No Analyst, no Admin. |

---

## 1. Persona → screen → group map

| Screen | Default users | Cognito groups allowed |
|---|---|---|
| SDN Search (1) | Frontline compliance officer | `Analyst`, `CCO`, `CRO` |
| Investigation / Case (2) | Officer + CCO sign-off | `Analyst`, `CCO`, `CRO` |
| Rules Performance (3) | CCO + CRO | `CCO`, `CRO` |
| Executive Today (5) | CCO (Board view) | `CCO` |
| Vector Intelligence (4) | Admin / Model governance | `Admin` (write), `CRO` (read-only) |

**New groups required:** `Analyst` (Precedence 4), `Admin` (Precedence 5).

**Default trial users (5 total):**
- `cco@testbank.local`
- `cro@testbank.local`
- `cio@testbank.local`
- `analyst@testbank.local`
- `admin@testbank.local`

Domain choice rationale: `.local` is RFC-safe and never routable. `.com` reads
nicer in demos but typos send mail to a real registrant. Real bank deploys
already override `BANK_DOMAIN` so this default only affects `TRIAL_MODE=true`.

---

## 2. Decision enum normalization

Engine returns `action="block"|"clear"`. Spec + audit schema require
`PASS|REVIEW|HOLD`.

| Engine outcome | New decision |
|---|---|
| No matches, freshness OK | `PASS` |
| Matches with final < HOLD threshold | `REVIEW` |
| Matches with final ≥ HOLD threshold OR `OFAC_BLOCK` violation | `HOLD` |
| Freshness stale (>24h) | bumped one level (PASS→REVIEW, REVIEW→HOLD) |

Add `ValidationResult.decision` alongside the existing `action`. Deprecate
`action` after one P2 release; callers move to `decision`.

---

## 3. Phase plan (in order)

### Phase 1 — Auth groundwork (1 commit, needs CFN deploy)

- [ ] **1.1** `infra/cloudformation/pipeline2-dashboard.yml` — add `GroupAnalyst`
      (Precedence 4) and `GroupAdmin` (Precedence 5) alongside CCO/CRO/CIO.
- [ ] **1.2** `src/api/auth.py` — extend `CurrentUser` with `can_screen`
      (Analyst/CCO/CRO), `can_govern_model` (Admin/CRO). Add `require_analyst`,
      `require_admin` Depends shims.
- [ ] **1.3** `infra/scripts/deploy-p2.sh:74` — `BANK_DOMAIN` default
      `trial.invalid` → `testbank.local`.
- [ ] **1.4** `deploy-p2.sh:366-373` — add `ANALYST_EMAIL` + `ADMIN_EMAIL`,
      `create_cognito_user` for each.
- [ ] **1.5** `dashboard/src/App.jsx:38-41` — recognize `Analyst` + `Admin`
      groups; extend `ctx` with `canScreen`, `canGovernModel`.
- [ ] **1.6** `docs/setup/operations.md` — update trial-user table (5 rows).

**Verify:** `aws cognito-idp list-groups --user-pool-id $POOL` shows 5 groups.

### Phase 2 — Engine + audit (no UI yet)

- [ ] **2.1** `src/services/validators.py:280,385` — return
      `decision: PASS|REVIEW|HOLD` (keep `action` for one release).
- [ ] **2.2** `src/models/database.py` — add SA models for
      `ScreeningEvent`, `ScreeningCase`, `ScreeningCaseEvent`.
- [ ] **2.3** `OFACScreener.screen()` — after each call write a
      `screening_events` row: `screening_id` = UUID, `criteria_hash` = SHA-256
      of normalized `{name, country, dob?, address?}`, `engine_response` =
      full JSONB, `actor_kind` = `'api' | 'dashboard'`.
- [ ] **2.4** On `decision in (REVIEW, HOLD)`: upsert `screening_cases` by
      `criteria_hash` (collapse duplicate hits in 24h window). Always insert
      a `screening_case_events` link row.
- [ ] **2.5** Update `ValidationResponse` pydantic to include `screening_id`
      + `decision`.

**Verify:** unit test — three POSTs to `/validate/ofac-screen` for the same
party in 24h produce 3 events, 1 case.

### Phase 3 — Production API

- [ ] **3.1** New `src/api/screening_api.py` — `POST /api/v1/screen/sdn`,
      `GET /api/v1/screen/sdn/{screening_id}`.
- [ ] **3.2** Auth: dual — Cognito Bearer (`require_analyst`) **or**
      HMAC header (`sync_auth.verify_hmac_dep`) — bank backends call via key.
- [ ] **3.3** Idempotency: return cached result if `criteria_hash` seen in
      last 24h AND SDN data hasn't refreshed since. Add `X-Idempotent: true`.
- [ ] **3.4** Register router in `src/main_pipeline2.py`.
- [ ] **3.5** `docs/runbooks/ofac-screening-api-contract.md` — request/response
      schema, idempotency rules, decision semantics, error codes.

**Verify:** curl PASS, REVIEW, HOLD cases; `select count(*) from
screening_events` matches.

### Phase 4 — Dashboard JSON endpoints

All under `src/api/dashboard_api.py`, prefix `/api/v1/dashboard/screening`.

- [ ] **4.1** `POST /search` — Officer search form. Body: `{name, country,
      dob?, sdn_number?}`. Calls engine, writes audit, returns
      `{screening_id, decision, matches[]}`. Auth: `require_analyst`.
- [ ] **4.2** `GET /cases` — list with filters `?status=open|closed&priority=&
      decision=&assignee=&q=&page=`. Auth: `require_analyst`.
- [ ] **4.3** `GET /cases/{case_id}` — full case + linked events + matched
      entity detail. Auth: `require_analyst`.
- [ ] **4.4** `POST /cases/{case_id}/decision` — body `{action: pass|review|
      escalate, notes, evidence?}`. Updates case + appends event. Auth:
      `require_analyst` for review/escalate, `require_cco` for pass-with-
      override.
- [ ] **4.5** `GET /rules-perf` — rule inventory + alerts/TP/FP counts from
      `screening_case_events.reviewer_action`. Auth: `require_approver`.
- [ ] **4.6** `GET /vector-intel` — similarity buckets, top name variants,
      recall metrics. Auth: `require_admin`.
- [ ] **4.7** `GET /executive-today` — today's screenings, alerts, open
      cases, last-list-update. Auth: `require_cco`.

### Phase 5 — Frontend pages

- [ ] **5.1** `dashboard/src/services/api.js` — add `screeningApi` with
      `search`, `listCases`, `getCase`, `postDecision`, `getRulesPerf`,
      `getVectorIntel`, `getExecutiveToday`.
- [ ] **5.2** `pages/SdnSearch.jsx` — name + country form, match list with
      score / source / matched entity / reason codes / Pass-Review-Escalate
      buttons. On decision, POST then route to `/dashboard/cases/{id}`.
- [ ] **5.3** `pages/Investigation.jsx` — `/cases/:caseId`. Subscore table
      (exact / alias / fuzzy / vector / country / final), case header,
      decision history, notes field, evidence upload.
- [ ] **5.4** `pages/RulesPerf.jsx` — Rule inventory table (status +
      weight) + performance table (alerts / TP / FP). Tune-threshold link
      visible only to `Admin`.
- [ ] **5.5** `pages/ExecutiveToday.jsx` — CCO tiles (Customers, Txns,
      Alerts, Open Cases, Confirmed, FPs, SDN records, Last update).
- [ ] **5.6** `pages/VectorIntel.jsx` — Admin-only. Similarity-bucket bar
      chart, top-N name variants, vector-recall metrics. Route-gate behind
      `canGovernModel` else 403.
- [ ] **5.7** `components/Navbar.jsx` — reorganize tabs by persona:
      `Search | Cases | Rules | Executive | Vector | --- | Rules | Changes
      | Obligations | Enforcement | SDN Engine | Workflows | Reports`.
      Rename existing OFAC tab to `SDN Engine`. Hide tabs the role can't
      access.
- [ ] **5.8** Routes in `App.jsx` — add `/dashboard/search`, `/dashboard/
      cases`, `/dashboard/cases/:id`, `/dashboard/rules-perf`, `/dashboard/
      executive`, `/dashboard/vector` (admin-only).

### Phase 6 — UX guards (separate review pass)

- [ ] **6.1** Audit every Officer-facing component — confirm we never render:
      raw embedding arrays, cosine floats `0.87231`, model name
      `bge-large-v1.5`, vector dim `1024`.
- [ ] **6.2** Relabel `MatchDetail.scores` keys for officer display:
      `trgm → "Fuzzy Match"`, `fuzz → "Alias Match"`,
      `vector → "Vector Similarity"`, `final → "Final Risk Score"`.
      Show as integers 0-100, not floats 0-1.
- [ ] **6.3** Keep raw scores in `engine_response` JSONB for audit / Admin
      VectorIntel only.

### Phase 7 — Tests + smoke

- [ ] **7.1** `tests/test_screening_api.py` — happy paths for PASS / REVIEW
      / HOLD, idempotency by `criteria_hash`, audit row written, RBAC denies
      `viewer / CIO`.
- [ ] **7.2** `tests/test_screening_audit.py` — confirm 3 events → 1 case
      collapsing; case transitions on `/decision`.
- [ ] **7.3** Frontend `vitest` — `SdnSearch` decision flow, role-gated
      `VectorIntel` 403 for non-admin.
- [ ] **7.4** `make schema-check` + `ruff` + `black` clean.
- [ ] **7.5** Local docker-compose click-through against all 5 screens
      with each of the 5 trial users.

---

## 4. Files that will change

```
infra/cloudformation/pipeline2-dashboard.yml      [Phase 1]
infra/scripts/deploy-p2.sh                        [Phase 1]
docs/setup/operations.md                          [Phase 1]
src/api/auth.py                                   [Phase 1]
src/services/validators.py                        [Phase 2]
src/models/database.py                            [Phase 2]
src/api/validation_api.py                         [Phase 2]
src/api/screening_api.py            (NEW)         [Phase 3]
src/main_pipeline2.py                             [Phase 3]
docs/runbooks/ofac-screening-api-contract.md      [Phase 3]
src/api/dashboard_api.py                          [Phase 4]
dashboard/src/services/api.js                     [Phase 5]
dashboard/src/App.jsx                             [Phase 5]
dashboard/src/components/Navbar.jsx               [Phase 5]
dashboard/src/pages/SdnSearch.jsx     (NEW)       [Phase 5]
dashboard/src/pages/Investigation.jsx (NEW)       [Phase 5]
dashboard/src/pages/RulesPerf.jsx     (NEW)       [Phase 5]
dashboard/src/pages/ExecutiveToday.jsx (NEW)      [Phase 5]
dashboard/src/pages/VectorIntel.jsx   (NEW)       [Phase 5]
tests/test_screening_api.py           (NEW)       [Phase 7]
tests/test_screening_audit.py         (NEW)       [Phase 7]
```

---

## 5. Open decisions (need product sign-off before Phase 3 ships)

1. **Group naming:** `Analyst` + `Admin` confirmed? (Alternative: `ModelGov`
   for governance instead of `Admin`.)
2. **Trial domain:** `testbank.local` (RFC-unroutable) vs `testbank.com`
   (registrable, prettier).
3. **Production `/api/v1/screen/sdn` auth:** Cognito-only, HMAC-only, or
   both. Current proposal: **both** — banks send HMAC from their backend,
   dashboard calls with Cognito Bearer.
4. **Idempotency window:** 24h proposed. If SDN list refreshes mid-window,
   cache is invalidated automatically (latest `last_seen_at` > cached
   `created_at`).
5. **Vector tuning UI:** read-only metrics in Phase 5; threshold-write UI
   deferred to a later phase pending the OFAC calibration runbook
   (`docs/runbooks/ofac-calibration-gold-set.md`) completing.

---

## 6. Related runbooks

- `docs/runbooks/ofac-screening-api-contract.md` — API contract (to be
  written in Phase 3).
- `docs/runbooks/ofac-calibration-gold-set.md` — per-class threshold
  calibration (already exists).
- `docs/runbooks/agent-4-sync.md` — P2 sync + workflow (owns this work).
