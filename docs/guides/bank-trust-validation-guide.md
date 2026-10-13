# Fortress Dashboard — Bank Trust & Validation Guide

**Audience:** Bank CCO, CRO, CIO, internal audit, BA validators, and any examiner asking "how do I know the number on screen is correct?"
**Companion docs:** [`dashboard-user-guide.md`](dashboard-user-guide.md) (how to use the dashboard) · [`docs/dashboard_onboarding.md`](../dashboard_onboarding.md) (every page reference) · [`docs/runbooks/ba-validation-postgres-access.md`](../runbooks/ba-validation-postgres-access.md) (how to open the read-only SQL session)
**Single principle:** *Every number on this dashboard traces to a row you can see, query, and reconcile yourself.*

This is the guide you hand to the bank's BA, internal audit team, or examiner. It explains, tab by tab, what the dashboard shows, where the number came from, and the exact SQL anyone can run to confirm it independently. It includes the sign-off flows, the full regulation coverage map, a worked example of the 6-agent test-generation pipeline, and the exam-export verification protocol.

---

## 0. How to use this guide

Three patterns. Pick the one that matches your question.

**(a) "What does this number mean?"** Find the tab in §3, read the row for that stat. The "What it counts" column is the plain-English definition.

**(b) "Is this number correct?"** Same row, "Validation query" column. Open a read-only psql session via the BA validation runbook, paste the query, compare. If the two disagree by more than one, escalate per §10.

**(c) "What do I do next?"** Most rows include a "Next action" line. The §4 sign-off matrix tells you who has authority. The §9 worked example shows what happens after Generate.

A note on numbers in this guide. Where you see a specific count (e.g. **224 rules**), that is what the live `testbank` API reported when this document was written (sync_version 30, 2026-06-30T01:52Z). Your bank's numbers will differ. The point is the *method*, not the value.

---

## 1. The trust model in one diagram

```
SOURCE (regulator website / OFAC XML)
   │
   │  scraped by connector → diff-detected
   ▼
PIPELINE 1 (Fortress Cloud, multi-tenant)
   │  table: compliance_rules, ofac_sdn_entries, regulatory_change_records
   │  raw chunks → S3 (KMS, scraper-chunks/<connector>/)
   │
   │  daily sync (HMAC signed, per-bank)
   ▼
PIPELINE 2 (your bank's VPC, single-tenant)
   │  same tables, your bank's data residency
   │  obligations extracted from rule_text (regex → LLM fallback)
   │  enforcement_actions correlated to rules
   │
   │  every dashboard endpoint = SQL against these tables
   ▼
DASHBOARD UI (React, Cognito-gated, role-scoped)
   │  every number = a backend query you can reproduce
   ▼
EXAMINATION EVIDENCE
   sign-off (Cognito email + JTI + timestamp) → exports (canonical hash → WORM S3)
```

Three properties that make the system audit-defensible:

1. **Two-pipeline separation.** The bank's PII never leaves the bank's VPC. P1 ships *rules* down; P2 holds *screenings, cases, and obligations* in-bank.
2. **Append-only audit tables.** `regulatory_change_records`, `screening_events`, `screening_case_events`, `export_artifacts`, `export_signoffs`, `export_access_log` are write-once. No row is ever updated to falsify a prior state; you mark it `is_active=FALSE` and write a new row.
3. **Idempotent versioning.** Exports are hashed before they're rendered. Re-running the same `(bank_id, type, as_of)` produces the same SHA256 and the same S3 key — your version stamp is a tamper-evidence anchor.

---

## 2. How to run any validation query

Two routes — pick whichever your role allows.

### Route A: BA read-only psql session (recommended)

Follow [`docs/runbooks/ba-validation-postgres-access.md`](../runbooks/ba-validation-postgres-access.md). It opens an SSM-tunneled Fargate jump task with **read-only** Postgres credentials against the bank's Aurora (`fortress_p2`). Every query in this guide is safe to run there — none mutate state.

Quick check that the session is alive:
```sql
SELECT current_database(), current_user, NOW() AT TIME ZONE 'UTC';
-- expected: fortress_p2 | fortress_p2_readonly | <utc timestamp>
```

### Route B: Hit the API directly

The unauthenticated health endpoint exposes top-line counts. Useful when you don't have a session and just want one anchor number.

```bash
curl -sk "$P2_API/health/sync"
# {"status":"ok","sync_version":30,"last_synced_at":"...","sync_age_hours":6.77,
#  "rules_count":224,"sync_status":"SUCCESS"}
```

For every other endpoint you need a Cognito JWT (sign in via the dashboard's hosted UI, then `localStorage.getItem('accessToken')` in the browser console — examiner-friendly).

---

## 3. Per-tab validation reference

Each subsection below covers one dashboard tab. Format:

- **Route + page file** — where the page lives in code.
- **Backend endpoints** — what the page calls.
- **Stat table** — every number on the page, with the validation SQL.
- **Filters / caveats** — what you must reproduce to get the same answer.
- **Common bank questions** — what BAs and examiners ask most.
- **What to do next.**

### 3.1 Readiness — the home page

| Property | Value |
|---|---|
| Route | `/readiness` |
| Page file | `dashboard/src/pages/Readiness.jsx` |
| Backend | `GET /api/v1/dashboard/readiness?lens={cco\|cro\|cio}` · `/risk-matrix` · `/cro` · `/cio` · `/findings` |
| Code | `src/api/dashboard_api.py:693–973` |

**The 10 readiness dimensions.** Each is scored 0–100. The composite score is a weighted average (weights vary by lens). Click the lowest bar → land on that workstream's tab.

| Dimension | Plain-English meaning | Validation query | Healthy band |
|---|---|---|---|
| Obligation inventory % | How many active rules have a CFR citation attached | `SELECT 100.0 * COUNT(*) FILTER (WHERE cfr_citation IS NOT NULL) / NULLIF(COUNT(*),0) FROM compliance_rules WHERE is_active=TRUE;` | ≥ 90 |
| Control mapping % | How many active obligations have a non-`unknown` regulator | `SELECT 100.0 * COUNT(*) FILTER (WHERE regulator IS NOT NULL AND regulator<>'unknown') / NULLIF(COUNT(*),0) FROM obligations WHERE is_active=TRUE;` | 100 (design goal) |
| Tested obligations % | How many active obligations have a test linked | `SELECT 100.0 * COUNT(*) FILTER (WHERE has_test_coverage=TRUE) / NULLIF(COUNT(*),0) FROM obligations WHERE is_active=TRUE;` | Rises over time as Generate is used |
| Risk-assessment currency % | Share of active rules with an enforcement risk score | `SELECT 100.0 * COUNT(*) FILTER (WHERE enforcement_risk_score > 0) / NULLIF(COUNT(*),0) FROM compliance_rules WHERE is_active=TRUE;` | ≥ 80 |
| Change management % | Share of detected changes that have been dispositioned | `SELECT 100.0 * COUNT(*) FILTER (WHERE status<>'detected') / NULLIF(COUNT(*),0) FROM regulatory_change_records WHERE is_active=TRUE AND regulator<>'OFAC';` | ≥ 90 sustained |
| Governance evidence % | Same as risk-assessment currency (proxy until separate signal lands) | (same SQL as above) | — |
| Independent testing % | Active rules with at least one completed workflow run | `SELECT 100.0 * COUNT(DISTINCT r.rule_id) FILTER (WHERE w.status='completed') / NULLIF(COUNT(DISTINCT r.rule_id),0) FROM compliance_rules r LEFT JOIN workflow_executions w ON w.rule_id=r.rule_id WHERE r.is_active=TRUE;` | Rises over time |
| Reporting % | Placeholder at 40 until findings table is wired | (placeholder) | n/a |
| Engine integrity % | Avg of (SDN freshness · embedding coverage · 7d pipeline success) | see "Engine integrity" breakdown below | ≥ 80 |
| Findings (MRAs) | Count of open MRA / MRIA findings | `SELECT COUNT(*) FROM findings WHERE status IN ('open','past_due');` | 0–few, never rising |

**Engine integrity breakdown** (the only composite — explain it to the examiner first):
```sql
SELECT
  CASE
    WHEN MAX(last_seen_at) >= NOW() - INTERVAL '24 hours' THEN 100
    WHEN MAX(last_seen_at) >= NOW() - INTERVAL '72 hours' THEN 60
    ELSE 30
  END AS sdn_freshness,
  (SELECT 100.0 * COUNT(*) FILTER (WHERE embedding IS NOT NULL) / NULLIF(COUNT(*) FILTER (WHERE is_active=TRUE),0) FROM ofac_sdn_vectors) AS embed_pct,
  (SELECT 100.0 * COUNT(*) FILTER (WHERE status='completed') / NULLIF(COUNT(*),0) FROM workflow_executions WHERE started_at >= NOW() - INTERVAL '7 days') AS pipe_pct
FROM ofac_sdn_vectors;
-- the dashboard averages the three and rounds to an integer
```

**Risk × coverage matrix (top-right).** Red cells = High risk × Untested. The "biggest drag" headline calls out the cell with the most rule IDs. Validation:
```sql
SELECT COUNT(*) AS high_risk_untested
FROM compliance_rules cr
WHERE cr.is_active = TRUE
  AND cr.enforcement_risk_score >= 0.8
  AND NOT EXISTS (
    SELECT 1 FROM workflow_executions w
    WHERE w.rule_id = cr.rule_id AND w.status = 'completed'
  );
```

**Lens-specific panels.**
- **CCO lens** — Coverage & evidence. Each panel is one of the dimensions above.
- **CRO lens** — Residual risk vs. appetite by domain. KRI 90-day trend. Peer signal from `enforcement_actions`.
- **CIO lens** — Pipeline success rate (7d), avg duration, embedding integrity, uptime, open failures.

**Common bank question:** *"Why is reporting % stuck at 40?"* — That dimension is a placeholder pending the findings/MRA pipeline going live (see §11 roadmap). All other dimensions are live numbers.

**What to do next:** Click the lowest bar → land on the corresponding tab. Sort by enforcement risk. Click **Generate** on the highest-risk untested rules.

---

### 3.2 Executive Today — CCO board view

| Property | Value |
|---|---|
| Route | `/dashboard/executive` |
| Page file | `dashboard/src/pages/ExecutiveToday.jsx` |
| Backend | `GET /api/v1/dashboard/screening/executive-today` |
| Role | **CCO only** (gated on `canEdit`) |

| Stat | What it counts | Validation query |
|---|---|---|
| Screenings today | Every screening call (PASS, REVIEW, HOLD) since midnight UTC | `SELECT COUNT(*) FROM screening_events WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC');` |
| Alerts today | Screenings that opened or attached to a case | `SELECT COUNT(*) FROM screening_events WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AND decision IN ('REVIEW','HOLD');` |
| Open cases | Cases not yet resolved | `SELECT COUNT(*) FROM screening_cases WHERE status NOT IN ('cleared','closed');` |
| Confirmed (lifetime) | True positives marked by reviewers | `SELECT COUNT(*) FROM screening_cases WHERE reviewer_action='confirmed_true_positive';` |
| False positives (lifetime) | FPs marked by reviewers | `SELECT COUNT(*) FROM screening_cases WHERE reviewer_action='cleared_false_positive';` |
| SDN records (active) | SDN entries currently enforced | `SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active=TRUE;` |
| SDN list freshness | Hours since last SDN sync | `SELECT EXTRACT(EPOCH FROM (NOW() - MAX(last_seen_at))) / 3600.0 FROM ofac_sdn_vectors;` |

**Filters to reproduce:** All "today" stats use UTC date_trunc — if you run from a non-UTC psql session, the count will drift by up to one calendar day at the boundary.

**Common bank question:** *"Why does Confirmed/FP not reset daily?"* — These are lifetime tallies on cases, by design. Examiners want a stable book of decisions, not a daily restart.

**Common bank question:** *"What if SDN freshness reads `unknown`?"* — That means the SDN loader has not run successfully. Escalate to the CIO. Do **not** sign a daily OFAC attestation off a stale dashboard.

---

### 3.3 Compliance Rules

| Property | Value |
|---|---|
| Route | `/rules` (and `?ids=…` from the risk matrix drill) |
| Page file | `dashboard/src/pages/Rules.jsx` |
| Backend | `GET /api/v1/dashboard/rules?regulator=&page=&page_size=100` |

| Stat / column | What it counts | Validation query |
|---|---|---|
| Total rules | Active rules with optional regulator filter | `SELECT COUNT(*) FROM compliance_rules WHERE is_active=TRUE;` |
| By regulator | Active rules grouped by source regulator | `SELECT regulator, COUNT(*) FROM compliance_rules WHERE is_active=TRUE GROUP BY regulator ORDER BY 2 DESC;` |
| Enforcement risk score | Per-rule 0–1 score; UI buckets ≥0.8 high, ≥0.6 med | `SELECT rule_id, enforcement_risk_score FROM compliance_rules WHERE rule_id='OCC-BSA-WIRE-001';` |
| Tested (count per rule) | Completed workflow runs for the rule | `SELECT COUNT(*) FROM workflow_executions WHERE rule_id='<rule_id>' AND status='completed';` |
| Bank status (per rule) | Per-bank override (active / not_applicable / override) | `SELECT status FROM rule_overrides WHERE rule_id='<rule_id>' AND client_id='<bank_id>';` |

**Filters to reproduce:** Jurisdiction chip uses `regulator ~ '^(CA|TX|FL|NY|WA)-'` (regex). Page size on `/rules` is **100 by default**.

**Common bank question:** *"Why does my rule count differ from yesterday's screenshot?"* — Either (a) the daily P1→P2 sync brought in new rules and bumped `sync_version`, (b) a rule was tombstoned (regulator retired it; `is_active=FALSE`), or (c) the CCO marked rules N/A. Reconciliation:
```sql
SELECT change_type, COUNT(*) FROM regulatory_change_records
WHERE created_at >= NOW() - INTERVAL '1 day' AND regulator <> 'OFAC'
GROUP BY change_type;
```

**What to do next:** Sort by risk, filter to the rules where Status = "Not tested", click **Generate** on the highest-risk ones. Each Generate kicks off the 6-agent pipeline (§9).

---

### 3.4 Obligations

| Property | Value |
|---|---|
| Route | `/obligations` |
| Page file | `dashboard/src/pages/Obligations.jsx` |
| Backend | `GET /api/v1/dashboard/obligations` |

| Stat | What it counts | Validation query |
|---|---|---|
| Total obligations | All active obligation rows | `SELECT COUNT(*) FROM obligations WHERE is_active=TRUE;` |
| Covered obligations | Obligations linked to a test | `SELECT COUNT(*) FROM obligations WHERE is_active=TRUE AND has_test_coverage=TRUE;` |
| Coverage % | Covered / total × 100, one decimal | `(covered::numeric / NULLIF(total,0)) * 100` |
| By regulator | Per-regulator breakdown + coverage % | (see below) |

**By-regulator query (matches the bar chart):**
```sql
SELECT regulator,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE has_test_coverage) AS covered,
       ROUND(100.0 * COUNT(*) FILTER (WHERE has_test_coverage) / NULLIF(COUNT(*),0), 1) AS coverage_pct
FROM obligations
WHERE is_active = TRUE
GROUP BY regulator
ORDER BY total DESC;
```

**Common bank question:** *"Where do obligations come from?"* — Each row in `compliance_rules.rule_text` is run through a **regex-first, LLM-fallback** extractor (`src/services/obligation_extractor.py`):

1. Sentences are split on `. ` / `.\n`. Sentences containing trigger phrases (`shall`, `must`, `required to`, `prohibited from`, `may not`, …) are kept.
2. Each surviving sentence is classified (shall / must / required / prohibited / obligation) and tagged with the first CFR cite found in the sentence.
3. **Only if** the regex yields zero obligations AND `LLM_EXTRACTION_MODE` ∈ {`fallback`, `shadow`}, the system invokes Bedrock Haiku 4.5 (`src/extraction/rule.py:extract_rule`). The LLM result must clear `LLM_EXTRACTION_MIN_CONFIDENCE` (default 0.5) or it is discarded.
4. Surviving sentences are inserted into `obligations` (one row per sentence).

**Four checks to confirm obligation correctness:**

```sql
-- (1) No orphans (every obligation traces to a real rule)
SELECT COUNT(*) AS orphans FROM obligations o
LEFT JOIN compliance_rules cr ON cr.rule_id = o.rule_id
WHERE o.is_active=TRUE AND cr.id IS NULL;
-- expected: 0

-- (2) Every obligation's source_text appears in its rule's text
SELECT o.obligation_id, o.rule_id FROM obligations o
JOIN compliance_rules cr ON cr.rule_id = o.rule_id
WHERE o.is_active=TRUE AND POSITION(o.source_text IN cr.rule_text) = 0
LIMIT 20;
-- expected: 0 rows

-- (3) No "unknown" regulators (the design contract)
SELECT COUNT(*) FROM obligations WHERE is_active=TRUE AND regulator='unknown';
-- expected: 0

-- (4) Spot-check the regex: pick 5 rules, count manually, compare
SELECT rule_id, COUNT(*) FROM obligations
WHERE is_active=TRUE AND rule_id IN (
  'OCC-BSA-WIRE-001','OFAC-FUZZY-001','CFPB-1071-RECAP-001',
  'FFIEC-CAT-INFOSEC-001','CRA-LARGE-EXAM-001')
GROUP BY rule_id;
```

**Why the number might change between page loads:**
- A daily P1→P2 sync just landed (`sync_version` bumps).
- `LLM_EXTRACTION_MODE` flipped from `off` to `fallback` — the residual ~96% of rules with no regex hit get LLM-extracted, total rises.
- The CCO marked a rule N/A — its obligations soft-deactivate (`is_active=FALSE`).
- A scraper diff detected updated `rule_text` → old obligations tombstone, new ones insert. Audit trail in `regulatory_change_records`.

**What to do next:** A coverage gap below 80% on any single regulator is the CCO's first work queue. Click into the bar → land on `/rules?regulator=<X>` filtered → Generate.

---

### 3.5 Regulatory Changes

| Property | Value |
|---|---|
| Route | `/changes` |
| Page file | `dashboard/src/pages/Changes.jsx` |
| Backend | `GET /api/v1/dashboard/changes?page=&page_size=20&include_ofac=false` + `/changes/{ref}` + `POST /changes/{ref}/signoff` |

| Stat | What it counts | Validation query |
|---|---|---|
| Total changes (inbox) | Active changes, OFAC excluded by default | `SELECT COUNT(*) FROM regulatory_change_records WHERE is_active=TRUE AND regulator<>'OFAC';` |
| Unsigned changes | Changes still at status `detected` | `SELECT COUNT(*) FROM regulatory_change_records WHERE is_active=TRUE AND status='detected' AND regulator<>'OFAC';` |
| Per-row change delta | JSON of added/removed sentences (migration 037) | `SELECT change_delta FROM regulatory_change_records WHERE change_ref='<ref>';` |
| Previous rule text | Snapshot at detection time | `SELECT previous_content FROM compliance_rules WHERE rule_id='<id>';` |

**Filters to reproduce:** The page defaults to `include_ofac=false` because the OFAC SDN loader emits one lifecycle event per alias — a Soviet-era diplomat being delisted produces 47 alias-rows in `regulatory_change_records` overnight, which would drown out a real CFPB rule change.

To see OFAC changes explicitly:
```sql
SELECT change_type, COUNT(*) FROM regulatory_change_records
WHERE regulator='OFAC' AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY change_type;
```

**Common bank question:** *"What happens when I click Sign off?"* — A modal asks for a disposition (`acknowledged`, `requires_action`, `not_applicable`) and an optional note. The backend:
1. INSERT into `change_record_dispositions` with `signed_off_at=NOW()`, signer email, JTI.
2. UPDATE `regulatory_change_records.status` from `detected` to the disposition.
3. If `requires_action`, optionally creates a JIRA epic linked to `change_ref`.
4. The change drops off the inbox immediately; its dispositioned row appears in the next Examiner evidence package (§7).

**The CCO sign-off cycle in detail (this is the diagram a bank examiner asks for):**

```
Detected           Acknowledged          Requires action       Closed
 (scraper)            (CCO note)         (JIRA epic opens)    (after PR merge)
   │                     │                    │                   │
   │ status=detected    │ status=signed_off   │ status=signed_off  │ status=signed_off
   │ signed_off_at=NULL │ disposition=ack     │ disposition=ra     │ resolved_at=…
   │                     │                    │                   │
   ▼                     ▼                    ▼                   ▼
regulatory_change_records   change_record_dispositions       workflow_executions
                                   (one row per disposition)        (PR opened)
```

**What to do next:**
- High-impact (red): read the diff drawer, decide disposition, sign off.
- Medium / low: handle in batches but **do not bulk-acknowledge** — examiners look at the timestamp distribution; a burst of identical signoffs two days before exam looks bad.
- The `requires_action` disposition is the one that earns its keep — it produces a downstream JIRA epic and (eventually) a GitHub PR with a regression test.

---

### 3.6 Enforcement Actions

| Property | Value |
|---|---|
| Route | `/enforcement` |
| Page file | `dashboard/src/pages/Enforcement.jsx` |
| Backend | `GET /api/v1/dashboard/enforcement?regulator=&page=&page_size=25` |

| Stat | What it counts | Validation query |
|---|---|---|
| Total enforcement actions | Active rows, optional regulator filter | `SELECT COUNT(*) FROM enforcement_actions WHERE is_active=TRUE;` |
| Penalty USD | Decimal(15,2), held-for-review > $10B | `SELECT regulator, action_date, penalty_amount_usd FROM enforcement_actions ORDER BY action_date DESC LIMIT 25;` |
| Regulator filter | OCC / CFPB / FinCEN / OFAC / FDIC | `SELECT regulator, COUNT(*) FROM enforcement_actions WHERE is_active=TRUE GROUP BY 1;` |

**Filter to reproduce:** The page bounds pagination at 25/page. Penalty parser: LLM (`src/extraction/penalty.py`) with confidence ≥ 0.5; regex fallback when LLM unavailable. Anything > $10B is flagged "held for review" in the UI because the regex fallback occasionally captures bank revenue from headlines.

**Common bank question:** *"Where do you scrape these from?"* — Four sources, weekly:
- OCC: `occ.gov/topics/supervision-and-examination/enforcement`
- FDIC: `fdic.gov/bank/individual/enforcement/`
- CFPB: `consumerfinance.gov/enforcement/actions/`
- FRB: `federalreserve.gov/apps/enforcementactions/enforcementactions`

**What to do next:** The CRO scans this monthly. Recent peer penalties = heightened examiner scrutiny in that domain.

---

### 3.7 OFAC / SDN Engine Health

| Property | Value |
|---|---|
| Route | `/ofac` |
| Page file | `dashboard/src/pages/OfacHealth.jsx` |
| Backend | `GET /api/v1/dashboard/ofac-health` |

| Stat | What it counts | Validation query |
|---|---|---|
| Active SDN entries | Master-record count | `SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active=TRUE;` |
| Total name variants | All variants (incl. tombstoned, for lifecycle audit) | `SELECT COUNT(*) FROM ofac_sdn_vectors;` |
| Active variants | Variants currently enforced | `SELECT COUNT(*) FROM ofac_sdn_vectors WHERE is_active=TRUE;` |
| Embedded variants | Variants with a vector loaded | `SELECT COUNT(*) FROM ofac_sdn_vectors WHERE embedding IS NOT NULL AND is_active=TRUE;` |
| Embedding coverage % | embedded / active × 100 | (compute from above) |
| Vector mode ready | TRUE when coverage ≥ 99% | dashboard computes from above |
| Last refresh | Most recent loader run | `SELECT MAX(last_seen_at) FROM ofac_sdn_vectors;` |

**What "vector mode ready" actually means** — the screening engine has three signals: trigram (typo-resistant), tsv (token), and vector (semantic). The first two are always live. Vector kicks in when ≥99% of active variants have an embedding. Below that threshold the engine still serves correctly via trigram+tsv — there is **no screening gap**, but the "Current" capability card switches from green to amber until backfill completes.

**SDN freshness card.** The header strip computes hours since `MAX(last_seen_at)`. SLA is 24h. If `> 24h`, the staleness bump kicks in: any REVIEW automatically becomes HOLD at screening time (rule FRESH-001).

**Common bank question:** *"Where does the SDN list come from?"* — A daily OFAC Treasury XML pull from `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML`. The raw XML is archived in S3 (`scraper-chunks/sdn_xml_raw/`, KMS-encrypted) and its SHA256 is recorded in `ofac_list_versions` so a forensic auditor can confirm the bank's screening was based on the exact list OFAC published that day.

**What to do next:** This is the CIO's weekly check. The CCO embeds a screenshot of this page in the monthly OFAC attestation export (§7.2).

---

### 3.8 SDN Search — front-line screening

| Property | Value |
|---|---|
| Route | `/dashboard/search` |
| Page file | `dashboard/src/pages/SdnSearch.jsx` |
| Backend | `POST /api/v1/dashboard/screening/search` |
| Role | Analyst, CCO, CRO, Admin |

| Result field | What it counts | How to validate |
|---|---|---|
| Screening ID | UUID of the call | `SELECT * FROM screening_events WHERE screening_id='<uuid>';` |
| Case ID | UUID of the case opened or attached | `SELECT * FROM screening_cases WHERE case_id='<uuid>';` |
| Decision (PASS / REVIEW / HOLD) | Engine verdict | column `screening_events.decision` |
| Final risk score (0–100) | Scaled engine score | `_officer_score(raw)=clamp(round(raw*100),0,100)` |
| Subscores | Fuzzy / Alias / Vector / Final | From engine's match_detail.scores (no DB lookup) |
| Freshness line | "Decision bumped because SDN data is N h old" | engine flag `staleness_bumped` |
| Attached case line | "Attached to CASE-XXXX" | `screening_events.case_was_pre_existing=TRUE` |

**The idempotency contract.** Two searches on the same `(customer_id, sdn_number, party_name)` within 24h return a **verbatim replay** with HTTP header `X-Idempotent: true`. That is a feature, not a bug — it means an analyst running the same search twice can't accidentally flood the case queue.

**Common bank question:** *"How do I prove this engine didn't miss someone?"* — Three checks:
1. Run the same name through the legacy mode (env-flag `OFAC_SCREENER_MODE=legacy`) and compare scores.
2. Verify the SDN list is fresh (§3.7).
3. Spot-check the FRESH-001 rule weight is > 0:
   ```sql
   SELECT rule_id, weight, is_active FROM screening_rules WHERE rule_id LIKE '%FRESH%';
   ```

**What to do next:**
- **PASS** + no matches → move on.
- **REVIEW** / **HOLD** → a case opens automatically. Click "case CASE-…" and go to Investigation.

---

### 3.9 Cases queue

| Property | Value |
|---|---|
| Route | `/dashboard/cases?status=&priority=&q=` |
| Page file | `dashboard/src/pages/Cases.jsx` |
| Backend | `GET /api/v1/dashboard/screening/cases` |

| Stat | What it counts | Validation query |
|---|---|---|
| Total cases (filtered) | With filters applied | `SELECT COUNT(*) FROM screening_cases WHERE <filters>;` |
| Priority | high / medium (set at open) | column |
| Decision at open | PASS / REVIEW / HOLD | column `decision_at_open` |
| Event count | Re-screenings attached to this case | column `event_count` |
| Status | open / in_review / escalated / cleared / closed | column |

**Filters to reproduce:**
- `status=open` is the analyst's default queue (everything not in `{cleared, closed}`).
- Free-text `q` searches `matched_name`, `sdn_number`, `external_customer_id` (ILIKE).

**Common bank question:** *"Why does one case have 12 events?"* — Recurring monitoring: the same customer screened daily attaches to the open case rather than opening 12 new ones. That's the SDN program working.

**What to do next:** Click into a case → Investigation. Sort by priority=high.

---

### 3.10 Investigation

| Property | Value |
|---|---|
| Route | `/dashboard/cases/{caseId}` |
| Page file | `dashboard/src/pages/Investigation.jsx` |
| Backend | `GET /screening/cases/{id}` · `POST /cases/{id}/decision` |

The page shows three panels (score breakdown, matched entity, decision history). The decision panel has three buttons: **Pass**, **Send to review**, **Escalate**.

**The HOLD → Pass guardrail.** Passing a HOLD case requires (a) CCO role and (b) ≥50 chars of reviewer notes (`PASS_HOLD_MIN_NOTES_CHARS`). This is enforced client-side AND server-side. The notes are the audit row an examiner reads first when asking "why did you clear this match?"

**Validation that a decision was recorded properly:**
```sql
SELECT c.case_id, c.reviewer_action, c.reviewer_notes, c.resolved_at, c.resolved_by_user_id
FROM screening_cases c WHERE c.case_id='<uuid>';
```
The notes column must be ≥50 chars when `decision_at_open='HOLD' AND reviewer_action='confirmed_true_positive'` was overridden to a pass.

**Decision history audit trail:**
```sql
SELECT ev.created_at, ev.actor_user_id, ev.decision, ev.engine_mode, ev.final_score
FROM screening_case_events link
JOIN screening_events ev ON ev.id = link.event_id
WHERE link.case_id = '<uuid>'
ORDER BY ev.created_at ASC;
```

**What to do next:** Resolution must include written notes. "Cleared, looked fine" is examiner-bait — write the rationale.

---

### 3.11 Rules Performance

| Property | Value |
|---|---|
| Route | `/dashboard/rules-perf?engine_mode=` |
| Page file | `dashboard/src/pages/RulesPerf.jsx` |
| Backend | `GET /screening/rules-perf` · `PATCH /screening/rules/{id}` (Admin) |

The page has **four canonical screening rules**:

| Rule | What it does | Weight effect |
|---|---|---|
| **OFAC-SCREEN-FUZZY-001** | Fuzzy match ≥ thresholds → REVIEW/HOLD | Display-only (real threshold in SSM `/fortress/p2/ofac/threshold/{class}`) |
| **OFAC-SCREEN-CLEAN-001** | No-FP rule (audit hook) | Display-only |
| **OFAC-SCREEN-OWN50-001** | ≥50% ownership chain → cascading HOLD | Display-only |
| **OFAC-SCREEN-FRESH-001** | SDN data > 24h → bump REVIEW to HOLD | **Engine-active**: weight > 0 enables the bump, ≤ 0 disables |

**Precision and KPI math:**
```sql
SELECT
  COUNT(*) AS total_alerts,
  COUNT(*) FILTER (WHERE reviewer_action='confirmed_true_positive') AS tp,
  COUNT(*) FILTER (WHERE reviewer_action='cleared_false_positive') AS fp,
  COUNT(*) FILTER (WHERE status='open') AS open_cases,
  ROUND(
    COUNT(*) FILTER (WHERE reviewer_action='confirmed_true_positive')::numeric /
    NULLIF(
      COUNT(*) FILTER (WHERE reviewer_action='confirmed_true_positive') +
      COUNT(*) FILTER (WHERE reviewer_action='cleared_false_positive'), 0),
    3
  ) AS precision
FROM screening_cases;
```
Add `WHERE first_event_engine_mode='<legacy|vector>'` for the engine-mode filter.

**Common bank question:** *"Editing the FUZZY weight didn't change my alert rate. Bug?"* — No: that field is display-only (per onboarding doc). Real thresholds are in SSM. Only the **FRESH** weight is engine-active.

**What to do next:** A precision drop below 0.7 on a 7-day window → call the Admin runbook. Two real levers:
1. SSM threshold tune (`/fortress/p2/ofac/threshold/individual` etc., see `scripts/setup/calibrate_ofac_thresholds.py`).
2. FRESH weight toggle while SDN sync is being investigated.

---

### 3.12 Vector Intelligence

| Property | Value |
|---|---|
| Route | `/dashboard/vector` |
| Page file | `dashboard/src/pages/VectorIntel.jsx` |
| Backend | `GET /screening/vector-intel` |
| Role | Admin (write), CRO (read) |

| Stat | Validation query |
|---|---|
| Active SDN variants | `SELECT COUNT(*) FILTER (WHERE is_active) FROM ofac_sdn_vectors;` |
| Embedded variants + coverage % | `SELECT COUNT(*) FILTER (WHERE embedding IS NOT NULL), COUNT(*) FILTER (WHERE is_active) FROM ofac_sdn_vectors;` |
| Final-score histogram (30d, matches only) | `SELECT width_bucket(final_score::numeric, 0, 1, 10) AS bucket, COUNT(*) FROM screening_events WHERE created_at > now() - INTERVAL '30 days' AND match_count > 0 GROUP BY 1 ORDER BY 1;` |
| Top names (30d) | `SELECT party_name, COUNT(*), AVG(final_score) FROM screening_events WHERE created_at > now() - INTERVAL '30 days' AND match_count > 0 GROUP BY party_name ORDER BY 2 DESC LIMIT 25;` |
| Engine mode split (7d) | `SELECT engine_mode, COUNT(*) FROM screening_events WHERE created_at > now() - INTERVAL '7 days' GROUP BY 1;` |

**Caveat:** This page exposes raw similarity scores (not the officer 0–100 scaling). It contains PII — Admin/CRO only.

**Common bank question:** *"Coverage is 87% — should we hold off going live with vector mode?"* — Yes. The default flip threshold is 99%. Until then, leave `OFAC_SCREENER_MODE=legacy` and let the embed backfill catch up.

---

### 3.13 Workflows (6-agent pipeline runs)

| Property | Value |
|---|---|
| Route | `/workflows` |
| Page file | `dashboard/src/pages/Workflows.jsx` |
| Backend | `GET /api/v1/dashboard/workflows?limit=20` |

| Stat | Validation query |
|---|---|
| Recent workflows | `SELECT id, jira_story_key, status, started_at, completed_at, artifacts FROM workflow_executions ORDER BY started_at DESC LIMIT 20;` |
| Success rate (7d) | `SELECT 100.0 * COUNT(*) FILTER (WHERE status='completed') / NULLIF(COUNT(*),0) FROM workflow_executions WHERE started_at >= NOW() - INTERVAL '7 days';` |
| Avg duration | `SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))::INT AS sec FROM workflow_executions WHERE status='completed' AND started_at >= NOW() - INTERVAL '7 days';` |
| Open failures | `SELECT COUNT(*) FROM workflow_executions WHERE status='failed';` |

**What's in `artifacts`?** A JSON blob with the 6-agent stage outputs and (on success) `github_pr_url`. See §9 for the worked example of what each field means.

**What to do next:** A workflow stuck in `running` > 10 minutes is almost always a Bedrock or GitHub API rate-limit / token issue. Check `/health/sync` first (rules out P1 problems), then logs.

---

### 3.14 Reports / Exports

See §7 — exports are the bank's examiner-grade evidence trail and deserve a full section.

---

## 4. Sign-off matrix — who has authority for what

Every write action on the dashboard is captured by `<user.email>:<jwt.jti>:<timestamp>`. The right role must be in place; the dashboard hides the buttons otherwise.

| Action | CCO | CRO | CIO | Analyst | Admin | Notes |
|---|:-:|:-:|:-:|:-:|:-:|---|
| Generate test for a rule | ✓ | | | | ✓ | Kicks off 6-agent pipeline (§9) |
| Mark rule N/A | ✓ | | | | ✓ | Requires reason text — audit row |
| Sign off detected change | ✓ | ✓ | | | ✓ | Disposition `acknowledged` / `requires_action` / `not_applicable` |
| Pass override on HOLD case | ✓ | | | | ✓ (rare) | ≥50 chars notes required |
| Send case to review / escalate | ✓ | ✓ | | ✓ | ✓ | Any analyst+ |
| Tune FRESH weight | | | | | ✓ | Engine-active toggle |
| Edit FUZZY/OWN50/CLEAN weight | | | | | ✓ | Display-only — real threshold in SSM |
| Generate Examiner / OFAC report (PDF) | ✓ | ✓ | | | ✓ | See §7 |
| Sign Examiner package | ✓ | + Internal Audit | | | | **Dual signature**: CCO + Internal Audit |
| Sign OFAC attestation | | ✓ | | | + BSA Officer | **Dual signature**: BSA + CRO |
| Sign Board / MRA / Change log / Coverage report | ✓ | | | | | Single signature (JTI-only) |
| Add Cognito user | | | | | + IT lead | Via Cognito console; `admin-add-user-to-group` |

### Sign-off cycle — change management worked example

```
Mon 09:14 UTC  Scraper detects OCC bulletin update
                → INSERT regulatory_change_records (status='detected', impact='high')
                → appears in /changes inbox

Mon 10:42 UTC  CCO opens the card, reads the diff drawer (added/removed sentences)
                → clicks Sign off, disposition='requires_action', note: "BSA team to update procedure"
                → INSERT change_record_dispositions (signer='cco@bank.com', jti='abc…')
                → UPDATE regulatory_change_records SET status='signed_off', disposition='requires_action'
                → POST to JIRA: creates epic linked to change_ref
                → JIRA epic triggers webhook → workflow_executions (6-agent pipeline)
                → PR opens on GitHub with regression test

Mon 14:05 UTC  CCO sees PR link on /workflows page, reviews + merges
                → workflow status='completed', artifacts.github_pr_url=<merged>

Mon 14:06 UTC  obligation rows for the affected rule get has_test_coverage=TRUE
                → next morning's Examiner package export reflects the new coverage
```

---

## 5. Regulation coverage (the inventory examiners ask for)

Fortress runs **17 federal connectors + 5 state DFI connectors**. Each connector lives in `src/connectors/regulatory/usa/`. The full list:

### Federal — BSA / AML / OFAC

| Connector | Regulator | Scope (CFR / law) | Source URL | Output |
|---|---|---|---|---|
| `ofac_sdn.py` | OFAC | SDN list + sanctions programs | `sanctionslistservice.ofac.treas.gov/.../SDN_ADVANCED.XML` | `ofac_sdn_entries`, `ofac_sdn_vectors`, `regulatory_change_records` |
| `occ_bsa.py` | OCC | 12 CFR 21/103/1020-1029; BSA four pillars | `occ.gov/.../bsa/index-bsa.html` | `compliance_rules` |
| `ffiec_bsa.py` | FFIEC | 12 CFR 21/103 + FFIEC joint guidance | `bsaaml.ffiec.gov/manual` (CF-gated; static encoding fallback) | `compliance_rules` |
| `fincen_sar.py` | FinCEN | 31 USC §5318 SAR + BSA guidance + BOI | `fincen.gov/resources/filing-information` + SAR TTI PDFs | `compliance_rules` |

### Federal — Call reporting & capital

| `ffiec_call_report.py` | FFIEC | FFIEC 031/041/051 + 12 CFR 363 | `ffiec.gov/nicpubweb/...` (CF-gated; static encoding) | `compliance_rules` |

### Federal — Consumer & fair lending

| `cfpb.py` | CFPB | 12 CFR 1002/1005/1026 + HMDA + Section 1071 (July 2026 deadline) | `consumerfinance.gov/rules-policy/...` | `compliance_rules` |
| `cra.py` | OCC/FDIC/FRB | 12 CFR 25 / 228 / 345 — Community Reinvestment Act | `occ.gov/topics/.../cra/index-cra.html` (+ FDIC, FRB) | `compliance_rules` |

### Federal — Risk management

| `irr.py` | OCC | OCC Bulletin 2010-1, FRB SR 96-13 (EVE/NII stress) | `occ.gov/news-issuances/bulletins/...` | `compliance_rules` |
| `cecl.py` | OCC | Bulletin 2011-12 (model risk), 2020-51 (ACL), FASB ASC 326 | `occ.gov/news-issuances/bulletins/...` | `compliance_rules` |
| `cre_concentration.py` | OCC/FDIC/FRB | Joint 2006 CRE Guidance (construction >100%, total CRE >300%) | `fdic.gov/news/financial-institution-letters/2007/...` | `compliance_rules` |
| `liquidity_risk.py` | OCC/FDIC/FRB | Interagency Liquidity (2010) + post-SVB | `occ.gov/.../liquidity/index-liquidity.html` | `compliance_rules` |
| `operational_risk.py` | OCC | OCC Op Risk handbook + Basel II events | `occ.gov/.../operational-risk/index-operational-risk.html` | `compliance_rules` |
| `ffiec_cybersecurity.py` | FFIEC | CAT + IT booklets + NIST CSF | `ffiec.gov/cybersecurity.htm` (CF-gated; partial fallback) | `compliance_rules` |

### Federal — Peer benchmarking

| `peer_benchmarks.py` | FDIC/FFIEC/OCC | Aggregate public peer data | `banks.data.fdic.gov/api/`, FFIEC UBPR, OCC SRP | `peer_benchmarks` |

### State DFIs

| `states/nydfs.py` | NY-DFS | NY Banking Law, Part 500 (cyber, 72h breach), Part 504 (BSA/AML) | `dfs.ny.gov/...` | `compliance_rules` (state_code=NY) |
| `states/ca_dfpi.py` | CA-DFPI | California Financing Law, CCPA/CPRA, Digital Financial Assets Law | `dfpi.ca.gov/...` | `compliance_rules` (state_code=CA) |
| `states/tx_dob.py` | TX-DOB | TX Finance Code, Money Services Act, Consumer Lending | `dob.texas.gov/...` | `compliance_rules` (state_code=TX) |
| `states/fl_ofr.py` | FL-OFR | FL Banking Code (Ch 655), Money Transmitters (Ch 560) | `flofr.gov/...` | `compliance_rules` (state_code=FL) |
| `states/il_idfpr.py` | IL-IDFPR | IL Banking Act, TOMA, Consumer Installment Loan Act | `idfpr.illinois.gov/...` | `compliance_rules` (state_code=IL) |

### Enforcement actions (weekly)

`src/services/enforcement_actions.py` scrapes OCC / FDIC / CFPB / FRB enforcement listing pages weekly. Penalty extraction uses LLM-first (confidence ≥ 0.5) with regex fallback. Hash key: `SHA-256(regulator::institution::action_date)[0:16]`.

**Validation that all 22 connectors are wired:**
```sql
SELECT regulator, COUNT(*) FROM compliance_rules WHERE is_active=TRUE GROUP BY regulator ORDER BY 2 DESC;
-- expected: rows for OCC, FDIC, CFPB, FinCEN, FFIEC, OFAC, FRB, NCUA, CRA,
--           NYDFS, CA-DFPI, TX-DOB, FL-OFR, IL-IDFPR (state codes as suffixes possible)
```

If you see `regulator='unknown'`, that is an inference miss in `scraper_scheduler._infer_regulator()` and should be reported.

**Three sources that are CF-bot-gated.** `bsaaml.ffiec.gov`, `ithandbook.ffiec.gov`, and parts of `www.ffiec.gov` block scraping behind Cloudflare's JS challenge. We fall back to **hardcoded CFR encodings** in `_build_regulations()` / `_build_requirements()` for those connectors. The regulatory scope is preserved; the data is static until those hosts allow programmatic access.

---

## 6. SDN screening — full chain of custody for the OFAC attestation

This is the trail your bank shows OFAC examiners. Every link is reproducible.

```
1. OFAC publishes SDN_ADVANCED.XML
2. ofac_sdn.py downloads + parses → list of entities
3. ofac_sdn_loader.upsert_sdn_variants normalizes each name variant
     │ table: ofac_sdn_vectors (1 row per name variant — primary + aliases)
     │ uniqueness: (sdn_number, name_normalized)
     │ ON CONFLICT: preserve embedding, reset missed_pulls, reactivate if dormant
4. validate_pull_integrity — fails if dropped > 2% (OFAC delists ~0.5%/yr)
     │ on failure, skip tombstoning (don't trust a partial pull)
5. tombstone_missing_variants — variants absent in 2 consecutive pulls
     → deactivated AND change_type='removed' row in regulatory_change_records
6. record_list_version — INSERT ofac_list_versions (version_id, sha256_of_xml,
   published_at, ingested_at, source_url)
7. embed_sdn_variants (separate ECS task) — Bedrock Titan Embed v2,
   1024-dim, unit-norm; resumable; ~50 rows per commit
8. OFACScreener (validators.py) — at screening time:
     a. normalize the input party name (same fn as loader)
     b. pg_trgm similarity, tsv match, vector similarity
     c. blended score → 0–1 → mapped to 0–100 for display
     d. FRESH-001 bump if SDN > 24h old
9. screening_events INSERT (UUID, decision, scores, engine_mode)
10. screening_cases INSERT or UPDATE (case_was_pre_existing flag)
```

**Forensic reconstruction.** An examiner asks "what list was screening Alice's wire on March 15 at 14:32?"
```sql
SELECT v.version_id, v.sha256_of_xml, v.published_at, v.ingested_at
FROM ofac_list_versions v
WHERE v.list_name = 'sdn'
  AND v.ingested_at <= '2026-03-15 14:32:00+00'
ORDER BY v.ingested_at DESC LIMIT 1;
```
Cross-check the SHA256 against the raw XML in S3 (`scraper-chunks/sdn_xml_raw/...`). The two must match to the byte.

---

## 7. Exam exports — examiner-grade evidence

Six tiles on `/reports`. Each export is **canonical-hashed before render**, stored in **WORM S3** with **Object Lock COMPLIANCE + 5-year retention**, and signed by the roles below.

| # | Tile | Export type | Required signers | Signing strength | Builder | Source query (verbatim) |
|---|---|---|---|---|---|---|
| 1 | Examiner evidence package | `examiner_package` | **CCO + Internal Audit** | strong (typed name + acknowledgement) | `snapshot._examiner_package` | controls + control_tests + test_executions ≤ as_of |
| 2 | OFAC screening attestation | `ofac_attestation` | **BSA + CRO** | strong | `snapshot._ofac_attestation` | latest `ofac_list_versions` + latest `ofac_validations` |
| 3 | Board / audit-committee report | `board_report` | CCO | JTI-only | `snapshot._board_report` | enforcement_actions ≤ as_of, top 25 |
| 4 | MRA / Findings tracker | `mra_tracker` | CCO | JTI-only | `snapshot._mra_tracker` | findings ≤ as_of |
| 5 | Regulatory change log | `change_log` | CCO | JTI-only | `snapshot._change_log` | change_record_dispositions ORDER BY created_at DESC |
| 6 | Coverage gap | `coverage` | CCO | JTI-only | `snapshot._coverage` | active control_rule_mappings + control_tests |

### 7.1 The version-hash promise

Every export is identified by a **document ID** like `FTX-EVP-2026-0630-4a1f93c`. The 7-char suffix is the first 7 chars of:

```
SHA256(canonical_json({
  "snapshot": <snapshot dict>,
  "template_version": "v1.2026.06",
  "vocab_version": "v1.2026.06",
  "app_commit": "<git sha at build time>"
}))
```

Properties:
- **Idempotent.** Re-running same `(bank_id, type, as_of)` produces the same hash and lands on the same S3 key.
- **Tamper-evident.** Any byte change to the snapshot (a different vocab, a row inserted post-hoc, a template patch) yields a different hash.
- **Pre-render.** The PDF bytes are non-deterministic (font subsetting, Chromium variation), so we hash the canonical *envelope*, not the output. The footer prints both the version hash and the template/vocab versions used.

**How to verify a sealed export:**
1. Look at the footer: `FTX-EVP-2026-0630-4a1f93c · template v1.2026.06 · vocab v1.2026.06`.
2. Query the artifact row:
   ```sql
   SELECT artifact_id, sha256, version_hash, generated_by, generated_at,
          template_version, vocab_version, app_commit,
          s3_bucket, s3_key, s3_etag, object_lock_retain_until
   FROM export_artifacts
   WHERE bank_id='testbank' AND type='examiner_package' AND as_of='2026-06-30';
   ```
3. The first 7 chars of `sha256` must equal `version_hash` must equal the suffix on the footer.
4. `object_lock_retain_until` should be `generated_at + 5 years`. The S3 object can't be deleted before that, by S3 policy — not by Fortress code.

### 7.2 Examiner package — column-by-column

CSV columns (per `src/exports/csv_writer.py:31`):
- `control_id`, `name`, `owner_role`, `owner_name`, `test_id`, `threshold_value`, `threshold_unit`, `threshold_rationale`, `jira_story_key`, `github_pr_url`, `commit_hash`

PDF sections (per `templates/examiner_package.html`):
- **Cover** — bank legal name, charter #, primary regulator, as-of date, document ID, version hash.
- **Control catalogue** — every mapped control with its threshold and the JIRA / PR / commit anchors.
- **Latest test results** — per test execution, the result (PASS/PARTIAL/FAIL/NOT_TESTED), evidence S3 URI, executed_at.
- **Sign-off footer** — typed names of CCO and Internal Audit + ESIGN/UETA acknowledgement.

**What an examiner verifies on this PDF:**
1. The footer hash matches the artifact row (above).
2. Every `github_pr_url` resolves to a merged PR on the bank's GitHub repo.
3. Every `commit_hash` exists in the repo and was the head of `main` at the time of test execution.
4. Every test execution row points to evidence in S3 that the auditor can pull.

### 7.3 OFAC attestation — what the BSA officer signs

Two-block document. Block 1: the SDN list version (version_id, published_at, ingested_at, **SHA256 of the XML**). Block 2: the independent validation row (performed_on, validator, scope, findings_summary, next_due).

The strong-sign requirement means the BSA Officer **and** the CRO each type their name, tick the ESIGN/UETA acknowledgement, and the system records IP + JTI + timestamp. The document is `DRAFT — UNSIGNED` (red watermark, 45°) until both sign.

**Why two signers?** The OFAC examiner specifically asks that the BSA function and the second line of defense (CRO) each attest. Single-signature attestations get flagged.

### 7.4 What happens if the PDF render fails

The CSV path is inline and always works. The PDF path runs in a separate ECS task (`exports-worker`) with Playwright/Chromium. If the worker fails:
- The artifact row is **not** inserted (idempotency: we only INSERT after a successful PUT to S3).
- The `/exports` poll endpoint will keep returning `status=rendering` until the worker either succeeds or times out.
- CloudWatch logs: `/fortress/p2-exports-worker/{bank_id}`.

**Memory note (`feedback_exports_worker_separate_td`):** The exports-worker has its own ECR repo and task definition family. A hot-fix that changes `src/exports/*` requires rebuilding **both** `fortress/pipeline2-{bank}` (the API) AND `fortress-p2-exports-worker` (the renderer). Skipping the worker is the #1 reason "the API is green but PDFs don't render."

---

## 8. The 6-agent test-generation pipeline (worked example)

This section is "show, don't tell." It walks one rule end-to-end through the pipeline, with the data each stage produces.

### 8.1 Architecture

```
JIRA story  ──webhook──▶  /api/webhooks/jira
                          ├─ HMAC auth check
                          ├─ INSERT/UPDATE workflow_executions (status=RUNNING)
                          └─ BackgroundTasks.process_workflow

process_workflow:
   pg_advisory_lock(0x70327367)  ← cluster-wide FIFO serialization
   │
   ├─ Agent 1: Discovery        (Haiku 4.5) — pattern-match against banking regex
   ├─ Agent 2: Understanding    (Sonnet 4.5) — hybrid pgvector+BM25, resolve obligation_ids
   ├─ Agent 3: Framework        (Haiku 4.5) — Gherkin scenarios from patterns
   ├─ Agent 4: Generation       (Sonnet 4.5, 16k tokens, 600s read timeout) — pytest code
   ├─ Agent 5: Validation       (Haiku 4.5 + ast.parse) — syntax + compliance + quality
   └─ Agent 6: Execution        (no LLM) — open PR, post JIRA comment, mark obligations covered
   │
   pg_advisory_unlock
   UPDATE workflow_executions SET status=COMPLETED, artifacts={...}
```

All Bedrock model IDs are pulled from settings (`BEDROCK_MODEL` family, `us.anthropic.*` inference-profile prefix per memory `feedback_bedrock_inference_profile_prefix`).

### 8.2 Worked example — OFAC FUZZY-001

**The rule (DB):**
```sql
SELECT rule_id, regulator, cfr_citation, threshold_amount, rule_text
FROM compliance_rules WHERE rule_id='OFAC-FUZZY-001';
--
-- rule_id          | OFAC-FUZZY-001
-- regulator        | OFAC
-- cfr_citation     | 31 CFR 501
-- threshold_amount | 85.0
-- rule_text        | Screen all customer names and beneficiaries against the
--                  | OFAC SDN list with fuzzy name matching at 85% threshold.
--                  | Block exact matches immediately. Hold fuzzy matches for
--                  | manual review.
```

**The JIRA story (user input):**
> As a wire operations officer, I want the system to screen all international wire beneficiaries against OFAC SDN list before release, with fuzzy name matching for potential aliases.

### Stage 1 — Discovery (`src/agents/discovery.py`)

**Output (persisted to `workflow_executions.artifacts.discovery`):**
```json
{
  "banking_patterns": {"wire_transfer": true, "ofac": true},
  "compliance_requirements": [
    "ofac_sdn_screening",
    "ofac_fuzzy_name_matching",
    "ofac_real_time_blocking"
  ],
  "discovered_apis": ["screen_ofac_sdn"],
  "dependencies": ["ofac_sdn_service"],
  "summary": "Wire transfer with OFAC screening — 3 compliance requirements"
}
```

### Stage 2 — Understanding (`src/agents/understanding.py`)

**Output:**
```json
{
  "obligation_ids": ["OBL-OFAC-FUZZY-001"],
  "rule_ids": ["OFAC-FUZZY-001"],
  "banking_rules": {
    "ofac": {
      "sdn_screening": true,
      "fuzzy_match_threshold": 85,
      "blocking_required": true,
      "reporting_deadline_days": 10
    }
  },
  "rag_context": [
    {"topic": "ofac_screening",
     "content": "OFAC SDN list must be checked before any funds transfer. Fuzzy matching required. Blocked transactions reported within 10 days."}
  ],
  "rag_used": true
}
```

Note `obligation_ids` is resolved by querying the live `obligations` table for sentences attributed to this rule. That's the link that lets Agent 6 write back `has_test_coverage=TRUE` later.

### Stage 3 — Framework (`src/agents/framework.py`)

**Output (3 Gherkin scenarios):**
```json
{
  "scenarios": [
    {"name": "ofac_sdn_exact_match_blocked",
     "given": "Customer name exactly matches OFAC SDN entry",
     "when":  "Wire transfer attempted to blocked party",
     "then":  "Transaction blocked, blocking report filed within 10 days",
     "tags":  ["ofac","sanctions"]},
    {"name": "ofac_fuzzy_match_review",
     "given": "Customer name is a variant of SDN entry (e.g. Muhammed vs Muhammad)",
     "when":  "Fuzzy name match score >= 85%",
     "then":  "Transaction held for manual compliance review",
     "tags":  ["ofac","fuzzy_match"]},
    {"name": "ofac_false_positive_release",
     "given": "Fuzzy match held for review",
     "when":  "Compliance officer confirms false positive",
     "then":  "Transaction released, false positive documented in audit trail",
     "tags":  ["ofac"]}
  ],
  "scenario_count": 3,
  "coverage": {"total_requirements": 3, "covered_requirements": 3, "coverage_percentage": 100}
}
```

### Stage 4 — Generation, Bedrock Sonnet 4.5 (`src/agents/generation_bedrock.py`)

**LLM input:** system prompt + 3 Gherkin scenarios + banking rules + compliance context.
**Model:** `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (per `settings.P2_AGENT_GENERATION_MODEL`).
**Output (truncated):**

```python
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

@pytest.fixture
def ofac_sdn_service():
    return MagicMock()

@pytest.mark.parametrize("name,country,expected_status", [
    ("Osama Bin Laden", "YE", "BLOCKED"),
    ("Osama bin Laden", "YE", "BLOCKED"),
    ("Muhammad Ahmed", "SA", "CLEARED"),
])
def test_ofac_fuzzy_match_screening(ofac_sdn_service, name, country, expected_status):
    """Test OFAC SDN screening with fuzzy name matching at 85% threshold."""
    ofac_sdn_service.screen.return_value = {
        "status": expected_status,
        "match_score": 95.0 if "Osama" in name else None,
        "audit_trail_id": "audit-123",
    }
    result = screen_ofac_sdn(name, country, threshold=85, service=ofac_sdn_service)
    assert result["status"] == expected_status
    if expected_status == "BLOCKED":
        assert result["match_score"] >= 85
        ofac_sdn_service.file_blocking_report.assert_called_once()

def test_ofac_false_positive_release():
    fuzzy_match = {"name": "Muhammad Ahmed", "score": 82.5, "status": "HELD_FOR_REVIEW"}
    result = release_false_positive(fuzzy_match, "cco-001")
    assert result["status"] == "RELEASED"
    assert result["audit_entry"]["approved_by"] == "cco-001"
```

### Stage 5 — Validation (`src/agents/validation.py`)

```json
{
  "is_valid": true,
  "quality_score": 89,
  "syntax_valid": true,
  "compliance_score": 95,
  "coverage": {"tests_generated": 4, "scenarios_total": 3, "scenarios_covered": 3, "coverage_percentage": 100},
  "recommendation": "approved"
}
```

### Stage 6 — Execution (`src/agents/execution.py`)

```json
{
  "pr_url": "https://github.com/testbank/compliance-tests/pull/287",
  "jira_comment_posted": true,
  "obligations_covered": ["OBL-OFAC-FUZZY-001"],
  "status": "completed"
}
```

**Database writebacks the bank can verify:**
```sql
-- the workflow row
SELECT id, jira_story_key, status, started_at, completed_at, artifacts->>'pr_url'
FROM workflow_executions WHERE id = '<uuid>';

-- the obligation was credited
SELECT obligation_id, has_test_coverage, coverage_updated_at, test_scenario_ids
FROM obligations WHERE obligation_id = 'OBL-OFAC-FUZZY-001';
```

### 8.3 Failure modes & where they land in the data

| Stage | Failure | Recorded as | Bank-side check |
|---|---|---|---|
| Webhook auth | bad HMAC | 401 + log | Cloudwatch error count |
| Discovery | exception | `status=FAILED` + error in artifacts | `SELECT * FROM workflow_executions WHERE status='failed' AND started_at >= NOW() - INTERVAL '1 day';` |
| Generation | Bedrock timeout | `status=FAILED`, `artifacts.error` includes timeout message | Same |
| GitHub PR | token rejected | `pr_url=null`, status='degraded' (not green) | `SELECT artifacts->>'pr_url' FROM workflow_executions ORDER BY started_at DESC LIMIT 10;` (any nulls on `status='completed'` = bad token) |
| Obligation writeback | DB error | `artifacts.obligation_writeback_error` populated; obligations not credited | `SELECT artifacts->'obligation_writeback_error' FROM workflow_executions WHERE artifacts ? 'obligation_writeback_error' LIMIT 10;` |

A failed workflow is *not* a hidden silent error — every failure is a row in `workflow_executions` with a non-null `artifacts.error` field. The Workflows tab (§3.13) is the operator's window into this.

---

## 9. Common bank / examiner questions (FAQ)

**Q1. "The dashboard says 224 rules. My CCO says we had 228 last week. Where did 4 go?"**

Three possibilities, in order of likelihood:
1. **Tombstoning** — a regulator retired or replaced 4 rules. The audit trail:
   ```sql
   SELECT change_ref, rule_id, regulator, change_type, change_summary, created_at
   FROM regulatory_change_records
   WHERE change_type IN ('removed','superseded')
     AND created_at >= NOW() - INTERVAL '7 days'
     AND regulator <> 'OFAC';
   ```
2. **N/A overrides** — the CCO marked rules N/A. They count as `is_active=FALSE`:
   ```sql
   SELECT rule_id, status, reason, updated_at FROM rule_overrides
   WHERE status='not_applicable' AND updated_at >= NOW() - INTERVAL '7 days';
   ```
3. **Sync drift** — a P1→P2 sync delivered 4 fewer rules. Check `sync_version` advanced:
   ```bash
   curl -sk $P2_API/health/sync   # compare sync_version to your screenshot's value
   ```

**Q2. "Are obligations the same as rules?"**

No. A rule is the regulatory citation (e.g., 31 CFR 501.604). An obligation is a single requirement sentence within that rule's text (e.g., "Reports of blocked transactions shall be filed within ten business days"). One rule typically yields 2–8 obligations. Validation: §3.4 — the source_text of every obligation must appear verbatim in its parent rule's text.

**Q3. "How do I prove the SDN screening on Wednesday's wire used the right list?"**

Two queries:
```sql
-- (a) what list was active at the screening time
SELECT v.version_id, v.sha256_of_xml, v.published_at, v.ingested_at
FROM ofac_list_versions v
WHERE v.list_name='sdn' AND v.ingested_at <= '<screening time>'
ORDER BY v.ingested_at DESC LIMIT 1;

-- (b) the screening event itself
SELECT screening_id, party_name, decision, final_score, engine_mode, sdn_data_age_hours
FROM screening_events WHERE screening_id='<uuid>';
```
The first row's `sha256_of_xml` should match the SHA256 of the raw XML in `s3://<bucket>/scraper-chunks/sdn_xml_raw/<date>.xml.gz`. That is your chain of custody to OFAC.

**Q4. "If two CCOs are signing the same change at the same time, what wins?"**

The dashboard's sign-off endpoint is single-row INSERT into `change_record_dispositions`. The second submitter gets a 409 Conflict. The audit row records timestamp + JTI of whoever got there first.

**Q5. "Can a non-CCO accidentally generate a test?"**

No. `Generate ↗` is hidden when `canEdit=false` (the JWT group is not CCO or Admin). The backend additionally rejects POST `/webhooks/jira` calls that don't carry the bearer HMAC.

**Q6. "What if the dashboard's number conflicts with my SQL?"**

In order of preference: (a) trust the SQL (the code is right and the guide is wrong — that's an open doc fix); (b) check the filters — most discrepancies are timezone (UTC vs local) or `include_ofac=false` defaults; (c) escalate to engineering with the SQL output + the screenshot.

**Q7. "Can I export the live numbers as a one-shot CSV for my BA?"**

Yes — every export tile has CSV format. CSV is generated inline (no worker latency) and is the same canonical data the PDF renders. See §7 for column lists.

**Q8. "How fresh is the FFIEC data?"**

CF-bot-gated. The static encoding in `_build_regulations()` is the source of truth for `bsaaml.ffiec.gov` and `ithandbook.ffiec.gov`. We do not claim live coverage for those hosts. The CFR citations they encode are stable — we update them when the regulator publishes new bulletins (manual review). Memory: `project_ffiec_cf_gated`.

**Q9. "What's the worst-case if the obligation extractor over-extracts?"**

False positives in the obligation table inflate the denominator of coverage %, which makes coverage look lower than reality (conservative direction). The remediation is to mark the extracted-in-error obligation row inactive:
```sql
UPDATE obligations SET is_active=FALSE, deactivated_at=NOW(), deactivated_reason='regex_false_positive' WHERE obligation_id='<id>';
```
This requires write access — not part of the BA read-only role.

**Q10. "Can examiners see audit logs?"**

Yes. Three append-only tables:
```sql
SELECT * FROM regulatory_change_records WHERE rule_id='<rule>' ORDER BY created_at;
SELECT * FROM screening_events WHERE screening_id='<id>';
SELECT * FROM export_access_log WHERE artifact_id='<id>';
```
Every export download leaves a row in `export_access_log` (IP, JTI, status). Tampering would require unprivileged write access to those tables, which no Cognito role has.

---

## 10. Escalation paths

| Symptom | First thing to check | Who to call |
|---|---|---|
| Dashboard number ≠ your SQL | UTC timezone + `include_ofac=false` filters | Engineering (open a ticket with both numbers + the SQL) |
| `/health/sync` shows `FAILED` | CloudWatch logs `/fortress/pipeline2/testbank` | CIO + on-call |
| Empty `/changes` inbox + you know rules changed | `regulatory_change_records WHERE is_active=TRUE` (the connector may not have detected the diff) | Engineering |
| PDF export stuck in `rendering` | Worker logs `/fortress/p2-exports-worker/<bank>` | CIO |
| OFAC freshness > 24h | OFAC loader CloudWatch logs | CIO immediately (regulatory issue) |
| Workflow stuck in `running` > 10 min | Bedrock + GitHub token validity | Admin |
| Pass on HOLD blocked client-side | The 50-char notes guardrail (`PASS_HOLD_MIN_NOTES_CHARS`) | CCO writes longer notes or escalates |

---

## 11. Roadmap items the bank should know about

- **Findings / MRA pipeline.** The Reporting % readiness dimension is at a placeholder 40 until the findings ingest is live. ETA per current backlog: next quarter.
- **OFAC threshold calibration.** Pilot banks should expect a one-shot calibration exercise per `docs/runbooks/ofac-calibration-gold-set.md` before flipping `OFAC_SCREENER_MODE` from `legacy` to `vector` in production.
- **Section 1071 deadline.** July 2026. Small-business lending data capture rules. The CFPB connector tracks these; verify your bank's CCO has acknowledged the change records.
- **Network peer benchmarking.** Opt-in. Your CCO can choose to share anonymized `(asset_tier, regulation_id, coverage_pct)` to P1's aggregate; see `peer_benchmarks.py`. Default is off.

---

## 12. Reading list

- [`dashboard-user-guide.md`](dashboard-user-guide.md) — day-to-day workflow.
- [`dashboard-roles-guide.md`](dashboard-roles-guide.md) — per-role expectations.
- [`docs/dashboard_onboarding.md`](../dashboard_onboarding.md) — per-page reference with screenshots.
- [`docs/runbooks/ba-validation-postgres-access.md`](../runbooks/ba-validation-postgres-access.md) — how to open the read-only SQL session.
- [`docs/runbooks/ofac-calibration-gold-set.md`](../runbooks/ofac-calibration-gold-set.md) — threshold-tuning runbook.
- [`docs/runbooks/demo-readiness.md`](../runbooks/demo-readiness.md) — what an exam dry-run looks like.
- `src/api/dashboard_api.py` — the backend source of truth for every endpoint.
- `src/connectors/regulatory/usa/` — every regulator connector (see §5).
- `src/agents/{discovery,understanding,framework,generation_bedrock,validation,execution}.py` — the 6-agent pipeline (see §8).
- `src/exports/` — the canonical envelope, snapshots, CSV/PDF emitters, WORM storage (see §7).

> *If the number on a tab disagrees with this guide, the **code is right and the guide is wrong** — open a doc fix.*
