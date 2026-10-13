# Fortress Compliance Dashboard — User Guide

**Audience:** Compliance officers and operators at bank customers.
**Reading time:** ~30 minutes.
**Use:** desk reference for day-to-day work and onboarding.

---

## 1. What this dashboard does

The Fortress Compliance Dashboard is a single workspace for keeping a bank
examination-ready. It consolidates four things that normally live in four
separate teams:

| Workstream | What you do here |
|---|---|
| **Regulatory inventory** | See every rule, obligation, and deadline the bank is subject to, with up-to-date enforcement-risk scores. |
| **Regulatory change management** | Get notified of detected rule changes, sign off on disposition, leave an examiner-grade audit trail. |
| **OFAC / SDN screening** | Screen parties (customers, counterparties, wire beneficiaries) in real time. Triage matches. Record decisions. |
| **Examination evidence** | Generate point-in-time reports for examiners, the board, and internal audit. |

Every action you take is logged. Every decision is attributable to a named
user. The dashboard is designed so an examiner could walk in tomorrow and you
could hand them a single PDF that demonstrates the control environment.

---

## 2. Roles and what each can do

The dashboard supports five roles. The role you sign in with determines which
panels are visible and which buttons are enabled.

| Capability | CCO | CRO | CIO | Analyst | Admin |
|---|:-:|:-:|:-:|:-:|:-:|
| Generate tests, mark rules N/A, override thresholds | ✓ | | | | ✓ |
| Approve / sign off regulatory changes | ✓ | ✓ | | | ✓ |
| Screen parties, open & decide SDN cases | ✓ | ✓ | | ✓ | ✓ |
| View Vector Intelligence (model internals) | | ✓ (read) | | | ✓ |
| Tune thresholds, govern the model | | | | | ✓ |
| View Executive (Board) panel | ✓ | | | | |
| View Rules Performance | ✓ | ✓ | | | ✓ |
| View everything else (read-only) | ✓ | ✓ | ✓ | ✓ | ✓ |

**Practical guidance:**

- The **CCO** is the primary day-to-day user — examination readiness, rule
  governance, change sign-off, exports.
- The **CRO** consumes the same data through a risk lens — appetite,
  enforcement intel, peer benchmarks, change approvals.
- The **CIO** is read-only — used for board reporting on the technology
  posture, vendor risk, and uptime.
- **Analysts** live in the SDN screening surface — search, case queue,
  disposition.
- **Admins** govern the model — they look at vector distributions, alert
  precision, and (with CCO co-sign) adjust thresholds.

---

## 3. Signing in

The dashboard is hosted at a per-bank URL (e.g. `dashboard.yourbank.com`)
behind your IdP. Sign in with your email. On first sign-in you'll be prompted
to set a permanent password. Sessions are bound to your role; all activity
appears in the audit log under your name.

If a button is greyed out or a screen is hidden, that's your role telling you
the action isn't yours to take. Reach the appropriate owner instead of asking
for elevated access.

---

## 4. The home screen — Examination Readiness

This is the landing screen for every role. It answers one question:
**if an examiner showed up today, how would the bank score?**

### What you see

- **Overall readiness score** (0–100), with a band — Failing, Developing,
  Operational, or Strong.
- **Top two scoring gaps** in plain English. e.g. *"15 obligations are not
  mapped to a control"*, *"3 regulatory changes are awaiting sign-off."*
- **Risk × Coverage matrix** — a 3×3 grid (High/Medium/Low risk × Untested/
  Partial/Tested coverage). The top-left cell — high-risk, untested — is the
  one to watch. Click any cell to drill into the underlying rules.
- **10-point readiness scorecard** — Inventory, Control Mapping, Testing,
  Risk Assessment Currency, Change Management, Findings, Governance,
  Independent Testing, Reporting, Engine Integrity. Each scored 0–100. The
  bars are sorted lowest-first so the biggest drag is always on top.
- **Persona lens toggle** — CCO / CRO / CIO. Defaults to your role. Each
  lens re-weights the same underlying data:

| Lens | What it emphasizes |
|---|---|
| **CCO** | Control coverage, evidence completeness, open findings/MRAs. |
| **CRO** | Residual risk vs. appetite by domain, key risk indicators, peer enforcement signal. |
| **CIO** | Pipeline health, embedding integrity, uptime, architecture posture. |

### Business decisions

| You see | You do |
|---|---|
| Readiness band drops from Operational → Developing | Open the lowest-scoring dimension and triage. The "biggest drag" line tells you where. |
| High-risk untested cell > 0 | Click into it; for each rule, decide **generate test** or **mark N/A with reason**. |
| Engine Integrity < 90 | Hand off to the CIO — it's a tech-ops issue (data freshness, embedding coverage, or pipeline failure). |

---

## 5. Compliance Rules

The complete, live regulatory inventory the bank is subject to.

### What you see

- Searchable table — Rule ID, Regulator, CFR citation, obligation text,
  enforcement-risk score (red ≥ 0.8, amber 0.6–0.8, green < 0.6), test status.
- Filters: jurisdiction (federal / state), regulator, status.
- For each rule: how many automated tests cover it, and whether the bank has
  marked it Active, Not Applicable, or Override.

### Actions

| Action | Who | When |
|---|---|---|
| **Generate test** | CCO, Admin | High-risk rule with no test. Triggers the automation pipeline — JIRA story → generated test code → GitHub PR. Typical end-to-end time: ~5 minutes. |
| **Regenerate test** | CCO, Admin | The existing test is stale or failing; you want a fresh one based on the latest rule text. |
| **Mark N/A** | CCO, Admin | Rule does not apply to this bank (e.g. swap-dealer rules at a community bank). **Requires a written reason** — captured permanently in the audit trail. |
| **Override threshold** | CCO + Admin | The rule is correct but the standard threshold isn't right for this bank's risk profile. Requires explicit rationale. |

### Business decisions

- **Generate vs. N/A** — generate is the default. Only mark N/A when you can
  defend the rationale to an examiner (line of business doesn't exist,
  asset-size threshold, exempt charter, etc.).
- **Test failures don't auto-close the rule.** If the generated test fails
  in CI, the rule stays "Not Tested" and the workflow surfaces the failure
  on the Workflows screen. Decide whether to fix the test or revisit the
  control.

---

## 6. Regulatory Changes

The inbox of detected rule mutations — added, amended, repealed.

### What you see

- A card list, newest first. Each card carries: impact rating (High /
  Medium / Low), regulator badge, status (Detected / Signed off / Remediation
  Planned / Closed), summary, semantic diff (what changed in the rule text),
  reference ID, and detected date.
- The header shows a count of unsigned changes — your inbox depth.

### Actions

| Action | Who | What happens |
|---|---|---|
| **Sign off — Acknowledged** | CCO, CRO, Admin | No action required from the bank. Change is recorded as reviewed. |
| **Sign off — Requires action** | CCO, CRO, Admin | A JIRA epic is auto-created; the change moves to *Remediation Planned*. Use this when policy, procedure, training, or systems need to change. |
| **Sign off — Not applicable** | CCO, CRO, Admin | The change doesn't apply to this bank. **Always write a one-line reason** — the audit trail depends on it. |

### Business decisions

- **Don't let unsigned changes accumulate.** Examiners look at the unsigned
  queue as a leading indicator of change-management hygiene.
- **Requires action ≠ immediate fix.** It just means there's a remediation
  plan. Track it in JIRA; the dashboard shows you the link.
- **Use Not Applicable sparingly** and only for clear scope reasons. "We
  don't think it applies" is not a defensible audit note.

---

## 7. Enforcement Actions

Real-world peer-enforcement intelligence. Use this to anticipate where
examiners will focus next.

### What you see

- Paginated table (25/page): date, regulator, action type, institution name,
  violation summary (with source URL), penalty amount.
- Filter by regulator (OCC, CFPB, FinCEN, OFAC, FDIC).

### Business decisions

- The CRO uses this as a **forward indicator** — if peers are getting hit on
  Reg E disputes this quarter, the bank's Reg E controls deserve a refresh.
- The CCO uses it to **brief the board** — three recent peer actions are far
  more persuasive in a board memo than abstract risk scores.
- No actions on this screen — it's reference data. Use it to inform what you
  do elsewhere (generate tests, sign changes, update policy).

---

## 8. Compliance Deadlines

Upcoming filing and reporting deadlines.

### What you see

- The nearest deadline is pinned to the readiness hero. The full list
  (top 20) shows regulation ID, deadline name, date, and days remaining.

### Business decisions

- This is a **safety net**, not a workflow. It exists so a deadline never
  slips because it lived in someone's spreadsheet. If a deadline date here
  doesn't match the bank's calendar of record, raise it — the rule may have
  changed.

---

## 9. Obligations

Where the bank's regulatory obligations live, broken down by regulator and
coverage.

### What you see

- Three counters: total obligations, obligations with test coverage,
  obligations awaiting tests.
- A bar chart: obligations per regulator, with coverage proportion.

### Business decisions

- A regulator with **0% test coverage** is the first place to spend a
  test-generation budget. Click through to /rules filtered by that regulator
  and start generating.
- The "Awaiting tests" count should trend down quarter-over-quarter. If it
  isn't, the bank is falling behind on automation — escalate to CCO.

---

## 10. OFAC / SDN Screening — Engine Health

Proof that the screening engine is operational and current.

### What you see

- A clear status — **Operational & current** or **Refresh overdue** — and
  how old the list is.
- **24-hour SLA badge** — the list must be refreshed within 24h or the badge
  flips. This is the line examiners look for.
- Counts: SDN entries, name variants in use, embedded variants and the
  percentage covered.
- Expandable technical details — retrieval modes, model in use, deployment
  model (in your VPC), last refresh time.
- A small table listing the active screening rules and their thresholds.

### Business decisions

- **If the SLA badge flips** — that's a P1 incident. Pause new wires until
  the engine is current, or fall back to manual screening, per your bank's
  documented contingency. Notify the CIO.
- **Embedding coverage < 95%** — the model isn't blind, but it's working
  harder on the lexical path. Flag to Admin for backfill.

---

## 11. SDN Screening — Search

This is the Analyst's daily workspace. Search a party. Get a decision. Move on.

### What you see

- Form: **Name** (required), **Country** (ISO-2), **Date of birth**,
  **Customer ID**.
- On submit: a decision banner — **PASS**, **REVIEW**, or **HOLD** — a
  Final Risk Score (0–100), and the screening ID for audit reference.
- If there are matches: a table with SDN number, matched name, sanction
  programs, country, and four score columns (Fuzzy Match, Alias Match,
  Vector Similarity, Final Risk Score — all 0–100, integer).

### How to use

1. **Always include the Customer ID** for real screening. Without it, the
   audit row can't be linked back to your customer record.
2. **Country narrows results sharply.** A name-only search of "Mohammed
   Ahmad" returns many candidates; adding "IR" narrows to the relevant set.
3. **Date of birth disambiguates aliases** — the most powerful single field
   when you have it.
4. **The same search within 24 hours returns the prior result** (with a
   header flag). Don't keep re-screening to "see if it changed" — if the
   SDN list refreshes, the cache invalidates automatically.

### Decisions you record

| Decision | Meaning | Who can record |
|---|---|---|
| **Pass** | Cleared — false positive, overruled with rationale. | CCO (and Admin). The bar is intentionally high. |
| **Send to review** | Need more information or a second opinion. The case stays open in the queue. | Analyst, CCO, CRO, Admin. |
| **Escalate** | High-confidence match — block the transaction, file the SAR/blocked-property report, route to senior compliance. | Analyst, CCO, CRO, Admin. |

### Business decisions

- **PASS** with no matches → no further action.
- **REVIEW** → a case auto-opens. Move to the Cases screen.
- **HOLD** → a case auto-opens at higher priority. Block any pending
  transaction immediately; investigate before clearing.

---

## 12. SDN Screening — Cases

The case queue. Every match that wasn't a clean PASS lives here until it's
resolved.

### What you see

- Filters: status (Open / Closed / All), priority (Any / High / Medium),
  free-text search (by party name, SDN number, or customer ID).
- Table: case ID, priority, decision at open, matched name, SDN number,
  customer ID, event count, status, opened date.

### Business decisions

- **Work the high-priority open cases first.** Median time-to-decision is
  the metric examiners care about; queues that age past 48h are a finding
  waiting to happen.
- **Multiple events on one case** means the same party has been screened
  more than once in the 24h window. Read the history before deciding — the
  context may already exist.

---

## 13. SDN Screening — Case Investigation

The full file on one match. This is the screen you spend the most time on.

### What you see

- **Header** — priority, decision at open, customer ID, SDN number, event
  count, status.
- **Score breakdown** — four subscores so you can see *why* the engine
  matched (was it the name? the alias? the vector path?).
- **Matched entity card** — name, entity type, country, nationality,
  sanction programs, top aliases, remarks.
- **Decision history** — every prior decision on this case, who made it,
  when, and what they wrote.
- **Decision form** — your notes (required when escalating or passing),
  evidence (optional), and three buttons: Pass (CCO-only), Review, Escalate.

### How to use

1. **Read the matched entity card first.** Name overlap with a high-profile
   sanctioned individual on a different DOB / country is the most common
   false positive.
2. **Look at the subscores.** A high *Alias Match* with a low *Vector
   Similarity* often means a transliteration coincidence — not necessarily
   the real party.
3. **Document your reasoning in Notes.** Future you, your CCO, and the
   examiner will all read it. Be specific: cite the customer record, the
   transaction context, and the disambiguating evidence.
4. **Escalate when in doubt.** It's cheaper to escalate and stand down than
   to clear and explain a missed match later.

---

## 14. Rules Performance

For CCO / CRO governance: are the screening rules working?

### What you see

- KPI cards: lifetime alerts, true positives (count and %), false positives
  (count and %), open cases, precision (e.g. 0.873).
- A rule inventory: each active screening rule with its weight and status.
- Alert volume broken down by opening decision (PASS / REVIEW / HOLD).

### Business decisions

- **Precision trending down** → either the SDN list is loading new entries
  that the thresholds aren't calibrated for, or the bank's customer base
  has changed. Hand off to Admin for tuning.
- **A specific rule with all alerts being false positives** → that rule is
  miscalibrated for this bank. Admin should look at the threshold; CCO
  signs off on any change.
- **A rule with zero alerts in 90 days** → either it's a "clean" rule
  (good — it's protective) or it's broken. Cross-check with the Engine
  Health screen.

---

## 15. Vector Intelligence (Admin)

The model-internals view. Read-only for CRO; the Admin owns it.

### What you see

- Coverage: active SDN variants, embedded variants, embedding %.
- Score distribution (last 30 days, matches only) — histogram of Final
  Risk Score buckets.
- Top screened names with hit count and average score.
- Engine-mode split — how many calls went through each retrieval path
  (lexical fuzzy / trigram / vector).

### Business decisions

- This is the only screen that intentionally shows raw model output —
  embedding coverage percentages, distribution histograms. Use it to
  diagnose precision drops surfaced on Rules Performance.
- **Threshold tuning is a sensitive change.** The recommended flow:
  1. Admin proposes a threshold change with rationale and supporting
     calibration data.
  2. CCO co-signs before the change goes live.
  3. The change is logged with both signatures and the prior threshold.
- **Never tune in production based on a single day's data.** The
  recommendation is at least two weeks of alert history before any
  threshold move.

---

## 16. Executive Today (CCO Board View)

A one-glance board-level snapshot.

### What you see

- Today's screenings, today's alerts, open cases, lifetime confirmed true
  positives, lifetime false positives, SDN records in use.
- SDN list freshness and SLA compliance.

### Business decisions

- This screen is **read-only by design** — it's the artifact you screenshot
  for the morning board flash. Material movements (alert spike, SLA flip)
  should already have prompted action on other screens.

---

## 17. JIRA Workflow Executions

The evidence pipeline. Every test the system generates leaves a trail here.

### What you see

- Three KPIs: 7-day success rate, average duration (intake → PR), open
  failures.
- The latest 20 runs with their JIRA story key, status, start time,
  duration, and result (GitHub PR link on success, failure reason on
  failure).

### Business decisions

- **Success rate below 90%** is an automation-quality issue. Hand to the
  CIO to triage with the engineering team.
- **An open failure on a high-risk rule** means that rule still isn't
  tested. Decide whether to retry, accept the gap, or hand back for
  manual control implementation.

---

## 18. Examination Evidence & Reports

The export center. Six artifact types, generated on demand, dated and
hash-stamped.

### What you see

| Artifact | Purpose |
|---|---|
| **Examiner Evidence Package** | The single document an examiner walks away with. Inventory, control mapping, test thresholds, results, change history. PDF or CSV. |
| **OFAC Screening Attestation** | Dated proof of list freshness and tested controls. Used in OFAC examinations. PDF or CSV. |
| **Board / Audit Committee Report** | Quarterly coverage trend, signed changes, open findings, engine health. PDF. |
| **Findings & MRA Tracker** | Each open finding with owner, deadline, status, closure evidence. CSV or PDF. |
| **Regulatory Change Log** | Dated, signed record of every change and its disposition. CSV or PDF. |
| **Coverage Gap Report** | Obligations without tested controls — your own mock-exam tool. CSV or PDF. |

### Actions

- Pick an "as-of" date (defaults to today). Click the format you want.
  Download is immediate.
- Each export carries a version hash. Two exports of the same artifact at
  the same as-of date will produce the same hash — so an examiner can
  verify nothing was edited.

### Business decisions

- **Generate the Examiner Evidence Package quarterly**, store it, and use
  it as the baseline for the next quarter's reviews.
- **Generate the OFAC Attestation whenever the SDN list refreshes** if
  your examination cycle requires dated attestations.
- **The board report is for the audit committee** — generate it on their
  calendar, not yours.

---

## 19. End-to-end flows

The dashboard supports the same compliance work the bank does day-to-day,
expressed as flows that cross roles.

### Flow A — SDN screening (Analyst → CCO)

```
Analyst    → SDN Search → engine returns PASS / REVIEW / HOLD
                              │
                              ▼
                       Case auto-opens in queue (if not PASS)
                              │
Analyst    → Cases → Case Investigation
                              │
                              ▼
                       Records Pass / Review / Escalate
                              │
                              ▼
CCO        → Reviews escalations, signs off Pass-with-override
                              │
                              ▼
CCO        → Reports → OFAC Screening Attestation (for examiners)
```

Median elapsed time, party → final decision: under 5 minutes for clean
PASS, under 30 minutes for a typical REVIEW that needs a customer file.

### Flow B — Regulatory change (Detection → Evidence)

```
System detects rule change → Changes inbox
                                  │
                                  ▼
                           CCO opens, reads diff
                                  │
                                  ▼
            Acknowledged / Requires Action / Not Applicable
                                  │
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
            (no further)    JIRA epic      Audit note
                           created
                                  │
                                  ▼
                       Status → Remediation Planned
                                  │
                                  ▼
                Test generation, policy update, training
                                  │
                                  ▼
                       Status → Closed
                                  │
                                  ▼
CCO  → Reports → Regulatory Change Log (examiner evidence)
```

### Flow C — Test generation (Inventory → Evidence)

```
CCO → Compliance Rules → filter: high-risk untested
                              │
                              ▼
                     Generate (or Regenerate)
                              │
                              ▼
                JIRA story → Agent pipeline runs
                              │
                              ▼
                Workflows screen tracks progress
                              │
                              ▼
                  GitHub PR opened (test code)
                              │
                              ▼
                  Bank engineering reviews + merges
                              │
                              ▼
                  Rule moves to Tested in the inventory
                              │
                              ▼
                  Coverage % rises on Obligations panel
                              │
                              ▼
                  Readiness band may advance
```

### Flow D — Threshold tuning (Admin + CCO governance)

```
Rules Performance shows precision drift
                              │
                              ▼
Admin opens Vector Intelligence → inspects distributions
                              │
                              ▼
Admin proposes threshold change with calibration evidence
                              │
                              ▼
CCO co-signs (rationale, prior threshold, expected impact)
                              │
                              ▼
Threshold applied → audit row written
                              │
                              ▼
Rules Performance monitored for two weeks before next change
```

---

## 20. Quick reference — per role

### CCO

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Readiness home, Changes inbox | Cases (escalations), Rules (gaps) | Rules Performance, Workflows | Examiner Package, Board Report |

### CRO

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Readiness (CRO lens), Enforcement | Cases (high-priority), Changes | Rules Performance, Vector Intel (read) | Board Report |

### CIO

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Readiness (CIO lens), Engine Health | Workflows | — | Board Report (architecture section) |

### Analyst

| Daily | Weekly | Monthly |
|---|---|---|
| Search, Cases, Case Investigation | Review own queue aging | — |

### Admin

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Engine Health | Vector Intel | Rules Performance, threshold review with CCO | — |

---

## 21. Conventions, trust, audit

- **Every action is logged.** Sign-offs, decisions, overrides, exports —
  all carry the actor's email and a timestamp.
- **Scores shown to officers are 0–100 integers.** Raw model outputs
  (cosine similarities, embedding distances) appear only on Vector
  Intelligence, by design.
- **Date format** is ISO 8601 (`YYYY-MM-DD`) everywhere except the SDN
  search form, which accepts `dd-mm-yyyy` for officer convenience and
  normalizes server-side.
- **Idempotency window** for screening is 24 hours. Re-screening the same
  party returns the prior result unless the SDN list refreshed in the
  meantime — in which case the search re-runs automatically.
- **Exports carry a version hash.** Two exports of the same artifact at
  the same as-of date will produce identical hashes; this is the integrity
  signal for examiners.

---

## 22. Getting help

- **Operational issues** (a screen won't load, a button errors): contact
  the bank's internal compliance-tech team. Provide the screening ID,
  case ID, or workflow ID from the screen.
- **Compliance questions** (what should I do with this case?): follow the
  bank's documented escalation path — Analyst → CCO → CRO.
- **Product questions** (does the dashboard support X?): reach out to
  your Fortress customer success contact.

---

*Fortress Compliance Dashboard. This guide reflects the dashboard as
configured for your bank; some panels may be hidden if not provisioned in
your deployment.*
