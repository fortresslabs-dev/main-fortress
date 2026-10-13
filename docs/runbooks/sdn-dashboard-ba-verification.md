# SDN Dashboard — Business Analyst Verification Story

**Status:** BA acceptance script, 2026-07-04
**Owners:** BA (execution) · P2 dashboard (Agent 4) · validators (Agent 2)
**Audience:** Business Analyst signing off the SDN Search + Screening Cases
screens before release.
**Related docs (do not duplicate):**
- `sdn-search-manual-screening.md` — design + parity plan
- `sdn-dashboard-completion.md` — dashboard shell + role-gating
- `ofac-screening-api-contract.md` — backend contract
- `guides/bank-trust-validation-guide.md` §3.2 — SQL parity queries

This document is the **BA test script**: what to click, what to type, what to
see, and how to prove the numbers on screen match the database. Every scenario
is written as *given / when / then* so it maps 1:1 to a JIRA sub-task.

---

## 1. Prerequisites

- **Environments** (pick one per run):
  - Dashboard UI: `https://<bank>.dashboard.fortresslabs.dev` (default: `testbank`).
  - API base: `https://fortressapi.<bank>.dashboard.fortresslabs.dev`.
- **Test accounts** (see `dashboard-roles-guide.md`):
  - `analyst@testbank.dev` — group `Analyst`
  - `cco@testbank.dev` — group `CCO`
  - `cro@testbank.dev` — group `CRO`
- **Access tier the BA needs** — pick the lowest that covers the section
  being run (see §2.1 for the decision matrix):
  - **Postman + Cognito JWT** — covers §2, §4–§7. No infra access needed.
  - **SQL read-only tunnel** — only for §8 audit sweep and reconciling any
    dashboard number that looks off. Requires the managed policy
    `fortress-p2-ba-query-readonly-${BANK_ID}` on the BA's IAM identity and
    the `fortress_ba_ro` Postgres role (both provisioned once per bank — see
    `business_analyst_validation_guide.md` §12.0).
  - **AWS console** — read-only observability only (CloudTrail sessions, RDS
    `postgresql` log group, ECS jump-task lifecycle). Cannot produce row
    counts. RDS Query Editor is **unavailable** because P2 uses
    `AWS::RDS::DBInstance` (Postgres), not Aurora.
- **Browser DevTools** open on the Network tab — helpful when a stat looks
  off and you need to confirm the backend response, not the render.

---

## 2. Baseline health check — run **before** any use case

Goal: confirm the SDN list is loaded and fresh. If any of these fail, stop and
escalate to the CIO — the rest of the script depends on a healthy list.

| Check | Where | Expected | Verify (SQL) |
|---|---|---|---|
| SDN entries loaded | `/ofac` → Technical details | ~13 500 entries · ~28 000 name variants · ≥ 99 % embedded | `SELECT COUNT(*) FROM ofac_sdn_entries WHERE is_active=TRUE;` and `SELECT COUNT(*) FROM ofac_sdn_vectors;` |
| Freshness | `/ofac` header strip | Green · "updated < 24 h ago" | `SELECT EXTRACT(EPOCH FROM (NOW() - MAX(last_seen_at)))/3600.0 FROM ofac_sdn_vectors;` |
| Executive Today parity | `/dashboard/executive` | "SDN records (active)" matches the SQL above | Same query as row 1 |
| Engine mode | `/ofac` → Technical details | `legacy` or `vector` — record which; every screen row later must match | env `OFAC_SCREENER_MODE` |

The dashboard number and the SQL number must agree to the row. Screenshot both
and attach to the JIRA sub-task.

---

## 2.1 How to run the checks — access decision matrix

Default to Postman. Reach for SQL only when the API cannot produce the number
you need (basically §8) or when a Postman number disagrees with the dashboard
and you need to reconcile against raw tables. The AWS console is not a data
source for these checks — it is a session/audit surface only.

| BA verification step | Preferred path | API endpoint (Postman) | SQL fallback needed? |
|---|---|---|---|
| §2 SDN counts + freshness | Postman | `GET /api/v1/dashboard/ofac-health` | No |
| §2 Executive Today parity | Postman (as CCO) | `GET /api/v1/dashboard/screening/executive-today` | No |
| §4 Live screen | Postman (as Analyst) | `POST /api/v1/dashboard/screening/search` | No |
| §5 CCO Pass override | Postman (as CCO) | `POST /api/v1/dashboard/screening/cases/{case_id}/decision` `{"action":"pass"}` | No |
| §6 Escalate to CRO | Postman (as CCO), then Postman (as CRO) | Same endpoint, `{"action":"escalate"}` | No |
| §7 Case queue + `N total` | Postman | `GET /api/v1/dashboard/screening/cases?status=open` | No |
| §5–§6 Decision history | Postman | `GET /api/v1/dashboard/screening/cases/{case_id}` | No |
| §8 Audit-trail completeness | **SQL** | — | **Yes** — `screening_events` day-range sweep |
| Any dashboard number that disagrees with Postman | SQL | — | Yes — reconcile to raw tables |

**Why Postman covers so much:** every dashboard tile ultimately renders a
value from one of the endpoints above. Hitting the endpoint directly proves
both the number and the role-gate (a wrong JWT returns 401/403 — that failure
is itself a §5 CCO-only / §6 CRO-only sign-off check).

**Why the AWS console isn't enough:**
- `RDS Query Editor` requires an Aurora cluster; P2 runs single-instance RDS
  Postgres, so the Query Editor tab is inactive for this DB.
- CloudWatch `postgresql` log group (`/aws/rds/instance/fortress-p2-pg-${BANK_ID}-${ENV}/postgresql`)
  only proves *that* a login happened, not what the row count is.
- ECS console can confirm the BA jump-task ran but returns no SQL result.

### 2.1.1 Get a Cognito JWT for Postman (one-time per session)

1. Sign in to `https://<bank>.dashboard.fortresslabs.dev` as one of the three
   test accounts.
2. Open browser DevTools → **Application** → **Local Storage** (or
   **Session Storage**, depending on browser) → find the key that starts
   `CognitoIdentityServiceProvider.<pool-id>.<user>.idToken`.
3. Copy the value. That JWT is the BA's `id_token` — paste into Postman as
   `Authorization: Bearer <id_token>`.
4. Repeat when the token expires (default 1 h; you will see `401 Unauthorized`
   from the API).

Alternative (scriptable): `aws cognito-idp initiate-auth --auth-flow
USER_PASSWORD_AUTH --client-id <app-client-id> --auth-parameters
USERNAME=analyst@testbank.dev,PASSWORD=<pw>` and read `.AuthenticationResult.IdToken`
from the response. Client IDs live in SSM at
`/fortress/${BANK_ID}/cognito/dashboard-client-id`.

### 2.1.2 When to open the SQL tunnel

Only these situations warrant `scripts/start_ba_session.sh <bank>`:

- §8 audit-trail sweep — no API equivalent for the day-range `screening_events`
  query that proves every action left a row.
- Reconciling a Postman/dashboard mismatch back to a raw `COUNT(*)`.
- Ad-hoc inspection an examiner asks for (e.g. "show me every screen for
  customer X across all time").

Everything else stays in Postman. Full SQL setup lives in
`business_analyst_validation_guide.md` §12.0 — do not duplicate here.

---

## 3. Use-case fixtures — sample search names

All fixtures are DPRK / NPWMD SDN records that survive backfill and normalize
the same way in `legacy` and `vector` modes. If a fixture stops returning the
expected SDN number, page Agent 2 (`validators.py`) before proceeding.

| Test name | Expected top SDN | Expected decision @ default threshold | Notes |
|---|---|---|---|
| `Kim Chol Nam` | 20604 | HOLD (final ≥ 92) | Primary DPRK individual — the "gold" fixture used in §5–§7 below. |
| `Kim Chol` | 23483 | HOLD | Two-token AND-semantics check (fix from commit `7e91505`). |
| `Kim Sang-ho` | 22991 | HOLD | Hyphenated name — exercises apostrophe/hyphen fusing. |
| `Kim Kyu` | 17624 | HOLD | Short-name recall check. |
| `John Q Public` | *(none)* | PASS | Negative control — must produce zero matches and still write an audit row. |

Actual SDN numbers depend on the latest OFAC list; if a number has drifted,
update this table but keep the name — the shape of the test is stable.

---

## 4. Use case 1 — Analyst first-line screen

**Given** I am signed in as `analyst@testbank.dev`
**When** I open `/dashboard/search`, type `Kim Chol Nam`, and click **Screen**
**Then** I see, in order:
1. A red **HOLD** banner with a final risk score ≥ 92 and a screening id.
2. Freshness line: "SDN list refreshed *N* h ago" (< 24 h → no bump).
3. **Attached-case line**: "Attached to existing case CASE-XXXX…" — clickable.
4. A match table with SDN `20604` as row 1 and the four subscores
   (Fuzzy / Alias / Vector / Final) all populated 0–100.

**And** the same screen has written one row to `screening_events`:

```sql
SELECT decision, final_score, engine_mode, actor_user_id, created_at
FROM   screening_events
WHERE  party_name = 'Kim Chol Nam'
ORDER  BY created_at DESC
LIMIT  1;
-- decision=HOLD, engine_mode matches §2, actor_user_id = my Cognito sub
```

**Pitfall to check:** if the negative-control name `John Q Public` returns
PASS with zero matches, the audit row must still be written. Zero-match ≠
zero-audit.

---

## 5. Use case 2 — CCO override (HOLD → Pass with rationale)

Reproduces the "Reviewer notes — required (≥50 chars) to Pass a HOLD" guardrail
visible in the sample screenshot.

**Given** the case opened in §4 exists and I am signed in as `cco@testbank.dev`
**When** I open the case (`/dashboard/cases/CASE-XXXX`)
**Then** I see the score breakdown, matched entity (`Kim Chol Nam` · individual
· NPWMD · alias `Kim Chol Nam`) and the decision history table showing the
analyst's HOLD entry from §4.
**And** the **Pass (CCO override)** button is **disabled** until my notes
textarea has ≥ 50 characters.
**When** I type a 50+ char rationale (e.g. "Verified DOB and passport
mismatch with SDN 20604; confirmed distinct individual per KYC file 8842.")
and click **Pass**
**Then** the case row updates to `status = cleared`, `reviewer_action =
cleared_false_positive`, and my notes are stored verbatim.

Verify:

```sql
SELECT status, reviewer_action, reviewer_notes, resolved_by_user_id, resolved_at
FROM   screening_cases
WHERE  case_id = 'CASE-XXXX';
```

**Pitfall to check:** the same button is **not visible** when signed in as
Analyst. Sign back in as analyst and confirm — role-gating is a hard release
requirement.

---

## 6. Use case 3 — CCO → CRO escalation (matches the "Kim Chol Nam" sample)

This reproduces the exact decision-history pattern from the reference
screenshot: four CCO HOLDs at 04:08 / 04:13 / 04:18 / 04:37 followed by a CRO
HOLD at 05:07.

**Given** the case `CASE-ED2514090B9D`-style row (customer + SDN 20604) exists
and I have signed in as `cco@testbank.dev`
**When** I click **Escalate**, enter a rationale ("Refer to CRO — pattern of
inbound wires from sanctioned corridor, requires SAR review."), and submit
**Then** the case moves to `status = escalated` with
`reviewer_action = escalated`, a JIRA ticket is filed
(`jira_client.py` → project MDP, issuetype Idea — see memory
`project_jira_mdp_idea_issuetype`), and the case appears in the **Screening
cases** queue with the CRO group notified.

**When** I sign out and back in as `cro@testbank.dev`, open the same case,
review the decision history (must include every CCO HOLD row), and click
**HOLD** with my own reviewer notes
**Then** the decision history table appends the CRO row:

```
2026-07-04 05:07:22    cro@testbank.dev    HOLD    100    <mode>    kim
```

matching the reference screenshot exactly (actor + timestamp columns).

Verify escalation left a full audit trail:

```sql
SELECT actor_user_id, decision, final_score, engine_mode, created_at
FROM   screening_events
WHERE  criteria->>'external_customer_id' = '<the customer id>'
  AND  created_at::date = CURRENT_DATE
ORDER  BY created_at;
-- expect 4× cco rows then 1× cro row, decisions all HOLD
```

**Pitfall to check:** the JIRA ticket URL surfaced in the case sidebar must
open a real MDP issue; a 4xx here often means the bank's `JIRA_ISSUE_TYPE`
env is unset.

---

## 7. Use case 4 — Screening cases queue

**Given** I am signed in as `analyst@testbank.dev`
**When** I open `/dashboard/cases?status=open&priority=any`
**Then** the header shows `N total` where N matches:

```sql
SELECT COUNT(*) FROM screening_cases WHERE status = 'open';
```

**And** the table has the columns from the reference screenshot in this order:
`Case · Priority · Decision · Matched · SDN · Customer · Events · Status ·
Opened`.
**And** rows sort by `Opened` desc by default; every row for `Kim Chol Nam`
resolves to SDN `20604` and the row's **Events** count equals:

```sql
SELECT event_count FROM screening_cases WHERE case_id = '<row case>';
```

**Filter checks (all must pass):**
1. Free-text `kim` → returns every open case for a Kim-* party.
2. Priority `high` → all rows show a red `high` chip.
3. Status `closed` → returns the case cleared in §5.
4. URL param edit `?status=closed` reproduces the same result on a fresh tab
   (shareable link).

---

## 8. Cross-check — audit trail is complete

Run the "regulator-ask" query from `sdn-search-manual-screening.md` §10.3 for
the day you tested. Every scenario above must appear:

```sql
SELECT actor_user_id, party_name, decision, final_score, engine_mode, created_at
FROM   screening_events
WHERE  created_at::date = CURRENT_DATE
ORDER  BY created_at;
```

**Expected minimum row count for a full BA run:** 6 (one per use-case action,
plus the negative-control PASS from §3). If any use-case action from §4–§6 is
missing an audit row, that is a **release-blocking** finding — file against
Agent 2.

---

## 9. Sign-off checklist (attach to release JIRA)

- [ ] §2 baseline: SDN count and freshness match SQL to the row (screenshots).
- [ ] §4 analyst screen produces the HOLD banner + case attach line + audit row.
- [ ] §5 CCO override refuses to submit below 50-char notes; succeeds above.
- [ ] §5 Pass button is invisible to Analyst.
- [ ] §6 CCO escalation files a JIRA ticket; case reaches CRO queue.
- [ ] §6 decision history table shows the full CCO → CRO sequence.
- [ ] §7 case queue filters + `N total` all reconcile to SQL.
- [ ] §8 audit trail contains one row per action taken; nothing missing.
- [ ] Engine mode (§2) matches every `engine_mode` column value observed.
- [ ] Negative control (`John Q Public`) yields PASS + zero matches + an audit
      row.

Sign-off owner: BA. Countersign: CCO on §5, CRO on §6.

---

## 10. Out of scope

- Batch CSV screening (Phase 4 candidate).
- Consolidated Sanctions List parity (Phase 3 — see
  `sdn-search-manual-screening.md` §12).
- Automated wire-screening decisions coming from
  `validators.py::OFACScreener` (covered by `test_validators_e2e.py`, not
  this manual script).
- OFAC threshold recalibration (see `ofac-calibration-gold-set.md`).

---

## Appendix A — Postman collection stub

Import as a raw collection or recreate the four requests below by hand. All
requests share the same auth: `Authorization: Bearer {{id_token}}` and
`Content-Type: application/json`.

**Environment variables** (Postman → Environments → New):

| Variable | Value | Where to get it |
|---|---|---|
| `base_url` | `https://fortressapi.testbank.dashboard.fortresslabs.dev` | Bank-specific — see §1. |
| `id_token` | `<paste JWT>` | §2.1.1 — DevTools localStorage. |
| `bank_id` | `testbank` | §1 default. |
| `customer_id` | `TEST-CUST-KIMCHOLNAM-001` | Any stable string; drives §6 case-attach. |
| `case_id` | `(fill in after §4)` | Returned by the search request when a case attaches. |

### A.1 Baseline — SDN health

```
GET {{base_url}}/api/v1/dashboard/ofac-health
```

Expected 200 body includes `sdn_entries`, `name_variants`, `embeddings_ready`,
`last_synced_hours_ago`. Use these directly for §2.

### A.2 Live screen — Kim Chol Nam (§4)

```
POST {{base_url}}/api/v1/dashboard/screening/search
{
  "party_name": "Kim Chol Nam",
  "party_country": null,
  "external_customer_id": "{{customer_id}}",
  "party_dob": null,
  "request_kind": "dashboard"
}
```

Save `case_id` from the response into the Postman env for the next requests.
Assert `decision == "HOLD"`, `final_score >= 92`, top match `sdn_number == "20604"`.

### A.3 CCO Pass override (§5)

Swap `{{id_token}}` for a CCO JWT before running.

```
POST {{base_url}}/api/v1/dashboard/screening/cases/{{case_id}}/decision
{
  "action": "pass",
  "reviewer_notes": "Verified DOB and passport mismatch with SDN 20604; confirmed distinct individual per KYC file 8842.",
  "reviewer_evidence": null
}
```

Expected: 200 with `status="cleared"`, `reviewer_action="cleared_false_positive"`.
Try again with an Analyst JWT — must return 403.
Try again with <50-char notes on a HOLD case — must return 400.

### A.4 Escalate to CRO (§6)

CCO JWT:

```
POST {{base_url}}/api/v1/dashboard/screening/cases/{{case_id}}/decision
{
  "action": "escalate",
  "reviewer_notes": "Refer to CRO — pattern of inbound wires from sanctioned corridor, requires SAR review.",
  "reviewer_evidence": null
}
```

Then swap to CRO JWT and `GET /api/v1/dashboard/screening/cases/{{case_id}}`
— the response's `decision_history` array must include the CCO escalate row
followed by the CRO's subsequent HOLD row (mirrors the reference screenshot).

### A.5 Case queue (§7)

```
GET {{base_url}}/api/v1/dashboard/screening/cases?status=open&priority=high&q=kim
```

Expected: `total` matches the count on `/dashboard/cases` UI; each row's
`event_count` matches the UI's **Events** column.

---

## Appendix B — SQL fallback (§8 only)

Reserved for the audit sweep and dashboard-mismatch reconciliation. Setup +
tunnel commands live in `business_analyst_validation_guide.md` §12.0 — do not
duplicate here. The one query this doc requires:

```sql
-- §8 — every action taken today must appear as a row here.
SELECT actor_user_id, party_name, decision, final_score, engine_mode, created_at
FROM   screening_events
WHERE  created_at::date = CURRENT_DATE
ORDER  BY created_at;
```

If a use-case action from §4–§6 is missing from the result set, that is a
release-blocking finding — file against Agent 2 (`validators.py`).
