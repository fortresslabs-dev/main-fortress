# Examination Readiness — Parameter Audit & Value Justification

**Audit date:** 2026-07-03
**Branch:** `feat/ftr-readiness-phase1`
**Sibling doc:** `docs/examination_readiness_fix_plan.md` — this document covers
*what is wrong and why it matters per persona*; the fix plan covers *how to fix*.

---

## 1. Purpose

The Examination Readiness page is the executive landing surface for CCO, CRO and
CIO. Because it composes ten sub-metrics into one gauge, any single widget that
disagrees with the others erodes trust in the whole product. This document:

1. Records the internal-consistency findings surfaced during the 2026-07-03
   review.
2. Categorises each finding as **business-logic**, **scraping / ingestion**,
   **data-model**, **phantom** (hardcoded), or **definition** (widget label vs
   query mismatch).
3. Justifies each of the ten parameters against the value each persona (CCO,
   CRO, CIO) is buying when they open the page.

The dashboard is functionally correct — every query on the backend runs and
returns real numbers where a table is populated. The problem is that the
widgets read from **two disjoint denominators** (`compliance_rules` and
`obligations`), two of the ten values are **hardcoded**, and the risk classifier
is present but **starved of upstream data**, so the matrix collapses to all-Low.

---

## 2. Root cause — one screen, two denominators

Widgets on the same page read from two different tables and never reconcile:

| Widget | Denominator |
|---|---|
| Obligation inventory, Risk-assessment currency, Governance evidence, Independent testing, Engine integrity | `compliance_rules` |
| Control mapping, Testing & rationale, "81 obligations unmapped" callout | `obligations` |
| Risk × Coverage matrix ("23 tested") | `compliance_rules` joined to `workflow_executions` |
| Findings panel | Empty stub — the `findings` table exists but the API returns `[]` |

Consequence: `Control mapping 100%` is *not* "we control-mapped 100% of the
bank's obligations." It is "of the obligations we extracted, 100% have a
non-null regulator label." A CCO reads that as "we're done." An examiner reads
it as noise.

Every fix in §4 that is tagged `definition` traces back to this root cause.

---

## 3. Score composition (recap for reference)

`GET /api/v1/dashboard/readiness` returns
`round(Σ(dimᵢ × wᵢ) / Σ wᵢ)` (`src/api/dashboard_api.py:752–760`).

Persona lens weights (`src/api/dashboard_api.py:590–607`):

| Dimension | CCO | CRO | CIO | Highest lens — reason |
|---|---|---|---|---|
| Inventory | 1.2 | 0.9 | 0.7 | CCO — foundation of everything downstream |
| Control mapping | 1.3 | 1.0 | 0.8 | CCO — day-to-day compliance operation |
| **Testing** | **1.5** | 1.2 | 0.9 | CCO — the artifact examiners ask for first |
| Risk-assessment currency | 1.0 | **1.4** | 0.8 | CRO — is our risk model up to date |
| Change management | 1.3 | 1.0 | 0.9 | CCO — regulatory delta backlog |
| **Findings** | 1.1 | **1.6** | 0.8 | CRO — MRAs are the CRO's scorecard |
| Governance | 1.2 | 1.0 | 0.8 | CCO — board and committee evidence |
| Independent testing | 1.0 | **1.3** | 0.9 | CRO — is testing separated from ops |
| Reporting | 0.9 | 0.9 | **1.3** | CIO — is the report engine reliable |
| **Engine integrity** | 0.6 | 0.7 | **1.7** | CIO — platform health |

CCO weights favour *operational compliance completeness*. CRO weights favour
*risk-assessment currency, findings closure, independent testing*. CIO weights
favour *platform reliability and report delivery*. The persona split is coherent
in intent; the underlying data is not yet coherent to support it.

Bands (`_band()` at `dashboard_api.py:610–614`):
`<40 failing • <70 developing • <85 operational • ≥85 strong`.

---

## 4. Per-parameter deep dive

Each parameter is scored on:
- **Source** — where the number literally comes from (file:line).
- **Category** — the class of change needed. One of:
  `phantom` · `business logic` · `scraping / ingestion` · `data model` · `definition`.
- **Value to CCO / CRO / CIO** — what this parameter is buying the persona.
- **Verdict** — what to do.

---

### P1 — Obligation inventory (shows 4%)

- **Source.** `pct(rules_with_cfr, rules_total, 70)` — `dashboard_api.py:628–631, 693`.
  Numerator: `compliance_rules WHERE cfr_citation IS NOT NULL AND cfr_citation<>''`.
  Denominator: all active `compliance_rules`.
- **What's wrong.**
  - **Denominator is what we scraped, not what the bank owes.** A 100% score
    means "every rule we ingested has a CFR citation." It does *not* mean "we
    have every obligation the charter requires."
  - There is no per-charter / per-asset-tier target obligation set anywhere in
    the codebase. No config, no table, no fixture.
  - The label says "Obligation inventory," the query counts *rules*. Two
    different concepts.
- **Category.** `data model` + `definition`. Introducing an
  applicable-obligation registry is a new table; renaming the widget is a
  definition fix.
- **Value — CCO.** This is the *foundation* metric — every downstream metric is
  a fraction of this. If the CCO cannot defend the denominator, no other number
  on the page is defensible either. **Highest CCO leverage.**
- **Value — CRO.** Modest direct value; the CRO reads this as "are we exposed
  because we're missing regulations we should have."
- **Value — CIO.** Low direct value; treated as an input signal to the ingestion
  pipeline's success rate.
- **Verdict.** Ship an applicable-obligation registry (per charter, per asset
  tier) and change the denominator. Until then, present the metric as
  "Ingestion completeness" — honest, and defensible.

---

### P2 — Control mapping (shows 100%)

- **Source.** `pct(obl_attributed, obl_total, 70)` — `dashboard_api.py:651–654, 694`.
  Numerator: `obligations WHERE regulator IS NOT NULL AND regulator<>'unknown'`.
  Denominator: all active `obligations`.
- **What's wrong.**
  - The query has no relationship to *controls*. It measures whether the
    obligation has a regulator label attached.
  - A real "control mapping" query would join `obligations → controls` via a
    mapping table. `ControlRuleMapping` exists at `database.py:704–716`, but
    that joins controls to **rules**, not obligations.
  - Result: the widget most likely to be confused with "we have controls in
    place for our obligations" is the widget that measures the least.
- **Category.** `definition` (rename) **or** `data model` + `business logic`
  (build an obligation→control mapping table + populate it).
- **Value — CCO.** Highest strategic value in the whole rubric — "for every
  obligation, do we have a control?" is the examiner's second question after
  "do you know your obligations?" Currently the widget answers a lesser
  question and looks like it answered the greater one.
- **Value — CRO.** Meaningful when honest: unmapped obligations = unmitigated
  inherent risk.
- **Value — CIO.** Low direct value.
- **Verdict.** Either rewrite the query against a real obligation↔control
  mapping (schema addition), or rename the widget to "Obligation attribution."
  The current pairing is the single most misleading widget on the page.

---

### P3 — Testing & rationale (shows 0%)

- **Source.** `pct(obl_covered, obl_total, 0)` — `dashboard_api.py:647–650, 695`.
  Numerator: `obligations WHERE has_test_coverage=TRUE`.
- **What's wrong.**
  - `has_test_coverage` is written `FALSE` at obligation creation
    (`obligation_extractor.py:423`) and **no code path in the repo sets it
    `TRUE`**. Verified: `grep -r "has_test_coverage\s*=\s*True"` → 0 matches.
  - The 6-agent test-generation pipeline never receives an `obligation_id` in
    its context, so even a successful test run has no target to write back to.
- **Category.** `business logic`. See `examination_readiness_fix_plan.md` §Fix 1
  for the discovery→execution thread + writeback SQL.
- **Reconciliation with the matrix.** The matrix's "23 tested" cell measures
  *rules* with a completed `workflow_executions` row
  (`dashboard_api.py:637–642`). Testing dimension measures *obligations*. Same
  concept, two tables, no shared identifier — the numbers can never agree until
  the writeback thread lands.
- **Value — CCO.** Weight 1.5 — highest weight in the CCO lens. This is what
  examiners open to: "show me your control testing." **Load-bearing metric.**
- **Value — CRO.** Weight 1.2 — CRO reads testing as coverage of the risk map.
- **Value — CIO.** Weight 0.9 — indirect (reliable test pipeline).
- **Verdict.** Highest single-fix leverage on the readiness score. Wire the
  writeback.

---

### P4 — Risk-assessment currency (shows 0%)

- **Source.** `pct(rules_with_risk, rules_total, 60)` — `dashboard_api.py:632–636, 696`.
  Numerator: `compliance_rules WHERE enforcement_risk_score IS NOT NULL AND > 0`.
- **What's wrong.**
  - `enforcement_risk_scorer` code exists but is **not called from the scrape
    cycle**. Rules are ingested with `enforcement_risk_score = NULL`, so the
    numerator is 0.
  - Downstream consequence — this is the input the **risk × coverage matrix**
    uses to bucket rules (High ≥ 0.8, Med ≥ 0.6, else Low; classifier at
    `dashboard_api.py:815`). All-NULL → all-Low. The empty red quadrant is not
    "we are clean," it is "the classifier is starved."
- **Category.** `scraping / ingestion`. The scorer exists; the scrape scheduler
  is not invoking it.
- **Value — CCO.** Fair (weight 1.0). CCO reads this as "does our risk view
  reflect current enforcement climate."
- **Value — CRO.** **Highest CRO weight (1.4).** The CRO's job is to keep the
  risk assessment current; without scoring the CRO has no lens at all.
- **Value — CIO.** Low direct value.
- **Verdict.** Wire the scorer into `scraper_scheduler.py`. One fix
  simultaneously unlocks P4, P7 (which reuses the same numerator), and the
  entire risk matrix.

---

### P5 — Change management (shows 0%; "226 changes unsigned")

- **Source.** `pct(chg_total − chg_open, chg_total, 80)` — `dashboard_api.py:660–667, 697`.
  `chg_open = regulatory_change_records WHERE status='detected' AND regulator<>'OFAC'`.
- **What's wrong.**
  - The metric is live and honest. The backlog is real: 226 detected change
    records with no reviewer sign-off.
  - The sign-off endpoint exists
    (`PATCH /api/v1/dashboard/changes/{ref}/signoff`) but there is no bulk
    action; clearing 226 records one at a time is process-hostile.
  - No SLA on time-to-sign-off, so the metric is binary (signed or not) rather
    than aging-aware.
- **Category.** `business logic` — bulk-review UX; optionally SLA aging.
- **Value — CCO.** High (weight 1.3). This is "did we handle every regulatory
  delta." Also drives the "226 changes unsigned" business-read callout.
- **Value — CRO.** Fair (weight 1.0). CRO cares more about risk-scored deltas
  than sign-off count.
- **Value — CIO.** Fair (weight 0.9).
- **Verdict.** Ship a bulk-signoff surface; add an aging bucket. Metric itself
  is trustworthy.

---

### P6 — Findings remediation (shows 80%)

- **Source.** **Hardcoded `findings = 80`** — `dashboard_api.py:698`.
- **What's wrong.**
  - Panel copy says "No findings store wired up yet." Parameter shows 80.
    These are directly contradictory on the same screen.
  - The `findings` table **exists** — `database.py:758–789` (model) and
    `alembic/versions/042_p2_exports_schema.py:226–257` (migration). It is
    fully specified for the OCC 5 Cs (concern, cause, consequence, corrective
    action, commitment).
  - `GET /api/v1/dashboard/findings` at `dashboard_api.py:995–1002` returns
    `{"findings": [], "count": 0, "overdue": 0}` unconditionally.
  - Nothing populates the table yet, but the display shortcut of "80" while
    the panel says "empty" is the single most damaging inconsistency on the
    page — an examiner who catches one phantom metric re-checks every metric.
- **Category.** `phantom`. Two fixes:
  1. Immediate (≤1 hour): change L698 to `None` / `N/A` and render an explicit
     "not measured yet" state.
  2. Follow-on: build the findings ingest → the schema is ready.
- **Value — CCO.** Weight 1.1. CCO's regulator-facing artifact.
- **Value — CRO.** **Highest CRO weight (1.6) of any dimension.** Findings
  closure *is* the CRO's scorecard. A phantom 80 here is a phantom badge for
  the CRO.
- **Value — CIO.** Low (weight 0.8).
- **Verdict.** **Priority #1 on the audit list.** Kill the phantom today; add
  the ingest path in the next sprint.

---

### P7 — Governance evidence (shows 0%)

- **Source.** `pct(rules_with_risk, rules_total, 80)` — `dashboard_api.py:699`.
  **Same numerator as P4.**
- **What's wrong.**
  - Reuses the risk-score coverage query. Governance evidence and
    risk-assessment currency are different concepts (board minutes, committee
    charters, BSA officer designation, training records vs. up-to-date risk
    scores) but share a query.
  - There is no board/committee artifact store in the schema.
- **Category.** `data model` + `definition`. Needs its own table plus its own
  query.
- **Value — CCO.** Weight 1.2. Examiners ask for board packet acknowledgements
  and BSA officer designation; this metric should reflect their presence.
- **Value — CRO.** Weight 1.0.
- **Value — CIO.** Weight 0.8.
- **Verdict.** Medium-term. Requires new schema. In the short term, split the
  widget from P4 to at least stop the double-count of a single numerator.

---

### P8 — Independent testing (shows 10%)

- **Source.** `pct(tested, rules_total, 50)` — `dashboard_api.py:637–642, 700`.
  Numerator: `DISTINCT rule_id` with any completed `workflow_executions`.
- **What's wrong.**
  - The number is real and small. Reflects genuine sparse test runs.
  - "Independent" is the definitional stretch: the query doesn't check whether
    testing was performed independently of the control owner (internal audit
    vs. control owner). It counts *anything* that ran through the pipeline.
- **Category.** `definition`. Splitting real "independent" testing from
  "any testing" requires either a separate workflow class or a role/actor tag
  on `workflow_executions`.
- **Value — CCO.** Weight 1.0.
- **Value — CRO.** **Weight 1.3.** Independent testing is a distinct BSA pillar
  from ordinary control testing; CRO relies on this to sign off on the risk
  assessment.
- **Value — CIO.** Weight 0.9.
- **Verdict.** Tag workflow executions with an actor role, and filter to
  independent-audit actors only for this widget. Rename in the interim to
  "Testing execution rate" — honest for now.

---

### P9 — Point-in-time reporting (shows 40%)

- **Source.** **Hardcoded `reporting = 40`** — `dashboard_api.py:701`.
- **What's wrong.**
  - Second phantom on the page (alongside P6). No persistent export audit log
    exists yet.
  - Unlike P6 the panel doesn't warn the reader that this number is
    placeholder — worse offence in principle, less visually contradicted.
- **Category.** `phantom`. Kill immediately; then wire when export audit log
  lands.
- **Value — CCO.** Weight 0.9.
- **Value — CRO.** Weight 0.9.
- **Value — CIO.** **Weight 1.3 — highest CIO weight after Engine integrity.**
  For the CIO the report generator *is* the product — if reports don't
  reliably tie back to evidence, the platform has failed at its core promise.
- **Verdict.** Kill the phantom today; treat the export audit log as a CIO-lens
  P0.

---

### P10 — Engine integrity (shows 100%)

- **Source.** `round((freshness + embed_ok + pipeline_ok) / 3)` —
  `dashboard_api.py:700–712`.
  - `freshness`: SDN `last_seen_at` recency (24h/72h/older buckets).
  - `embed_ok`: `ofac_sdn_vectors WHERE embedding IS NOT NULL / total`.
  - `pipeline_ok`: 7-day `workflow_executions` success rate.
- **What's wrong.**
  - This is **our platform's health**, not the bank's compliance posture.
  - Sitting inside the ten-parameter rubric it reads as "the bank scores 100 on
    engine integrity," which is nonsense — the bank has no engine.
  - Even at weight 0.6 in the CCO lens it inflates the composite by ~4–6
    points against a Failing baseline.
- **Category.** `definition` — either exclude from the customer-facing
  composite, or move to a labelled "Platform health" sub-section.
- **Value — CCO.** Weight 0.6 — should be zero; CCO doesn't own the platform.
- **Value — CRO.** Weight 0.7 — should be zero.
- **Value — CIO.** **Highest CIO weight (1.7) of any dimension.** For the CIO
  this is the load-bearing widget — but it belongs in a *CIO-only* view or on
  the CIO lens, not averaged into the CCO/CRO composite.
- **Verdict.** Move to a labelled Platform Health card on the CIO lens.
  Exclude from CCO/CRO composites. This is a re-composition fix, not a
  removal.

---

## 5. Cross-cutting widget issues (not tied to a single parameter)

### 5.1 Risk × Coverage matrix

- **File:** `dashboard_api.py:785–...` (endpoint), `:815–816` (classifier).
- **Classifier:** `high if enforcement_risk_score >= 0.8 else med if >= 0.6
  else low`. Real code, real thresholds, no bug.
- **Problem:** the input column (`compliance_rules.enforcement_risk_score`) is
  NULL for essentially every row because the scorer isn't wired (see P4).
  All-Low is the honest downstream consequence of an upstream ingestion gap.
- **Category:** `scraping / ingestion` (upstream). Same fix as P4.
- **Persona value:** the red quadrant is *the* CRO-lens artefact. Until
  populated, the CRO has no reason to open the page.

### 5.2 Findings panel + P6 pairing

- Already covered under P6. Emphasise: the empty-state copy is the *right*
  answer; the 80 next to it is the wrong answer. Fixing one line
  (`dashboard_api.py:698`) makes the page internally consistent — it's the
  cheapest credibility win on the board.

### 5.3 "biggest_drag" business-read callout ("81 unmapped, 226 unsigned")

- **File:** `dashboard_api.py:763–777`.
- Numbers are live counts, correct. But "81 unmapped" is derived from
  `has_test_coverage=FALSE`, which — because nothing sets it TRUE — is a
  proxy for "obligations that exist," not "obligations without a mapping." The
  business read reads the same latent bug that pins P3 at 0%.
- **Verdict:** ships automatically when the P3 writeback lands.

### 5.4 Two disjoint denominators (rules vs obligations)

- Discussed in §2. The one architectural fix that makes every other widget
  reconcilable is to introduce an **applicable-obligation registry** — a
  charter/asset-tier-keyed target set — and route every widget's denominator
  through it. Without that fix, "internal consistency" remains negotiated per
  widget, not structural.

---

## 6. Findings summary by category

| Category | Findings | Load-bearing fix |
|---|---|---|
| **Phantom** (hardcoded value on a page that also says "not wired") | P6, P9 | Change L698 and L701 to `None` / `N/A`. ~15 minutes. |
| **Scraping / ingestion** (code exists, pipeline not invoking it) | P4, P7, risk-matrix classifier input | Wire `enforcement_risk_scorer` into `scraper_scheduler.py`. |
| **Business logic** (missing writeback thread through the agent pipeline) | P3, "81 unmapped" biggest_drag | Discovery→execution `obligation_id` threading + SQL writeback. See fix plan §Fix 1. |
| **Data model** (widget presupposes a table that isn't in the schema) | P1 (applicable-obligation registry), P2 (obligation↔control mapping), P7 (governance evidence artefacts) | Schema additions; medium-term. |
| **Definition** (label describes concept A, query measures concept B) | P2, P8, P10 | Rename widgets, split queries. Cheap. |
| **Operational** (metric is honest but UX makes clearing the backlog hostile) | P5 (bulk sign-off) | Add bulk action + aging. |

---

## 7. Persona value framing — one line each

- **CCO:** *"Do I know my obligations, are they mapped to controls, and are
  those controls tested?"* — hinges on **P1 → P2 → P3**, in that order.
  Currently the chain is: undefined denominator → misdefined mapping →
  permanently-zero testing. Fix the middle first (P3 writeback) because it's
  the highest weight and it's the cheapest single unlock.

- **CRO:** *"Is my risk assessment current, are my findings closing, and is my
  testing independent?"* — hinges on **P4, P6, P8**. Currently: risk assessment
  starved (upstream), findings phantom (hardcoded), independent testing
  conflated with any testing (definitional). The CRO's whole lens is stalled
  behind the risk scorer wiring — one fix opens P4 and the risk matrix
  together.

- **CIO:** *"Is the platform delivering reports, and is the engine healthy?"*
  — hinges on **P9 and P10**. Currently: reporting is phantom (kill L701) and
  engine integrity is honest but composed into the wrong screens. Move it,
  don't remove it.

---

## 8. Recommended sequencing (cross-referenced to `examination_readiness_fix_plan.md`)

1. **Data-integrity pass (hours, not days).**
   - Change `dashboard_api.py:698` and `:701` from hardcoded ints to `None`,
     render explicit "not measured" state on the frontend.
   - Move Engine integrity out of the CCO/CRO composites (or drop weight to 0
     on those lenses).
   - Rename Control mapping to Obligation attribution *or* commit to the
     mapping schema.
   - **Outcome:** dashboard becomes internally consistent, even if scores drop.
2. **Wire the writeback thread (P3).** See fix plan Fix 1. Highest single
   readiness gain per unit engineering.
3. **Wire the enforcement risk scorer.** Unlocks P4, P7 numerator, and the
   whole matrix.
4. **Ship the applicable-obligation registry.** Turns P1 into a real
   completeness metric and gives every downstream widget a shared denominator.
5. **Findings ingest** — schema is ready; ingester is the missing piece.
6. **Bulk sign-off UX + aging on P5.**
7. **Governance-evidence schema + independent-testing actor tag.**

Steps 1–3 clear the internal contradictions. Step 4 is the architectural fix
that stops the contradictions from re-emerging.
