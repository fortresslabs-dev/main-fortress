# Fortress Compliance Dashboard — Role-by-Role Guide

**Audience:** Compliance officers and operators at bank customers.
**Reading time:** ~30 minutes total; ~5–10 minutes per role section.
**Use:** desk reference. Read your role's section first, then skim the
others to understand the handoffs.

---

## 1. About this guide

This guide is organized by role. Each role section is self-contained: it
walks through the screens that role uses, the actions they can take, the
business decisions they need to make, and how their work hands off to
other roles. If you only ever work in one role, you can read just that
section. If you supervise multiple roles, read them in order.

For a screen-by-screen reference (organized by feature rather than role),
see `dashboard-user-guide.md` in this directory.

---

## 2. Roles and what each can do

| Capability | CCO | CRO | CIO | Analyst | Admin |
|---|:-:|:-:|:-:|:-:|:-:|
| Generate tests, mark N/A, override thresholds | ✓ | | | | ✓ |
| Approve / sign off regulatory changes | ✓ | ✓ | | | ✓ |
| Screen parties, open & decide SDN cases | ✓ | ✓ | | ✓ | ✓ |
| View Vector Intelligence (model internals) | | ✓ (read) | | | ✓ |
| Tune thresholds, govern the model | | | | | ✓ |
| View Executive (Board) panel | ✓ | | | | |
| View Rules Performance | ✓ | ✓ | | | ✓ |
| View everything else (read-only) | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## 3. Signing in (all roles)

Sign in at the bank's dashboard URL with your email. On first sign-in
you set a permanent password. Your role is bound to your sign-in; every
sign-off, override, decision, and export carries your name and a
timestamp.

If a button is greyed out or a screen is hidden, that's your role telling
you the action isn't yours to take. Route to the appropriate owner rather
than asking for elevated access.

---

# Part A — Chief Compliance Officer (CCO)

You're the primary owner of the dashboard. You see every screen, and you
hold the final word on rule generation, change sign-off, override
decisions, and examination evidence. This part walks through your typical
day, week, and quarter, and the business decisions that go with each
screen.

## A.1 The home screen — Examination Readiness

This loads first every time you sign in. It answers one question:
**if an examiner showed up today, how would the bank score?**

### What you see

- **Overall readiness score** (0–100) and band — Failing, Developing,
  Operational, Strong.
- **Top two scoring gaps** in plain English — e.g. *"15 obligations are
  not mapped to a control"*.
- **Risk × Coverage matrix** (3×3). The high-risk, untested cell is the
  one to act on first. Click any cell to drill in.
- **10-point readiness scorecard** — Inventory, Control Mapping, Testing,
  Risk Assessment Currency, Change Management, Findings, Governance,
  Independent Testing, Reporting, Engine Integrity. Sorted lowest first
  so the biggest drag is on top.
- **Lens toggle** — defaults to CCO (control coverage, evidence
  completeness, open findings/MRAs).

### What you do here

- **First thing each morning** — glance at the band. If it moved, find out
  why before standup.
- **Click the high-risk untested cell** — get a filtered list of rules to
  triage today.
- **Engine Integrity < 90** — hand off to the CIO; that's a tech-ops
  issue, not yours.

## A.2 Compliance Rules — your inventory

The complete, live regulatory inventory the bank is subject to.

### What you see

- Searchable table — Rule ID, Regulator, CFR citation, obligation text,
  enforcement-risk score (red ≥ 0.8, amber 0.6–0.8, green < 0.6), test
  status.
- Filters: jurisdiction (federal/state), regulator, status.
- For each rule: test count, and whether it's Active, Not Applicable,
  or Override.

### Your actions

| Action | When | What happens |
|---|---|---|
| **Generate test** | High-risk rule with no test | JIRA story → generated test code → GitHub PR in ~5 minutes. Engineering reviews + merges. |
| **Regenerate test** | Existing test is stale or failing | Fresh test based on the latest rule text. |
| **Mark N/A** | Rule does not apply to this bank | **Requires a written reason.** Captured permanently in audit. |
| **Override threshold** | Rule applies but the standard threshold isn't right | Requires explicit rationale. Co-signed with Admin if model thresholds. |

### Business decisions

- **Generate is the default.** Only mark N/A when you can defend the
  rationale to an examiner. "We don't think it applies" is not defensible.
- **Failed tests don't auto-close a rule.** It stays Not Tested; you decide
  whether to fix or revisit the control.
- **Don't override silently.** Examiners will ask why a threshold differs
  from the standard. Your rationale must be in the audit trail before they
  ask.

## A.3 Regulatory Changes — your inbox

The dashboard detects rule mutations (added/amended/repealed) and lands
them here. Don't let this queue grow.

### What you see

- Card list, newest first: impact rating, regulator, status, summary,
  semantic diff, reference ID, detected date.
- Unsigned-changes count in the header — your inbox depth.

### Your actions

| Decision | When | Outcome |
|---|---|---|
| **Acknowledged** | No action required | Recorded as reviewed. |
| **Requires action** | Policy/procedure/training/systems must change | JIRA epic auto-created. Status → Remediation Planned. |
| **Not applicable** | Truly out of scope for this bank | **Always include a one-line reason.** |

### Business decisions

- **Unsigned changes are a leading indicator** examiners look for. A queue
  with anything older than a week is a finding waiting to happen.
- **Requires action ≠ immediate fix.** It commits to a plan. Track in JIRA.
- The CRO has the same approval rights — agree with them on division of
  labor (e.g. CRO covers risk-rated changes; you cover everything else).

## A.4 Compliance Deadlines

Nearest filing/reporting deadline is pinned to the readiness hero. Full
list of upcoming 20 is on its own panel.

### Business decision

This is a safety net. If a date here doesn't match the bank's calendar of
record, raise it — the rule may have changed and a calendar update is
overdue.

## A.5 SDN Case Escalations

Analysts work the screening queue. Escalations land at your level.

### What you see

- Cases tab → filter status = Open, priority = High.
- Each case has a decision history, score breakdown, matched-entity card,
  and the analyst's notes.

### Your actions

| Action | When |
|---|---|
| **Pass (with override)** | You disagree with the screen; you have rationale and evidence that the customer isn't the sanctioned party. **Notes required.** Only you (and Admin) can pass an escalated match. |
| **Send to review** | Need more information from the line of business. Case stays open. |
| **Re-escalate to CRO** | Material risk or media exposure. Use the bank's escalation protocol; the dashboard logs the decision either way. |

### Business decisions

- **The pass-override bar is intentionally high.** Document the customer
  context, the disambiguating evidence (different DOB, different country,
  different program), and your conclusion. Future you and the examiner
  will both read these notes.
- **Aged cases > 48h** are a finding risk. Watch your team's queue depth.

## A.6 Rules Performance — screening governance

Is the screening engine doing its job?

### What you see

- Lifetime alerts, true positives, false positives, open cases, precision.
- Each active rule with its weight and status.
- Alert volume by opening decision (PASS/REVIEW/HOLD).

### Business decisions

- **Precision trending down** → coordinate with Admin on threshold review.
- **A rule with 100% false positives** → it's miscalibrated for this bank.
  Admin proposes a fix; you co-sign.
- **A rule with zero alerts in 90 days** → either it's protective (good)
  or it's broken. Cross-check against Engine Health.

## A.7 Executive Today (Board view)

Your board-flash screen. Today's screenings, today's alerts, open cases,
lifetime confirmed TPs, lifetime FPs, SDN records in use, SDN freshness.

### Business decision

Screenshot this for the morning board flash. Anything material here should
already have triggered action elsewhere — this is the artifact, not the
workflow.

## A.8 Examination Evidence & Reports — your output

Six artifact types, generated on demand, dated and hash-stamped.

| Artifact | When you generate it |
|---|---|
| **Examiner Evidence Package** | Quarterly, and on demand before any examination. |
| **OFAC Screening Attestation** | Whenever your examination cycle requires dated attestations. |
| **Board / Audit Committee Report** | On the audit committee calendar. |
| **Findings & MRA Tracker** | Monthly with the open-findings review. |
| **Regulatory Change Log** | Quarterly, and on examiner request. |
| **Coverage Gap Report** | Pre-exam mock review tool. Generate it, work the gaps, regenerate. |

### Business decisions

- **The version hash is your integrity proof.** Two exports at the same
  as-of date will produce the same hash. Examiners can verify nothing was
  edited.
- **Generate, then store.** Don't rely on the dashboard cache for evidence
  retention — put the file in your bank's document management system.

## A.9 Read-only screens you'll consult

| Screen | Why you'd open it |
|---|---|
| **Enforcement Actions** | Peer-enforcement intelligence. Brief the board with three recent peer actions. |
| **Obligations** | Coverage by regulator. Find your next investment of test-generation budget. |
| **OFAC Engine Health** | If the 24h SLA flips, pause new wires per your contingency plan. |
| **JIRA Workflow Executions** | If the success rate drops below 90%, hand to the CIO. |
| **Vector Intelligence** | Not yours to operate, but you may consult it before co-signing a threshold change. |

## A.10 Your cadence

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Readiness (CCO lens), Changes inbox, Case escalations | Rules (high-risk gaps), Workflows summary | Rules Performance with Admin, Findings/MRA Tracker | Examiner Package, Board Report, Coverage Gap Report |

## A.11 End-to-end flows you own

### Regulatory change (detection → evidence)

```
Change detected → Your inbox
                    │
                    ▼
    Acknowledged / Requires Action / Not Applicable
                    │
                    ▼     (Requires Action → JIRA epic)
              Remediation Planned
                    │
                    ▼
    Test gen + policy update + training
                    │
                    ▼
                  Closed
                    │
                    ▼
    Reports → Regulatory Change Log (examiner evidence)
```

### Test generation (inventory → coverage → readiness)

```
Compliance Rules → filter: high-risk untested
                    │
                    ▼
              Generate test
                    │
                    ▼
        JIRA story → PR via Workflows
                    │
                    ▼
   Engineering merges → rule moves to Tested
                    │
                    ▼
        Coverage % rises → Readiness band may advance
```

### Pass-with-override (case escalation)

```
Analyst escalates → Your case queue
                    │
                    ▼
        Read decision history + matched entity
                    │
                    ▼
        Notes: customer context + disambiguating evidence
                    │
                    ▼
        Pass (with override) — your name on the record
                    │
                    ▼
        Reports → OFAC Attestation includes this decision
```

---

# Part B — Chief Risk Officer (CRO)

You consume the same data as the CCO but through a risk lens — appetite,
peer enforcement, residual risk by domain. Your screen actions are mostly
read; your authoritative action is signing off on regulatory changes and
material case escalations.

## B.1 Home — Examination Readiness (CRO lens)

This is your most-used screen.

### What you see (CRO lens)

- **Overall readiness score** and band.
- **Residual risk vs. appetite by domain** — Sanctions, BSA/AML,
  Consumer, Privacy, Licensing. Each domain shows risk-weighted coverage
  vs. the appetite threshold you've set for the bank.
- **Key Risk Indicators (KRIs):**
  - High-risk untested rule count
  - Average remediation days (from change-detected to closed)
  - Overdue changes (> 60 days unsigned)
  - Coverage velocity (rules tested per month)
  - Risk-assessment age
- **Peer enforcement signal** — recent enforcement activity in peer-bank
  domains.

### What you do here

- **Daily** — confirm the band hasn't moved. If a KRI moves outside
  appetite, schedule a review.
- **Weekly** — look at coverage velocity. Trending down means the CCO
  needs more engineering time or more analyst time.
- **Click a domain bar** to see the rules driving residual risk.

## B.2 Regulatory Changes — sign-off

You share approval rights with the CCO. Agree on division of labor — a
common split is CRO covers risk-rated (high-impact) changes; CCO covers
the rest.

### What you see

- Card list, newest first: impact rating (High/Medium/Low), regulator,
  status, summary, semantic diff, reference ID, detected date.

### Your decisions

| Decision | When | Outcome |
|---|---|---|
| **Acknowledged** | No action required by the bank | Recorded as reviewed. |
| **Requires action** | Risk-rated change needs a remediation plan | JIRA epic auto-created. |
| **Not applicable** | Out of scope for this bank | **Always include a reason.** |

### Business decisions

- **For high-impact changes**, even when the CCO has authority, your
  co-sign in the audit trail is valuable evidence for the audit committee.
- **Watch the unsigned queue depth** — it's a leading indicator examiners
  use to assess change-management hygiene.

## B.3 Enforcement Actions — your forward indicator

Real-world peer enforcement data. This is the most uniquely-CRO screen.

### What you see

- Paginated table (25/page): date, regulator, action type, institution,
  violation summary (with source URL), penalty amount.
- Filter by regulator.

### Business decisions

- **If peers are getting hit on a domain this quarter, the bank's controls
  in that domain deserve a refresh.** Open a discussion with the CCO.
- **Three recent peer actions** are more persuasive in a board memo than
  abstract risk scores. Curate them for the quarterly board pack.
- **No actions on this screen** — it's reference data informing what you
  do elsewhere.

## B.4 SDN Case Escalations

You see the case queue. The CCO holds the pass-with-override authority,
but you can also send-to-review or escalate, and you'll be looped on
material cases.

### What you see

- Open cases, decision history, score breakdown, matched-entity card,
  analyst notes.

### Your actions

| Action | When |
|---|---|
| **Send to review** | Need more from line of business or legal. |
| **Escalate further** | Material risk, regulatory reporting consideration, media exposure. Use the bank's escalation protocol. |

### Business decisions

- **Watch case aging.** Cases open > 48h are a finding risk; the metric
  examiners care about is median time-to-decision.
- **Look for patterns.** Repeated escalations on the same customer or
  payment corridor signal a control gap, not just a series of one-off
  hits. Raise with the CCO.

## B.5 Rules Performance — screening governance

Are the screening rules working?

### What you see

- Lifetime alerts, true positives, false positives, open cases, precision.
- Each active rule with its weight and status.
- Alert volume by opening decision (PASS/REVIEW/HOLD).

### Business decisions

- **Precision below your acceptable threshold** → trigger a tuning review
  with Admin (CCO co-signs the change).
- **Heavy false-positive load** in one rule → the rule may need a
  threshold change or a different model path. Coordinate.

## B.6 Vector Intelligence — read access

You have **read** access to model internals. Admin owns the screen.

### What you see

- Active SDN variants, embedded variants and coverage.
- Score distribution histograms (last 30 days).
- Top screened names.
- Engine-mode split (lexical / trigram / vector).

### Business decisions

- Use this to **understand a precision drop** surfaced on Rules
  Performance. The distribution histogram often makes the cause obvious.
- **You don't tune thresholds** — Admin proposes, CCO co-signs. But your
  read access lets you challenge a proposed change before it ships.

## B.7 Read-only screens you'll consult

| Screen | Why |
|---|---|
| **Compliance Rules** | Spot-check the high-risk untested list to confirm coverage velocity numbers. |
| **Obligations** | Confirm regulator-by-regulator coverage. |
| **OFAC Engine Health** | 24h SLA badge — if it flips, expect a P1 incident escalation. |
| **JIRA Workflow Executions** | Confirm the test-generation pipeline is healthy. |
| **Examination Evidence & Reports** | Pull the Board Report quarterly for your section of the pack. |

## B.8 Your cadence

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Readiness (CRO lens), Enforcement | Cases (high-priority), Changes review | Rules Performance with CCO, Vector Intel (read) | Board Report (your sections) |

## B.9 Where you fit in cross-role flows

### Regulatory change (CCO drives, you co-sign on high-impact)

```
Change detected → CCO/CRO inbox
                    │
                    ▼
    (You sign off material risk-rated changes)
                    │
                    ▼
        JIRA epic → remediation → closed
                    │
                    ▼
    Change log export carries both signatures
```

### Threshold tuning (Admin + CCO own; you challenge)

```
Rules Performance shows precision drift
                    │
                    ▼
Admin proposes threshold change with rationale
                    │
                    ▼
        You review on Vector Intelligence
                    │
                    ▼
        CCO co-signs (or rejects with your input)
                    │
                    ▼
        Threshold applied; precision tracked for 2 weeks
```

### Case escalation (Analyst → CCO; you on material)

```
Analyst escalates → CCO queue
                    │
                    ▼
        Material case → CCO loops you in
                    │
                    ▼
        Your read of the file + risk perspective
                    │
                    ▼
        Decision recorded with both names
```

---

# Part C — Chief Information Officer (CIO)

Your view of the dashboard is **read-only**. You're not generating tests,
signing off changes, or working cases. You're the technology owner: the
dashboard's job, for you, is to demonstrate that the platform is
operating, that data is current, and that the architecture supports the
bank's compliance program. You'll quote this evidence in board packs,
vendor risk reviews, and SOC audits.

## C.1 Home — Examination Readiness (CIO lens)

Set this as your default view.

### What you see (CIO lens)

- **Overall readiness score** and band.
- **Pipeline success rate** (7 days) — the test-generation automation
  succeeded on this % of attempts.
- **Average run duration** — intake to GitHub PR. Track-and-trend.
- **Embedding index integrity** — count and percentage of SDN variants
  that have current embeddings.
- **Uptime** (30 days).
- **Architecture posture** — deployment model, data residency,
  connectors health, RBAC + audit summary.

### What you do here

- **Daily** — confirm pipeline success rate ≥ 90%, uptime ≥ 99.9%. If
  either dips, dig into Workflows.
- **Weekly** — embedding integrity should hold ≥ 95%; if it drifts,
  Admin runs a backfill.
- **Quarterly** — pull the architecture summary into the board pack.

## C.2 JIRA Workflow Executions — your operations screen

The evidence pipeline. Every test the system generates leaves a trail
here.

### What you see

- Three KPIs: 7-day success rate, average duration, open failures.
- Latest 20 runs: JIRA story key, status, start time, duration, result
  (GitHub PR link on success, failure reason on failure).

### What you do here

- **Success rate < 90%** is an automation-quality issue. Investigate the
  failure reasons; coordinate with engineering.
- **A persistent failure pattern** (same step, same regulator) often means
  a connector or rule extractor needs attention.
- **An open failure on a high-risk rule** means a control gap. Surface to
  the CCO so they decide whether to retry, accept the gap, or implement
  the control manually.

## C.3 OFAC / SDN Screening — Engine Health

Proof that the screening engine is operational and current.

### What you see

- **Status** — Operational & current / Refresh overdue.
- **24-hour SLA badge** — the SDN list must refresh within 24h or the
  badge flips.
- Counts — SDN entries, name variants in use, embedded variants and the
  coverage %.
- Retrieval modes — HNSW vector + trigram + tsvector.
- Deployment model — in your VPC.

### What you do here

- **If the SLA badge flips, it's a P1 incident.** Per the bank's
  contingency, compliance pauses new wires (or falls back to manual
  screening) until the engine is current. Coordinate the technical
  recovery; the CCO handles the operational fallback.
- **Embedding coverage < 95%** — the model isn't blind but is working
  harder on the lexical path. Schedule an Admin backfill.

## C.4 Read-only screens you may quote

| Screen | Why |
|---|---|
| **Compliance Rules** | When you brief on regulatory coverage breadth. |
| **Obligations** | Coverage-by-regulator chart for the board pack. |
| **Enforcement Actions** | Context for the risk narrative. |
| **Examination Readiness** (full) | Bank-wide posture snapshot. |
| **Examination Evidence & Reports** | View-only. CCO generates; you may quote KPIs. |

## C.5 Screens you won't use

- **Compliance Rules edit actions** — CCO/Admin.
- **Regulatory Changes sign-off** — CCO/CRO/Admin.
- **SDN Search / Cases / Investigation** — Analyst/CCO/CRO/Admin.
- **Rules Performance, Vector Intelligence** — CCO/CRO/Admin.
- **Executive Today** — CCO only.

These won't appear in your navigation. If you need to see one, the
Fortress configuration can be adjusted; raise it through customer success.

## C.6 Your cadence

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Readiness (CIO lens), Engine Health | Workflows summary | Vendor risk review materials | Board Report (architecture section) |

## C.7 Where you fit in cross-role flows

### Incident escalation — Engine Health SLA flip

```
Engine Health badge flips to "Refresh overdue"
                    │
                    ▼
        You investigate root cause
                    │
                    ▼
        Coordinate with engineering on recovery
                    │
                    ▼
        CCO executes operational contingency
                    │
                    ▼
        Post-incident review documented
```

### Pipeline degradation — Workflow failures

```
Workflows success rate < 90%
                    │
                    ▼
        Investigate failure pattern
                    │
                    ▼
        Engineering fix → next run validates
                    │
                    ▼
        CCO informed of any control gap during outage
```

## C.8 Architecture talking points

When you're briefing the board or vendor risk:

- **Deployment model** — Fortress runs inside the bank's VPC. Data does
  not leave the bank.
- **Identity** — bank-managed IdP, role-based access control, every
  action logged.
- **SDN data** — refreshed on a 24h SLA from authoritative OFAC sources;
  status visible on Engine Health.
- **Test generation** — automated end-to-end (rule → JIRA → PR);
  engineering reviews and merges; full audit trail on Workflows.
- **Evidence** — examiner-grade exports with version hashes for
  integrity verification.

---

# Part D — Compliance Analyst

You're frontline. You screen parties (customers, counterparties, wire
beneficiaries) and you record what happens with each match. Three
screens make up 95% of your day: **Search**, **Cases**, and **Case
Investigation**.

## D.1 SDN Screening — Search

This is where you start. Open the Search screen, screen the party, get
the decision, take the next step.

### The form

| Field | Required | What to put in |
|---|---|---|
| **Name** | yes | The full party name as it appears on your wire / KYC record. Don't pre-correct spelling — type it as-is. |
| **Country** | no | ISO-2 code (e.g. `IR`, `RU`, `KP`). Optional, but raises precision sharply. |
| **Date of birth** | no | `dd-mm-yyyy`. The most powerful single field for disambiguating aliases. |
| **Customer ID** | no, but always include for real screening | Your bank's internal customer reference. Links the audit row to the customer record. |

Click **Screen**.

### What you get back

A decision banner:

| Decision | Meaning | What to do |
|---|---|---|
| **PASS** | No match above threshold | No action; the audit row is written automatically. Move on. |
| **REVIEW** | Probable / fuzzy match — needs human review | A case auto-opens. Go to Cases. |
| **HOLD** | High-confidence match | A case auto-opens at high priority. Block any pending transaction immediately; investigate before clearing. |

Plus a **Final Risk Score** (0–100), a **Screening ID** for audit
reference, and (if there are matches) a table:

| Column | Read it as |
|---|---|
| SDN number | The OFAC SDN list identifier — useful when documenting. |
| Matched name | The exact SDN entry name (may differ from your input). |
| Programs | The sanction programs (e.g. DPRK3, NPWMD, RUSSIA-EO14024). |
| Country | The SDN entry's country. |
| Fuzzy Match | Lexical similarity (0–100). |
| Alias Match | Match against known aliases (0–100). |
| Vector Similarity | Semantic similarity (0–100). |
| Final Risk Score | The composite score that drove the decision (0–100). |

### Tips

- **Always include the Customer ID** for real screening. Without it, the
  audit row can't be linked back to your customer record.
- **Country narrows results sharply.** Name-only "Mohammed Ahmad" returns
  many candidates; adding `IR` narrows to the relevant set.
- **Same search within 24 hours returns the prior result.** Don't keep
  re-screening to "see if it changed" — if the SDN list refreshed in
  between, the cache invalidates automatically and the search re-runs.

## D.2 SDN Screening — Cases

After a REVIEW or HOLD, a case opens here. This is your queue.

### What you see

- **Filters:** status (Open / Closed / All), priority (Any / High / Medium),
  free-text search (by party name, SDN number, or customer ID).
- **Table:** case ID, priority, decision at open, matched name, SDN
  number, customer ID, event count, status, opened date.

### How to work the queue

1. **Open cases, high priority first.** Click the case ID to open the
   Investigation screen.
2. **Don't let cases age past 48 hours.** Median time-to-decision is the
   metric examiners watch. If you can't decide, send-to-review with a
   note explaining what you're waiting for.
3. **Event count > 1** means the same party was screened multiple times
   in 24 hours. Read the history before deciding — context may already
   exist.

## D.3 SDN Screening — Case Investigation

The full file on one match. This is where you spend the most time.

### What you see

- **Header** — priority, decision at open, customer ID, SDN number,
  event count, status.
- **Score breakdown** — four subscores (Fuzzy Match, Alias Match,
  Vector Similarity, Final Risk Score). Helps you see *why* the engine
  matched.
- **Matched entity card** — name, entity type, country, nationality,
  sanction programs, top aliases, remarks.
- **Decision history** — every prior decision on this case (who, when,
  what they wrote).
- **Decision form** — your notes (required), evidence (optional), and
  decision buttons.

### How to use

1. **Read the matched entity card first.** Name overlap with a
   high-profile sanctioned individual on a different DOB / country is
   the most common false positive.
2. **Check the subscores.** High Alias Match + low Vector Similarity
   often means a transliteration coincidence rather than the real party.
3. **Compare entity attributes** to your customer record. Different
   nationality, different DOB, different occupation? That's the
   disambiguating evidence the audit trail needs.
4. **Document your reasoning in Notes.** Be specific: cite the customer
   record, the transaction context, and the disambiguating evidence.
   Future you, your CCO, and the examiner will all read this.

### Decisions you can record

| Decision | When | Who can record |
|---|---|---|
| **Send to review** | Need more information — customer file, payment context, second opinion. | You. |
| **Escalate** | Confirmed or very likely match. Block the transaction. Triggers CCO/CRO review. | You. |
| **Pass (with override)** | Confirmed false positive with documented disambiguation. | CCO or Admin only. You cannot pass an escalated match. |

### When to escalate vs. send-to-review

| Situation | Recommended decision |
|---|---|
| Score high, DOB matches, country matches | **Escalate** |
| Score high, but customer file shows different DOB / nationality / occupation | Send to review with note; CCO may pass-override |
| Score moderate (60–80), aliases overlap but customer is legitimate retail | Send to review with note |
| Engine HOLD on a customer you've cleared previously | Send to review citing prior case ID; let CCO confirm the override is still valid |
| Time-sensitive wire, score high, uncertain | **Escalate** — when in doubt, escalate. It's cheaper than explaining a missed match. |

## D.4 Read-only screens you'll consult

| Screen | Why |
|---|---|
| **Examination Readiness** | High-level posture; usually just a glance. |
| **OFAC Engine Health** | If results look strange, confirm the engine is current. If the 24h SLA badge is red, escalate — don't keep screening into a stale system. |

## D.5 Your cadence

| Daily | Weekly |
|---|---|
| Search incoming parties, work open cases | Review your queue aging |

## D.6 Where you fit in the screening flow

```
You         → Search → engine returns PASS / REVIEW / HOLD
                            │
                            ▼
                     Case auto-opens (if not PASS)
                            │
You         → Cases → Case Investigation
                            │
                            ▼
                     Pass / Send to review / Escalate
                            │
                            ▼     (Escalations → CCO)
              CCO reviews escalation queue
                            │
                            ▼
              CCO passes-with-override (or confirms hold)
                            │
                            ▼
              All decisions logged → OFAC Attestation export
```

## D.7 Common situations — quick decisions

| Situation | Your move |
|---|---|
| Common Western name, no other attributes, low score | PASS auto-recorded; no action |
| Match on a politically-exposed person you can't identify in your customer file | Send to review; note "customer attributes incomplete — awaiting LOB confirmation" |
| Score 95+, full name + DOB + country match | Escalate immediately; block transaction; notify CCO |
| Wire from a sanctioned-program country (DPRK, IRAN, SYRIA, CUBA) | Escalate regardless of score; the program flag alone is enough to require CCO review |
| Customer is a corporation, SDN match is an individual with the same name | Confirm the entity type difference, send to review with note |
| You suspect a system issue (results obviously wrong, screening hangs) | Stop screening; check Engine Health; escalate to CCO and IT |

---

# Part E — Admin (Model Governance)

You govern the screening model. Your job is to keep precision high
without surrendering recall, to maintain the embedding index, and to
propose threshold changes when the data warrants it. You have the
strongest powers on the dashboard — and the most discipline expected.

You are **not** the everyday compliance officer. Most of your work is
diagnostic; your write actions go through CCO co-sign.

## E.1 Vector Intelligence — your home screen

This is the only screen that intentionally shows raw model output.
Embedding distribution, score histograms, retrieval-path mix. Use it to
diagnose precision and to inform threshold proposals.

### What you see

- **Coverage:** active SDN variants, embedded variants, embedding %.
- **Score distribution (last 30 days, matches only)** — histogram of
  Final Risk Score buckets. The shape tells you a lot — clean bimodal
  means thresholds are well-placed; a long tail at the threshold means
  many borderline calls.
- **Top screened names** with hit count and average score.
- **Engine-mode split** — how many calls went through each retrieval
  path (lexical fuzzy / trigram / vector).

### What you do here

- **Daily** — confirm embedding coverage ≥ 95%. If it drifts, run a
  backfill.
- **Weekly** — check the distribution; look for clustering near the
  decision threshold (indicates calibration drift).
- **Before any threshold proposal** — pull two weeks of distribution
  data; you'll need it for the rationale.

## E.2 Rules Performance — your alert-quality view

How are the screening rules actually performing?

### What you see

- KPIs — lifetime alerts, true positives (count + %), false positives
  (count + %), open cases, precision.
- Each active rule with its weight and status.
- Alert volume by opening decision (PASS / REVIEW / HOLD).

### What you do here

- **Precision below the bank's target** → trigger a tuning review with
  the CCO. Don't change thresholds unilaterally.
- **One rule generating disproportionate false positives** → propose a
  threshold change for that specific rule, not the whole engine.
- **A rule with zero alerts in 90 days** → confirm with Engine Health
  that it's protective (good) and not broken (bad).

## E.3 Compliance Rules — your write powers

You share write authority with the CCO on the rule inventory.

### Actions

| Action | When you use it |
|---|---|
| **Generate test** | High-risk rule with no test — same as CCO. Use sparingly; it's not your day job. |
| **Regenerate test** | Existing test is stale or failing. |
| **Mark N/A** | Rare for Admin — typically a CCO decision. |
| **Override threshold** | When the standard threshold doesn't suit the bank's risk profile. **Co-sign with CCO.** |

### Discipline

- **Default to CCO authority.** Use your write powers when a model-side
  change is the right answer (e.g. the rule is fine, the threshold isn't).
- **Document the rationale on every override** — what data drove the
  decision, what alternative was considered, what the prior threshold was.

## E.4 Threshold tuning — the most sensitive change

This is the action with the longest blast radius. Treat it as a
governance event, not a config change.

### The recommended flow

```
1. Rules Performance shows precision drift or false-positive volume.
2. You inspect Vector Intelligence:
   - score distribution near threshold
   - engine-mode split (where are the misses coming from?)
   - top screened names (any concentration?)
3. You assemble a proposal:
   - Current threshold, proposed threshold, expected impact
   - Two weeks of supporting alert-history data
   - Counterfactual: what does the proposed threshold do to the last
     30 days of true positives?
4. CCO reviews and co-signs.
5. You apply the change. The audit row captures both signatures, the
   prior threshold, and your rationale.
6. Monitor Rules Performance for two weeks. Don't move thresholds again
   in that window unless you've broken the metric.
```

### Don'ts

- **Don't tune in production on a single day's data.** Two weeks of
  alert history is the floor.
- **Don't tune in response to a single high-profile false positive.**
  One bad escalation isn't a threshold problem — it's a triage problem.
- **Don't tune without CCO co-sign.** Even if the system would let you,
  the audit trail demands the second name.

## E.5 OFAC Engine Health — your operational view

Proof that the screening engine is operational and current.

### What you see

- Status (Operational & current / Refresh overdue), 24h SLA badge,
  freshness age.
- SDN entry count, variant count, embedded variant count + %.
- Retrieval modes, deployment model.

### What you do here

- **24h SLA flips to overdue** → this is a P1. Coordinate with the CIO
  on technical recovery; CCO handles the operational fallback.
- **Embedding % drops** → schedule a backfill.

## E.6 SDN screening — you can use it

You hold `can_screen` rights, so the Search / Cases / Investigation
screens are open to you. In practice you'll use them only for:

- **Spot-checking** after a threshold change — screen a few known
  parties and confirm the decisions are what you expected.
- **Resolving an escalation** when the CCO is unavailable and you're
  the named backup. The pass-with-override audit will carry your name.

You are not the queue worker. That's the Analyst's job.

## E.7 Read-only screens you'll consult

| Screen | Why |
|---|---|
| **Examination Readiness** | Overall context. |
| **JIRA Workflow Executions** | If test generation is failing, confirm the model side isn't the cause. |
| **Examination Evidence & Reports** | View-only. CCO generates examiner artifacts. |

## E.8 Your cadence

| Daily | Weekly | Monthly | Quarterly |
|---|---|---|---|
| Engine Health, embedding coverage | Vector Intelligence distribution check | Rules Performance with CCO, threshold review | Model governance report (your sections of the board pack) |

## E.9 Where you fit in cross-role flows

### Threshold tuning (you propose, CCO co-signs)

```
Rules Performance shows precision drift
                    │
                    ▼
You diagnose on Vector Intelligence (2 weeks of data)
                    │
                    ▼
You write the proposal — current vs. proposed, impact, rationale
                    │
                    ▼
CCO reviews and co-signs
                    │
                    ▼
You apply; audit row captures both signatures + prior threshold
                    │
                    ▼
You monitor for 2 weeks before next change
```

### Engine integrity (SLA / backfill)

```
Engine Health badge flips or embedding % drops
                    │
                    ▼
You triage (data refresh? embed worker failure? capacity?)
                    │
                    ▼
Backfill or escalate to CIO + engineering
                    │
                    ▼
Engine restored; post-incident notes recorded
```

### Rules Performance review (monthly with CCO)

```
You pull last month's TP / FP / precision
                    │
                    ▼
Joint review with CCO — anything actionable?
                    │
                    ▼
        Either no change, or a threshold proposal kicks off
```

---

# Part F — Shared conventions & getting help

## F.1 Conventions & trust (all roles)

- **Every action is logged** with the actor's email and timestamp.
- **Scores shown to officers are 0–100 integers.** Raw model outputs
  (cosine similarities, embedding distances) appear only on Vector
  Intelligence, by design.
- **Date format** is ISO 8601 (`YYYY-MM-DD`) everywhere except the SDN
  search form, which accepts `dd-mm-yyyy` for officer convenience and
  normalizes server-side.
- **Idempotency window** for screening is 24 hours. Re-screening the
  same party returns the prior result unless the SDN list refreshed in
  the meantime — in which case the search re-runs automatically.
- **Exports carry a version hash.** Two exports of the same artifact at
  the same as-of date will produce identical hashes; this is the
  integrity signal for examiners.

## F.2 Getting help

- **Operational issues** (a screen won't load, a button errors): contact
  the bank's internal compliance-tech team. Provide the screening ID,
  case ID, or workflow ID from the screen.
- **Compliance questions** (what should I do with this case?): follow the
  bank's documented escalation path — Analyst → CCO → CRO.
- **Calibration questions** (is this threshold movement defensible?):
  Admin to CCO before acting; Fortress customer success if you need the
  model rationale documented in writing.
- **Product questions** (does the dashboard support X?): reach out to
  your Fortress customer success contact.

---

*Fortress Compliance Dashboard. This guide reflects the dashboard as
configured for your bank; some panels may be hidden if not provisioned in
your deployment.*
