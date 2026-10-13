# Examination Readiness — Resolutions Log

Companion to `examination_readiness_parameter_audit.md` (the audit) and
`examination_readiness_fix_plan.md` (the plan). Each entry below records **one
fix**, why it was necessary, exactly what changed, and a worked-example
before/after so the resolution is auditable without re-reading the diff.

---

## R1 — P6 Findings remediation: kill the phantom

**Date:** 2026-07-03
**Category:** `phantom` (per audit §6)
**Persona most affected:** CRO (findings weight 1.6 — the highest weight of any
dimension in the CRO lens).

### Problem

`src/api/dashboard_api.py:698` shipped a hardcoded `findings = 80`, so the
"Findings remediation" widget always reported **80%** — even though the
adjacent Open findings (MRAs) panel on the same page displayed
*"No findings store wired up yet — the tracker will populate once the table is
provisioned."* Two widgets, one page, directly contradicting each other.

Root cause: at the time the ten-parameter rubric was wired (`feat/ftr-readiness-phase1`
commit `1db2c8b`), the `findings` table did not exist. Migration `042_p2_exports_schema.py`
subsequently created it (`findings` + `finding_history`, with OCC 5 Cs schema,
see `src/models/database.py:758–789`), but the readiness endpoint was never
updated to read from it. The `80` remained baked in.

Downstream damage:
- CCO lens composite inflated by ~6 points against a Failing baseline
  (`30 → 24` once the phantom is removed).
- CRO lens composite inflated by ~8 points (`32 → 24`) — worse because
  findings carry the CRO's heaviest single weight.
- Any examiner who spot-checks the 80 discovers there is nothing behind it,
  and re-checks every other metric on the page.

### Fix

**Backend — `src/api/dashboard_api.py`:**

1. Added a live count against the `findings` table inside `_dimension_scores()`
   (after the SDN counts, around L688):

   ```python
   fnd_total  = SELECT COUNT(*) FROM findings
   fnd_closed = SELECT COUNT(*) FROM findings WHERE status='validated_closed'
   ```

2. Replaced the hardcoded assignment at L698:

   ```python
   # before
   findings = 80                              # no findings store yet

   # after
   findings = pct(fnd_closed, fnd_total) if fnd_total > 0 else None
   ```

3. Updated `_dimension_scores()` return type from `Dict[str, int]` to
   `Dict[str, Optional[int]]`.

4. Updated the composite-score computation in `get_readiness()` to skip `None`
   dimensions and renormalise weights across only the measured ones:

   ```python
   measured_w = {k: w for k, w in weights.items() if dims.get(k) is not None}
   total_w = sum(measured_w.values()) or 1
   score = round(sum(dims[k] * measured_w[k] for k in measured_w) / total_w)
   ```

   Renormalisation is the load-bearing part: without it, a `None` would either
   crash the composite (multiplication against `None`) or force a fallback
   baseline — which is the phantom we just deleted.

5. Updated `_drag_summary()` to filter out `None` dims before sorting for the
   "biggest_drag" business-read callout.

**Frontend — `dashboard/src/pages/Readiness.jsx`:**

6. `Dimensions` component now renders `N/A` (and a zero-width bar) when
   `d.score` is `null`, instead of `null%`:

   ```jsx
   const measured = d.score !== null && d.score !== undefined
   ...
   <div className="ds">{measured ? `${d.score}%` : 'N/A'}</div>
   ```

   The existing empty-state message in the Open findings (MRAs) panel is
   unchanged — it already reads correctly.

### Semantics of the change

| Situation | Findings dim | Composite effect |
|---|---|---|
| `findings` table empty (typical Phase 1 state) | `None` — rendered `N/A` | Excluded from composite; weights renormalise across the remaining 9 dims |
| Table has rows, some closed | `pct(closed, total)` | Included in composite at its lens weight |
| Table has rows, none closed | `0` | Included; drives composite down honestly |
| Table has rows, all closed | `100` | Included; contributes fully |

`validated_closed` is the only `FindingStatus` we credit as "remediated"
(`src/models/database.py:615–619`). The other three (`open`, `past_due`,
`pending_validation`) are not remediated by examiner standards, so counting
them would soft-inflate the metric.

### Worked example — before / after, all three lenses

Using the ten values visible in the current dashboard screenshot
(`inventory=4, control_mapping=100, testing=0, ra_currency=0, change_mgmt=0,
findings=?, governance=0, independent=10, reporting=40, engine=100`) run
through the actual lens weights at `dashboard_api.py:590–607`:

| Lens | Before (phantom = 80) | After — empty table (`None`) | After — 2 findings, 1 closed (50) | After — 5 findings, 4 closed (80, real) |
|---|---:|---:|---:|---:|
| **CCO** | 30 | **24** | 27 | 30 |
| **CRO** | 32 | **24** | 28 | 32 |
| **CIO** | 39 | **36** | 37 | 39 |

Read this table left-to-right:

- The two "Before" and rightmost "After (real 80)" columns are numerically
  identical **for the CCO and CRO lenses**. That means: **a bank with 5
  findings and 4 remediated legitimately earns the score the old phantom was
  claiming.** The phantom was flattering the platform, not the bank.
- The middle column ("empty table → None") is the *honest* score for a bank
  we haven't ingested findings for yet: CCO 24, CRO 24, CIO 36. Every future
  point on the findings dimension now has to be earned.
- The CRO column moves the most (32 → 24 = 8 points) because findings is the
  CRO's single heaviest-weighted dimension (`1.6`).
- The CIO column moves the least (39 → 36 = 3 points) because the CIO's
  weight on findings is only `0.8`.

### Per-persona value (why this matters)

- **CCO.** The "Open findings (MRAs)" panel is the exam artefact examiners
  open first. A CCO who took the old dashboard's `80%` to the board would
  have been describing a metric that had no source table. The fix delivers
  the CCO an honest number (or an honest N/A) — either of which is
  defensible in front of an examiner.
- **CRO.** Findings closure *is* the CRO's scorecard. An 8-point inflation
  on the CRO composite was the largest single distortion on the page.
  Removing it makes the CRO lens meaningful for the first time.
- **CIO.** Small direct impact; but the CIO cares about metric integrity as
  a platform property. Killing a phantom on the executive landing page is a
  platform-health event, not a compliance-content event.

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- Simulated composite math (all 3 lenses × 4 findings scenarios) matches
  the table above — see the simulator script preserved inline for
  reproducibility if the weights ever change:

  ```python
  def score(dims, w):
      m = {k: wt for k, wt in w.items() if dims.get(k) is not None}
      total = sum(m.values()) or 1
      return round(sum(dims[k] * m[k] for k in m) / total)
  ```

- Existing test coverage: `tests/integration/api/test_dashboard_api.py`
  asserts `/readiness` returns `200` and a dict payload; it does not assert
  any specific dimension values, so no test-suite breakage. Follow-up tests
  worth adding (not part of this fix): (a) `findings` = null when table is
  empty; (b) composite skips null dims; (c) composite matches the manual
  math for a seeded finding.

### Files changed

- `src/api/dashboard_api.py` — findings query, None-safe composite, drag summary
- `dashboard/src/pages/Readiness.jsx` — N/A rendering in `Dimensions`

### Follow-ups (not this fix)

- **R2 (next)** — apply the same treatment to P9 (`reporting = 40`
  hardcoded at `dashboard_api.py:701`). Same pattern: replace with a live
  query against the export-audit log; return `None` when unmeasured; the
  composite renormalisation is already in place from R1.
- **Findings ingest pipeline.** Schema is ready; a bank-side or examiner
  handoff needs to be defined before findings will populate. Tracked
  separately.
- **Add regression assertion to test suite:** once the fix is in prod, add
  a test that fails if `findings` ever becomes a hardcoded int again.

---

## R2 — P9 Point-in-time reporting: kill the second phantom

**Date:** 2026-07-03
**Category:** `phantom` (per audit §6)
**Persona most affected:** CIO (reporting weight `1.3` — the CIO's
second-heaviest dimension after Engine integrity).

### Problem

`src/api/dashboard_api.py:701` shipped a hardcoded `reporting = 40`. The
inline comment read `# no persistent export audit log yet`, which was true
at the time the ten-parameter rubric was first wired (`feat/ftr-readiness-phase1`
commit `1db2c8b`). It stopped being true when migration
`042_p2_exports_schema.py` landed the exports schema on 2026-06-21:

- `export_artifacts` — WORM store keyed by `(bank_id, type, as_of,
  version_hash)`, `type` locked by CHECK constraint to the six canonical
  exam-pack outputs (`examiner_package`, `ofac_attestation`, `board_report`,
  `mra_tracker`, `change_log`, `coverage`).
- `export_signoffs` — e-sig audit chain per artifact.
- `export_access_log` — download-tracking per FFIEC IT Handbook §IV.

So the assumption that seeded R1's audit ("no export audit log yet") is
stale by two months. R2 is a **stronger** fix than R1: instead of only
returning `None` on emptiness, it can compute a real coverage metric the
moment a bank starts producing artifacts.

### Semantics — what does "point-in-time reporting" measure?

The examiner-pack question is *"Do you have a current, signed evidence
package of each required type?"* That has two axes: **completeness**
(do you have all six types?) and **recency + signoff** (is each one
current and attested?). R2 collapses both into one 0–100 metric:

- **Denominator:** 6 — the canonical types locked in the CHECK constraint.
  Codified as `_CANONICAL_REPORT_TYPES = 6` (module-level constant, no
  magic number). A bank presenting an exam pack needs *all six* current,
  not five.
- **Numerator:** `COUNT(DISTINCT ea.type)` across artifacts joined to a
  signoff row, filtered to `generated_at >= NOW() - INTERVAL '90 days'`.
  90 days matches the standard quarterly board cadence — an artifact
  older than that isn't a "point-in-time" artifact anymore.
- **`None` when `export_artifacts` is empty** — same pattern as R1,
  processed by the same weight-renormalisation in `get_readiness()`.

### Fix

**Backend — `src/api/dashboard_api.py`:**

1. Added the canonical-type count as a module-level constant so a schema
   change (e.g. adding a 7th artifact type in a future migration) forces
   an intentional update at one file location:

   ```python
   _CANONICAL_REPORT_TYPES = 6
   ```

2. Added two queries in `_dimension_scores()` alongside the R1 findings
   queries:

   ```python
   artifacts_total       = SELECT COUNT(*) FROM export_artifacts
   signed_recent_types   = SELECT COUNT(DISTINCT ea.type)
                           FROM export_artifacts ea
                           JOIN export_signoffs es
                             ON es.artifact_id = ea.artifact_id
                           WHERE ea.generated_at >= NOW() - INTERVAL '90 days'
   ```

3. Replaced the hardcoded assignment at L701:

   ```python
   # before
   reporting = 40                              # no persistent export audit log yet

   # after
   reporting = (
       pct(signed_recent_types, _CANONICAL_REPORT_TYPES)
       if artifacts_total > 0 else None
   )
   ```

No new frontend changes needed — the `N/A` rendering and composite
renormalisation shipped with R1 and cover R2 by construction.

### Semantics of the change

| Situation | Reporting dim | Composite effect |
|---|---|---|
| `export_artifacts` empty (typical Phase 1 state) | `None` — rendered `N/A` | Excluded; weights renormalise across measured dims |
| Artifacts exist but zero signed in last 90d | `0` | Included; drives composite down honestly |
| N of 6 canonical types signed in last 90d | `round(100·N/6)` (0, 17, 33, 50, 67, 83, 100) | Included at lens weight |
| All 6 types signed within 90d | `100` | Full credit — exam pack is examiner-ready |

Only the six locked-in types count toward the denominator. If an artifact
of a non-canonical type is inserted (which the CHECK constraint would
reject anyway), it would neither help nor hurt the metric.

### Worked example — before / after, all three lenses

Base dims from the visible dashboard state (holding all other dims
constant while stepping through the reporting axis):

| Scenario | CCO | CRO | CIO |
|---|---:|---:|---:|
| A. Original (findings=80 phantom, reporting=40 phantom) | 30 | 32 | 39 |
| B. After R1 only (findings=N/A, reporting=40 phantom left) | 24 | 24 | 36 |
| C. **After R1 + R2, both tables empty** (both `None`) | **23** | **22** | **35** |
| D. Realistic (2 findings/1 closed = 50; 4 of 6 report types signed = 67) | 29 | 30 | **41** |

Read across:

- **B → C** is the R2 delta. CCO −1, CRO −2, CIO −1. Modest but honest —
  reporting was a smaller phantom than findings, but still a phantom.
- **C → D** shows what real ingest earns: CCO +6, CRO +8, CIO +6. The
  CIO composite actually **rises above the original phantom** score
  (39 → 41) once real reports flow through — because the CIO's `1.3`
  weight on reporting was being *underserved* by the `40` phantom, not
  flattered by it.
- **A → C** is the total credibility repair from R1 + R2 combined: CCO
  drops 7 points (30 → 23), CRO drops 10 points (32 → 22), CIO drops
  4 points (39 → 35). Every one of those points was fictional.

### Per-persona value (why this matters)

- **CIO.** Reporting is the CIO's second-heaviest dimension (`1.3`) and
  the artifact chain is a platform property — the report engine, the
  WORM store, the signoff chain, the access log. The pre-R2 dashboard
  underrepresented a working CIO product (would have shown `40` even at
  full artifact coverage) *and* overrepresented a non-working one (would
  have shown `40` at zero coverage). R2 fixes both directions.
- **CCO.** Reporting weight is `0.9` — modest for the CCO — but this is
  the exam-pack surface CCOs personally sign, so the metric needs to be
  earnable rather than baked in.
- **CRO.** Reporting weight is `0.9`. Low direct impact; R2's value to
  the CRO is indirect (a credible dashboard the CRO can trust across
  the board).

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- Composite math (all three lenses × four scenarios) matches the table
  above; the simulator inline in R1 works unchanged.
- Existing test coverage in `tests/integration/api/test_dashboard_api.py`
  still limited to `200` / dict assertions; no test-suite regression.

### Files changed

- `src/api/dashboard_api.py` — canonical-type constant, artifact +
  signoff queries, reporting assignment; **no frontend change needed**
  (R1's N/A rendering + renormalised composite cover R2 by construction).

### Follow-ups (not this fix)

- **Freshness haircut per artifact type.** A `coverage` report might be
  produced quarterly (90d window is right), but a `change_log` might
  fire monthly. Future refinement: per-type `stale_after` interval, so
  a bank producing a monthly `change_log` on a 60-day cadence is
  correctly flagged.
- **Signoff completeness by role.** Currently R2 credits any signoff.
  A stricter version would credit only artifacts with signoffs from the
  full role matrix per Q9 (`bsa_officer` + `cco` + relevant others).
  Deferred until the Q9 sign-off role matrix is captured in code.
- **Regression assertion.** Once R2 is in prod, add a test that fails
  if `reporting` (or `findings`) ever becomes a hardcoded int again —
  cheap way to prevent phantom regressions.

### Combined status after R1 + R2

Both hardcoded phantoms on the readiness page are now gone. The audit's
Category §6 row for **`phantom`** is closed. Every dimension on the page
either reads a live database source or returns `N/A` — no baked-in
baselines remain. Remaining audit categories (`scraping / ingestion`,
`business logic`, `data model`, `definition`, `operational`) are
untouched by R1/R2 and are the next queue.

---

## R3 — P4/P7 + Risk matrix: fix the enforcement-risk back-propagation join

**Date:** 2026-07-03
**Category:** `scraping / ingestion` (per audit §6) — but the diagnosis is more
subtle than the audit assumed (see "Actual root cause" below).
**Personas most affected:**
- **CRO** — Risk-assessment currency is the CRO's second-heaviest lens weight
  (`1.4`). This is the fix that finally makes the CRO lens meaningful.
- **CCO** — Governance evidence (`1.2`) shares the same numerator, so fixing
  one dimension fixes two.
- **All lenses** — the Risk × Coverage matrix ("where danger and gaps
  overlap") is driven by `compliance_rules.enforcement_risk_score`.
  Populating that column is what moves the matrix out of all-Low.

### Actual root cause (correction to the audit)

The audit doc (§4 P4) stated that
`enforcement_risk_scorer` "code exists but is not called from the scrape cycle."
That is **wrong**. The scheduler already invokes it every cycle at
`src/services/scraper_scheduler.py:882–889`:

```python
try:
    async with get_db() as session:
        from src.services.enforcement_risk_scorer import EnforcementRiskScorer
        scorer = EnforcementRiskScorer()
        scored = await scorer.run(session)
        logger.info("enforcement_risk_scorer_done", scored=scored)
except Exception as exc:
    logger.warning("enforcement_risk_scorer_failed", error=str(exc))
```

The scorer runs. It aggregates enforcement actions per regulation, computes
a 0–1 risk score (log-normalised frequency 0.45 + log-normalised penalty
0.35 + trend direction 0.20), and upserts to `enforcement_risk_scores`.
That half works.

**What silently fails** is the last step — the back-propagation to
`compliance_rules.enforcement_risk_score`. The old query at
`enforcement_risk_scorer.py:163–167` was:

```python
await session.execute(
    update(ComplianceRule)
    .where(ComplianceRule.rule_id.contains(reg_id))
    .values(enforcement_risk_score=score)
)
```

`.contains(reg_id)` compiles to `rule_id LIKE '%reg_id%'`. The two sides
have incompatible shape:

| Side | Shape | Example |
|---|---|---|
| `compliance_rules.rule_id` | agency-topic-kind-N | `OCC-BSA-CTR-001` |
| `reg_id` from `_extract_regulations_cited()` | CFR/USC citation OR family label | `31 C.F.R. 1010.311` **or** `BSA` |

Consequence: only bare family labels (`BSA`, `AML`, `CRA`, …) accidentally
substring-matched into `rule_id`s that already embedded the same token
(`OCC-BSA-CTR-001` contains `BSA`). Every precise CFR/USC citation missed
every rule. And even the family-term hits were accidental — `AML` matched
nothing because no rule_id embeds the string "AML".

That's why the audit saw all rules NULL and the matrix all-Low: the
back-propagation was ~90% broken by shape, not by absence of invocation.

### Fix

**`src/services/enforcement_risk_scorer.py`:**

1. Swapped `from sqlalchemy import ... update` for
   `from sqlalchemy import ... text` — the new join uses PostgreSQL
   `regexp_replace` for citation normalisation, which is cleanest as raw
   SQL.

2. Replaced the broken UPDATE with a shape-aware, two-branch match:

   ```python
   UPDATE compliance_rules
      SET enforcement_risk_score = :score,
          updated_at             = NOW()
    WHERE (
            cfr_citation IS NOT NULL
            AND regexp_replace(lower(cfr_citation), '[.\s§]', '', 'g')
              = regexp_replace(lower(:reg_id),     '[.\s§]', '', 'g')
          )
       OR (
            lower(:reg_id) IN
              ('bsa','aml','cra','hmda','ecoa','tila','respa','fca','fdca')
            AND (
                 lower(category) LIKE '%' || lower(:reg_id) || '%'
              OR lower(rule_id)  LIKE '%' || lower(:reg_id) || '%'
            )
          )
   ```

   The two branches are disjoint by shape — a citation is either a
   CFR/USC citation *or* a bare family label; never both.

3. Added per-`reg_id` debug logging with the affected row count so a
   future ops incident can diagnose which citations are producing zero
   downstream matches:

   ```python
   logger.debug("enforcement_risk_backprop", reg_id=..., score=..., rows_updated=...)
   ```

### Why this uses `cfr_citation` (migration 013) rather than a new mapping table

`compliance_rules.cfr_citation` (`src/models/database.py:106`) was added
by migration 013 for exactly this shape reason — connectors write both a
hyphen-encoded `rule_id` (for stable primary-key semantics) *and* the
canonical CFR/USC string (for cross-reference joins). It is populated at
scrape-time by every USA connector. Using it here is the intended
architecture — R3 stops piggy-backing on a coincidental substring hit
against `rule_id` and starts using the column that exists for this
purpose.

### Worked example — 7 representative citations against 7 rule fixtures

Rules (drawn from actual `src/connectors/regulatory/usa/occ_bsa.py`,
`fincen_sar.py`, `cra.py` shapes):

| rule_id | category | cfr_citation |
|---|---|---|
| OCC-BSA-CTR-001 | bsa_aml | 31 CFR 1010.311 |
| OCC-BSA-CDD-001 | bsa_aml | 31 CFR 1010.230 |
| OCC-BSA-WIRE-001 | bsa_aml | 31 CFR 1020.410 |
| CFPB-1071-FIELD | section_1071 | 12 CFR 1002.107 |
| FDIC-CRA-INV | cra | 12 CFR Part 345 |
| FRB-HMDA-COV | hmda | 12 CFR 1003.2 |
| STATE-NY-ML | money_laundering | *(null)* |

Enforcement citations against those rules:

| Citation | OLD `rule_id.contains(reg_id)` matches | NEW (R3) matches |
|---|---|---|
| `31 C.F.R. 1010.311` | *(none — silent miss)* | `OCC-BSA-CTR-001` (precise) |
| `12 C.F.R. § 1003.2` | *(none — silent miss)* | `FRB-HMDA-COV` (precise) |
| `12 CFR Part 345` | *(none — silent miss)* | `FDIC-CRA-INV` (precise) |
| `BSA` | 3 rules (accidental substring on `rule_id`) | 3 rules (via category) — unchanged |
| `AML` | *(none — no rule_id contains "AML")* | 3 rules (via category `bsa_aml`) |
| `CRA` | `FDIC-CRA-INV` (via rule_id substring) | `FDIC-CRA-INV` (via category) — unchanged |
| `12 U.S.C. 5311` | *(none — correct)* | *(none — correct: no rule cites this)* |

**Net gain:** every CFR/USC citation now hits its intended rule — 3
new precise matches in this fixture set. The bare `AML` family label,
which produced zero matches under the old join, now correctly scores
the three BSA/AML rules via `category`.

### Downstream impact on the readiness widgets

Same 7-rule fixture (scaled to 200 rules across BSA/CRA/HMDA/1071/state
in the simulator) with 8 realistic enforcement citations (CTR at 0.85,
CDD at 0.72, wire at 0.65, HMDA at 0.55, CRA at 0.62, 1071 at 0.45,
family BSA at 0.78, family AML at 0.68):

| Loop semantics | Join | Rules scored | Matrix High | Matrix Med | Matrix Low |
|---|---|---:|---:|---:|---:|
| Current (last-write-wins — pre-existing bug) | OLD | 100 / 200 | 0 | 100 | 100 |
| Current | NEW (R3) | 102 / 200 | 0 | 100 | 100 |
| Correct (MAX across matches — separate follow-up) | OLD | 100 / 200 | 0 | 100 | 100 |
| Correct | NEW (R3) | 102 / 200 | **2** | 98 | 100 |

Two things to read from that table:

1. **R3 is a correctness fix.** Under the old join, CFR-specific
   citations produced **zero** downstream updates. Under R3 they land on
   their target rules.
2. **The full readiness gain is throttled by a second bug in the same
   file.** The scorer's UPDATE runs in a loop over `reg_id`s — the last
   write wins per rule. When a rule matches both a CFR citation
   (`31 CFR 1010.311` at 0.85) and a family label (`BSA` at 0.78), the
   family write overwrites the more precise CFR write. The matrix's High
   quadrant stays empty until this is fixed. See "Follow-ups" below.

### P4 / P7 / composite deltas

Because the aggregate P4 (`ra_currency`) and P7 (`governance`)
percentages are `rules_with_risk / rules_total`, both fixes (R3 and the
MAX follow-up) each add small aggregate percentage points; the visible
impact is dominated by *which* rules get scored, not *how many*. A
precise 2-rule High-quadrant is the examiner-facing win — that quadrant
is the risk-matrix caption's headline artefact
(`dashboard_api.py:815–816`).

For a realistic bank that has run one scrape cycle post-fix with the
seeded rule + enforcement counts above:

| Lens | Before R3 (all-NULL) | After R3 alone | After R3 + MAX follow-up |
|---|---:|---:|---:|
| P4 ra_currency % (rules_with_risk / rules_total) | 0 | 51 | 51 |
| P7 governance % (same numerator) | 0 | 51 | 51 |
| Matrix red-quadrant cell (high · untested) | 0 | 0 | 2 |
| CCO composite (from R1+R2 baseline of 23) | +9 pts → **32** | +9 pts → **32** | +9 pts → **32** |
| CRO composite (from R1+R2 baseline of 22) | +11 pts → **33** | +11 pts → **33** | +11 pts → **33** |
| CIO composite (from R1+R2 baseline of 35) | +6 pts → **41** | +6 pts → **41** | +6 pts → **41** |

The CRO lens gains the most because `ra_currency` carries the CRO's
weight of `1.4` — the second-heaviest CRO weight after findings.
Composite deltas are the same under R3-alone vs R3-plus-MAX because
the aggregate `%` doesn't change; the qualitative matrix change is what
the MAX follow-up delivers.

### Per-persona value (why this matters)

- **CRO.** Risk-assessment currency (`ra_currency`) and independent
  testing are the CRO's two operational levers on the readiness score.
  Before R3, `ra_currency` was stuck at 0% (with a fallback baseline of
  60) not because the CRO hadn't done the work but because the platform
  never wrote the score. R3 is the single change that makes the CRO
  lens honest for the first time.
- **CCO.** Governance evidence (P7) reuses the P4 numerator, so R3 also
  moves the CCO lens up by ~9 points. Just as important: the Risk ×
  Coverage matrix is the widget most examiners open first. Populating it
  is a credibility event, not just a metric event.
- **CIO.** Indirect: R3 proves the ingestion → aggregation →
  back-propagation chain works end-to-end. That's a CIO-facing platform
  milestone — the enforcement scrape actually influences downstream
  posture instead of dead-ending at a scoring table nothing reads.

### Verification

- `python3 -m py_compile src/services/enforcement_risk_scorer.py` — passes.
- Simulator against 7 representative citations × 7 fixture rules — exact
  match table above; every citation lands where its shape dictates.
- Simulator against 200 rules × 8 enforcement citations — matches the
  downstream table above.
- No frontend change; no schema change.
- The pre-existing loop semantics (last-write-wins) is unchanged by R3
  so no regression against the current (broken) baseline is possible.

### Files changed

- `src/services/enforcement_risk_scorer.py` — swapped `update` import for
  `text`, replaced the broken back-propagation UPDATE with a
  shape-aware two-branch match, added per-`reg_id` debug logging with
  affected-row count.

### Follow-ups (not this fix)

- **R4 candidate — take MAX across matches, not last-write-wins.** The
  loop at `enforcement_risk_scorer.py:123–167` iterates `reg_id`s and
  UPDATEs per iteration. A rule that matches multiple citations gets
  whichever score iteration-order lands last. Correct semantics: score
  = MAX across all matching citations. Fix by either (a) aggregating
  the target scores per rule in Python first, then doing a single
  bulk UPDATE per unique rule/score pair, or (b) folding into a single
  SQL statement using `GREATEST` semantics with a CTE. Until this
  lands, R3's precision improvement is capped whenever a CFR citation
  and a family label both hit the same rule.
- **Confirm `enforcement_actions` is actually populated.** R3 assumes
  the enforcement connector runs weekly and produces rows. If it
  doesn't, the scorer computes zero scores regardless of join
  correctness. Verify against prod Aurora once R3 deploys.
- **Add regression assertion** — a test that seeds a `compliance_rules`
  row with a known `cfr_citation`, a matching enforcement action, then
  asserts `enforcement_risk_score` is populated post-scorer-run.
  Cheapest way to prevent a future refactor from reintroducing the
  shape mismatch.

### Combined status after R1 + R2 + R3

The audit's `phantom` category is closed (R1, R2). The audit's
`scraping / ingestion` category is partially closed (R3): the join
between enforcement citations and rules is fixed; the loop-semantics
overwrite bug is now the load-bearing follow-up. Remaining audit
categories: `business logic` (P3 test-coverage writeback), `data model`
(P1 applicable-obligation registry, P2 obligation→control mapping),
`definition` (P10 Engine integrity in wrong composite, P2/P8 renames),
`operational` (P5 bulk sign-off).

---

## R4 — P3 test-coverage writeback + score transparency accordion

**Date:** 2026-07-04
**Category:** `business logic` (P3 writeback) + `UX` (Fix 5, score transparency)
**Personas most affected:**
- **CCO** — Testing carries the CCO's heaviest single weight (`1.5`). Every
  successful agent run that never flipped `has_test_coverage=TRUE` was a
  silent shortfall — the writeback was the only true *bug* on the readiness
  page.
- **CRO** — Testing weight is `1.2` (third-heaviest CRO dim), and the
  independent-testing dim shares the workflow_executions denominator, so
  writeback progress is visible on the CRO lens too.
- **All lenses** — Fix 5 makes the score itself actionable: operators can
  now see *which* dimension is dragging the composite and click straight
  to the surface that moves it.

### Problem

**P3 (business-logic bug).** `obligations.has_test_coverage` was written
`FALSE` at row creation (`obligation_extractor.py:423`) and never flipped
TRUE by any code path. The 6-agent test-generation pipeline generated
tests, opened PRs, posted JIRA comments — and left the coverage flag
untouched. The `testing` dimension read `0 / 81 obligations = 0%` in
perpetuity, floored at weight `1.5` on the CCO composite.

**Fix 5 (UX bug).** After R1+R2+R3 the composite score was honest, but
the *reason* it moved (or didn't) was invisible. The `/readiness`
response returned per-dim `{key, label, score}` — enough for the fixed
5-column grid, not enough to answer "*which dim is costing me the most
score points right now, and where do I click to fix it?*" Operators had
to source-dive `dashboard_api.py:_LENS_WEIGHTS` to know that testing
carried 1.5× on CCO but only 0.9× on CIO.

### Fix — Part 1: P3 writeback thread

The writeback needed three pieces to work end-to-end. Each already existed
on `feat/ftr-readiness-phase1` before R4 landed — R4 audits them and
adds the missing historical backfill:

1. **UnderstandingAgent resolves target obligations.**
   `src/agents/understanding.py:142–197` — `_resolve_obligation_ids` maps
   the DiscoveryAgent's banking_patterns → `compliance_rules.domain`
   (via `_PATTERN_TO_DOMAIN`), collects `rule_id`s from
   `ComplianceService.get_rules_by_domain`, then joins to
   `obligations.regulation_id`. The result lands on the workflow context
   as `understanding.obligation_ids`. Scoping via `domain` prevents a
   single wire-transfer test from being credited against every FinCEN
   obligation.

2. **ExecutionAgent flips the flag on happy-path.**
   `src/agents/execution.py:107–159` — `_mark_obligations_covered` runs
   the writeback SQL against the resolved set on `pr_url && is_approved`
   (validation-gated so we don't credit failed runs):

   ```sql
   UPDATE obligations
      SET has_test_coverage   = TRUE,
          coverage_updated_at = NOW(),
          test_scenario_ids   =
              COALESCE(test_scenario_ids, '[]'::jsonb)
              || CAST(:new_ids AS jsonb)
    WHERE obligation_id = ANY(:ids)
      AND is_active = TRUE
   RETURNING obligation_id
   ```

   `test_scenario_ids` gets `{jira_key or workflow_id}::{scenario_name}`
   entries per scenario — full audit trail, no schema migration needed
   (fix-plan §Fix 1 decision, deferred migration `024`).

3. **Historical backfill for completed workflow_executions.**
   `scripts/backfill_obligation_test_coverage.py` (new). Walks
   `workflow_executions WHERE status='completed' AND rule_id IS NOT NULL`
   in ascending `completed_at` order, joins each execution's `rule_id`
   to `obligations.regulation_id`, and flips uncovered obligations to
   `has_test_coverage=TRUE` with a `{jira_key or workflow_id}::backfill`
   marker so the audit trail records the credit came from historical
   inference. DRY_RUN=1 by default.

Result: `has_test_coverage` transitions from "structurally always FALSE"
to "TRUE for every obligation with a completed run — past, present and
future." Testing dim goes from a perma-zero to a live metric that moves
with every merged PR.

### Fix — Part 2: score transparency accordion

**Backend — `src/api/dashboard_api.py`:**

1. Added `_DIM_ACTIONS` map — per-dim `(cta_label, cta_href)` so the
   frontend accordion can route to the surface that actually moves the
   number (`testing → /workflows`, `change_mgmt → /changes`, …).

2. Extended each entry in the `/readiness` `dimensions` array from
   `{key, label, score}` to the six fields spec'd in fix-plan §Fix 5.1:

   ```json
   {
     "key": "testing",
     "label": "Testing & rationale",
     "weight": 1.5,
     "score": 0,             // legacy alias
     "value": 0,             // fix-plan spec name
     "weighted_contribution": 0.0,   // score points this dim added
     "shortfall_impact":      16.48, // score points this dim is leaving on the table
     "measured": true,
     "cta_label": "Generate tests",
     "cta_href":  "/workflows"
   }
   ```

   Math (matches `get_readiness` after weight renormalisation across
   measured dims only):

   ```
   weighted_contribution = round(value  * weight / Σw_measured, 2)
   shortfall_impact       = round(weight * (100 - value) / Σw_measured, 2)
   ```

   Unmeasured dims (findings/reporting when their tables are empty)
   return `null` for both — they're excluded from the composite by
   R1's renormalisation and therefore aren't dragging.

3. Also returned `weight_total_measured` at the top level so clients
   can rebuild the composite locally without hard-coding
   `_LENS_WEIGHTS` on the frontend.

**Frontend — `dashboard/src/pages/Readiness.jsx` + `theme.css`:**

4. Added a new `<Breakdown>` panel between `<Dimensions>` (the
   at-a-glance 5-column grid) and the per-lens sub-panels. Collapsed
   by default to keep the executive landing page compact.

5. When open, the accordion renders one row per dim sorted by
   `shortfall_impact` descending — biggest recoverable ground first.
   Columns: Dimension · Weight (`×1.5`) · Value (`0%`) · Contribution
   (`+0.00` score pts) · Shortfall (`+16.48` score pts) · Action
   button routing via `cta_href`.

6. Header shows the top gap in plain English (*"Biggest recoverable
   ground: Testing & rationale · +16.5 pts recoverable"*) so a CCO
   who never expands the accordion still gets the punchline.

7. CSS tokens reuse the existing `.ftr-panel` / `.ftr-dim` colour
   language (`--red` for shortfall, `--emerald`/`--amber` for score
   bands, `--faint` for N/A) — no new palette introduced.

### Semantics — what the shortfall math is buying

`shortfall_impact` is measured in composite-score points, not
percentages of the raw dimension. That's the point: a dim at 0% with a
tiny weight can matter less than a dim at 50% with a huge weight. The
accordion translates weights the operator never had to see into a
direct answer — "if this dim went to 100, the composite would move by
+X points."

Simulator against the current dashboard state
(`inventory=4, control_mapping=100, testing=0, ra_currency=51,
change_mgmt=0, findings=None, governance=51, independent=10,
reporting=None, engine=100`):

| Lens | Composite | Top-3 shortfall_impact (score pts recoverable) |
|---|---:|---|
| **CCO** | 35 | `testing (+16.5)`, `change_mgmt (+14.3)`, `inventory (+12.7)` |
| **CRO** | 36 | `testing (+14.1)`, `independent (+13.8)`, `change_mgmt (+11.8)` |
| **CIO** | 46 | `testing (+12.0)`, `change_mgmt (+12.0)`, `independent (+10.8)` |

**Every lens ranks `testing` #1.** That's exactly the outcome R4's
writeback fix is designed to move. An operator opening the accordion
gets a directive: "Fix testing next, click Generate tests." Not "the
composite is 35 for reasons you'd have to grep the codebase to know."

### Worked example — before / after (CCO lens, empty findings/reporting)

Baseline holds R1+R2+R3 (`findings=None, reporting=None`); we walk
`testing` from 0 → 100 as the writeback lands and the backfill runs:

| Scenario | testing dim | CCO composite | Δ vs. baseline |
|---|---:|---:|---:|
| A. R1+R2+R3 baseline, writeback never fires | 0 | 35 | — |
| B. 1 wire-transfer PR merged, 4 obligations flipped | 5 | 36 | +1 |
| C. Backfill runs on 20 historical completed runs | 25 | 39 | +4 |
| D. Full pilot cadence — 50% obligation coverage | 50 | 43 | +8 |
| E. Coverage plateau at 80% (realistic exam-ready) | 80 | 48 | +13 |
| F. 100% coverage (all obligations tested) | 100 | 51 | **+16** |

Read the table: the theoretical ceiling for Fix 1 alone on the CCO
lens against the current dashboard state is **+16 composite points** —
exactly the `shortfall_impact` the accordion surfaces at scenario A.
The accordion's number is the number.

### Per-persona value

- **CCO.** Testing at 0% was the CCO's single largest leak (weight
  1.5). R4's writeback + backfill converts a permanently-zero metric
  into one that moves with real work. The accordion now shows the CCO
  where the *next* biggest leak sits after testing (change_mgmt +14,
  inventory +12) — a natural roadmap.
- **CRO.** Testing is also the CRO's #1 shortfall (+14). The
  independent-testing dim (#2 at +14) reuses the same
  `workflow_executions` join, so R4 moves both together. The CRO now
  has a lens that ties to work being done, not to a floor.
- **CIO.** The accordion is the CIO's structural contribution — it's
  a platform-transparency feature. The CIO cares less about which dim
  is worst and more that the score *is auditable*: "here are the
  weights, here is the math, here is the impact, here's the click."
  R4 delivers that.

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- `python3 -m py_compile scripts/backfill_obligation_test_coverage.py`
  — passes.
- `npx vite build` — dashboard builds clean, no JSX/CSS errors.
- Simulator (all 3 lenses × 6 testing scenarios) reproduces the
  before/after table above; the response's `weighted_contribution` and
  `shortfall_impact` sum to `score` and `100 - score` respectively.
- Existing test coverage: `tests/integration/api/test_dashboard_api.py`
  still asserts `200` + dict; new payload keys are additive so no
  regression.

### Files changed

- `src/agents/understanding.py` — `_resolve_obligation_ids` (already
  present on branch pre-R4; documented here for completeness).
- `src/agents/execution.py` — `_mark_obligations_covered` (already
  present on branch pre-R4; documented here for completeness).
- `scripts/backfill_obligation_test_coverage.py` — **new.** DRY_RUN=1
  by default; walks completed workflow_executions and flips
  historical obligations.
- `src/api/dashboard_api.py` — `_DIM_ACTIONS` map, extended
  `dimensions[]` payload with weight / value / weighted_contribution
  / shortfall_impact / measured / cta_label / cta_href,
  `weight_total_measured` top-level.
- `dashboard/src/pages/Readiness.jsx` — new `<Breakdown>` accordion
  component, sorted by shortfall_impact desc, with CTA buttons routing
  via `useNavigate`.
- `dashboard/src/styles/theme.css` — `.ftr-breakdown*` styles reusing
  existing colour tokens; N/A styling for unmeasured dims.

### Follow-ups (not this fix)

- **Daily delta.** Fix-plan §5.3 called for a "+X vs yesterday" badge.
  Deferred — needs a `coverage_snapshots`-style daily rollup for the
  full 10-dim vector. Cheap MVP would be an in-memory 24h cache.
- **Regression test.** Add an integration test that seeds one rule +
  one obligation + one `workflow_executions status='completed'` row,
  runs the backfill script in DRY_RUN=0, then asserts
  `has_test_coverage=TRUE` and that
  `GET /readiness` returns `testing > 0` with a non-zero
  `weighted_contribution`. Cheapest way to prevent the writeback from
  silently regressing.
- **Deep-link filter on CTA hrefs.** `/workflows` and `/changes` land
  on the default tab today. Making the CTA drop the user on a
  pre-filtered view (e.g. `/workflows?status=incomplete`) is a
  polish pass.
- **`independent` dimension actor tag** (audit §P8) — still counting
  "any workflow_executions" rather than "independent audit
  workflow_executions". R4 unblocks writeback but doesn't split the
  independent numerator.

### Combined status after R1 + R2 + R3 + R4

The audit's `phantom` category is closed (R1, R2). The audit's
`scraping / ingestion` category is closed at the join level (R3); the
loop-semantics MAX-across-matches follow-up is still on the follow-up
queue. The audit's `business logic` category is closed (R4 — P3
writeback + historical backfill). Remaining audit categories:
`data model` (P1 applicable-obligation registry, P2 obligation→control
mapping schema, P7 governance-evidence schema), `definition`
(P2/P8 renames, P10 Engine-integrity re-composition), `operational`
(P5 bulk sign-off), and the R3 MAX-across-matches follow-up.

---

## R5 — Enforcement scorer: MAX-across-matches back-propagation

**Date:** 2026-07-04
**Category:** `scraping / ingestion` follow-up (R3's flagged sequel — see R3 §Follow-ups)
**Personas most affected:**
- **CCO / CRO** — the risk × coverage matrix (widget examiners open first)
  finally admits precise-CFR citations into the High quadrant. R3 fixed the
  join; R5 stops family labels stomping the precise scores.
- **All lenses** — small aggregate P4/P7 percentage delta; the visible payoff
  is the qualitative High-quadrant transition, not composite points.

### Problem

The R3 fix (2026-07-03) changed the back-propagation join from a broken
`rule_id.contains(reg_id)` substring to a shape-aware 2-branch match:
precise CFR citations → `cfr_citation`, bare family labels (`BSA`/`AML`/…)
→ `category` + `rule_id`. That fixed the *join*.

What R3 explicitly left as its top follow-up was the loop semantics. The
scorer iterates `reg_id`s in dict-insertion order (=DB group-by order — not
stable across cycles) and issues one UPDATE per iteration:

```python
for reg_id, stats in reg_stats.items():
    ...
    UPDATE compliance_rules SET enforcement_risk_score = :score
     WHERE <2-branch match>
```

When a single rule matches multiple citations (typical for BSA/AML rules
that carry both a precise CFR citation and a family label), whichever
`reg_id` iterates last wins. Example against the R3 fixture: rule
`OCC-BSA-CTR-001` matches `31 CFR 1010.311` (score 0.85) *and* `BSA`
(score 0.78). Under last-write-wins the 0.78 stomps the 0.85 — the more
precise citation is silently lost. Under MAX-across-matches the 0.85
survives, and the rule crosses into the risk-matrix High quadrant (≥0.75)
that examiners key on.

Root cause: the UPDATE is idempotent inside a single iteration but
non-idempotent across iterations against the same target row. The
column has no "if greater" semantics — the SQL just clobbers.

### Fix

**`src/services/enforcement_risk_scorer.py`:**

1. Added `from collections import defaultdict`.
2. Restructured `run()` into two passes over the reg_stats loop:

   **Pass 1 — SELECT + accumulate.** Kept the R3 2-branch match SQL but
   changed the statement from `UPDATE` to `SELECT rule_id`. Fetched
   rule_ids per iteration and updated `rule_score_map[rule_id] =
   max(prior, score)`. Also tracks `winning_reg[rule_id] = reg_id` for
   structured logging so future incidents can trace which citation
   drove a rule's final score.

   ```python
   rule_score_map: Dict[str, float] = {}
   winning_reg: Dict[str, str] = {}
   for reg_id, stats in reg_stats.items():
       ...  # existing EnforcementRiskScore upsert unchanged
       matches = await session.execute(text(SELECT_SQL), {"reg_id": reg_id})
       for rid, in matches.fetchall():
           if score > rule_score_map.get(rid, 0.0):
               rule_score_map[rid] = score
               winning_reg[rid] = reg_id
   ```

   **Pass 2 — bulk UPDATE grouped by score.** Grouped the accumulated
   map by score value and issued one UPDATE per distinct score using
   `WHERE rule_id = ANY(:rule_ids)`. That bounds the UPDATE count by
   the cardinality of `compute_risk_score` outputs (typically 5–20
   distinct scores across the whole rule set) rather than the reg_id
   fan-out.

   ```python
   by_score: Dict[float, List[str]] = defaultdict(list)
   for rid, s in rule_score_map.items():
       by_score[s].append(rid)
   for score_val, rule_ids in by_score.items():
       await session.execute(text("""
           UPDATE compliance_rules
              SET enforcement_risk_score = :score, updated_at = NOW()
            WHERE rule_id = ANY(:rule_ids)
       """), {"score": score_val, "rule_ids": rule_ids})
   ```

3. Replaced the per-iteration `enforcement_risk_backprop` debug log with:
   - `enforcement_risk_match` per iteration (reg_id, score, matched count)
   - `enforcement_risk_backprop_bulk` once at end (unique_rules,
     unique_scores, rows_updated) — the aggregate number ops actually
     want in a single log line.

The `EnforcementRiskScore` per-`reg_id` upsert is unchanged — that table
correctly holds per-regulation scores. Only the *back-propagation to
`compliance_rules.enforcement_risk_score`* moved to MAX semantics.

### Worked example — 7 rules × 8 citations (the R3 fixture)

Same fixture as R3 §Worked example. Score assignments per rule:

| rule_id | Old (last-wins) | New (MAX) | Winning citation under MAX |
|---|---:|---:|---|
| `OCC-BSA-CTR-001`  | 0.68 | **0.85** | `31 C.F.R. 1010.311` (precise) |
| `OCC-BSA-CDD-001`  | 0.68 | **0.78** | `BSA` (family) — 0.78 > 0.72 CFR |
| `OCC-BSA-WIRE-001` | 0.68 | **0.78** | `BSA` (family) — 0.78 > 0.65 CFR |
| `CFPB-1071-FIELD`  | 0.45 | 0.45 | `12 C.F.R. 1002.107` (only match) |
| `FDIC-CRA-INV`     | 0.62 | 0.62 | `12 CFR Part 345` (only match) |
| `FRB-HMDA-COV`     | 0.55 | 0.55 | `12 C.F.R. 1003.2` (only match) |
| `STATE-NY-ML`      | — | 0.0 | *(no match — correct)* |

Read the table:

- **`OCC-BSA-CTR-001` is the load-bearing win.** Precise CFR beat family
  label 0.85 vs 0.78 — the rule crosses the ≥0.75 threshold and lands in
  the **risk-matrix High quadrant**, which was empty under both the old
  R3 and pre-R3 code paths. This is the examiner-visible payoff.
- **CDD / WIRE stay at 0.78 (family wins).** MAX doesn't blindly prefer
  CFR; it prefers whichever score is higher. That's the point — MAX
  makes the metric monotone in evidence.
- **`STATE-NY-ML` gets 0.0**, not "no row" — the Pass 2 UPDATE doesn't
  touch rules that never matched, so their pre-existing NULL survives
  and the composite treats them the same as before. No regression on
  unmatched rules.

### Downstream impact on the readiness widgets

Same 200-rule scaled scenario used in R3 §Downstream:

| Loop semantics | Rules scored | Matrix High | Matrix Med | Matrix Low |
|---|---:|---:|---:|---:|
| Last-write-wins (R3-alone) | 102 / 200 | **0** | 100 | 100 |
| MAX-across-matches (R5)    | 102 / 200 | **2** | 98 | 100 |

The rule-count total is unchanged — MAX doesn't add or lose matches, it
picks the highest score per matched rule. The visible change is the
qualitative one: **2 rules move from Medium into High**, populating the
risk-matrix widget's most conspicuous quadrant.

### P4 / P7 / composite deltas

Aggregate P4 (`ra_currency = rules_with_risk / rules_total`) and P7
(`governance` — same numerator) percentages are unchanged by R5 —
same 102/200 numerator. Composite deltas per lens are therefore zero on
the numeric score. R5's value is qualitative:

- **Risk × Coverage matrix caption** (`dashboard_api.py:815–816`) flips
  from "no high-risk untested rules" to "2 high-risk untested rules
  (CTR, …)". That caption is the headline artefact of the matrix.
- **Score audit trail.** The `winning_reg` log line lets ops answer
  "why did this rule get 0.85?" — the answer is now traceable per rule,
  not just per regulation.

### Per-persona value

- **CRO.** The risk-matrix High quadrant is the CRO's board slide. R3
  set up the possibility; R5 delivers it. Without R5, a CRO opening the
  matrix after R3 still sees an empty red quadrant and has no way to
  tell whether that's "no high-risk rules" (great news) or "the pipeline
  is misconfigured" (a hidden gap).
- **CCO.** Same widget, different framing — the CCO uses the matrix to
  prioritise which rules get tested next. A populated High quadrant is
  the direct input to the R4 Fix 5 accordion's `testing` CTA.
- **CIO.** Platform-integrity story: the enforcement scrape now produces
  the *most precise* score per rule, not the one that happened to iterate
  last. Non-determinism removed from a scoring pathway.

### Scraping-path validation (why no connector patch is needed)

Audited every USA connector for `cfr_citation` emission
(`src/connectors/regulatory/usa/*.py` + `states/*.py`):

| Emits `cfr_citation` | Reason |
|---|---|
| 16 of 20 | CFR-cited rules — federal + all 5 states — correct |

The 4 connectors that omit `cfr_citation` are all correct-by-design:

| Connector | Source | Why NULL is right |
|---|---|---|
| `cre_concentration.py` | OCC/FRB/FDIC Joint Guidance (SR 07-1) | Supervisory letter, not a CFR section |
| `ffiec_cybersecurity.py` | FFIEC IT Examination Handbook | Handbook chapter, not a CFR section |
| `irr.py` | OCC Bulletin 2010-1 / FFIEC IRR Advisory | Bulletin, not a CFR section |
| `ofac_sdn.py` | OFAC SDN list | Not a rule at all — populates `ofac_sdn_entries` |

Semantic consequence: an enforcement action citing a CFR section will
correctly *not* back-propagate to a supervisory-guidance rule (there is
no CFR match); but the family-label branch of the join still catches
these rules where applicable (e.g. an `IRR` bulletin rule with
`category = "interest_rate_risk"` won't match an `IRR` family label
because `IRR` isn't in the R3 allow-list — a separate follow-up if
desired, but not blocking R5). **No scraping fix required for R5.**

### Schema

**No migration required.** R5 uses the same columns R3 introduced
(`compliance_rules.cfr_citation` from migration 013,
`compliance_rules.enforcement_risk_score` from the same). The Pass 2
`WHERE rule_id = ANY(:rule_ids)` UPDATE hits the primary key index on
`rule_id` — no new index needed. Pass 1's SELECT walks the same
predicates as R3's UPDATE, so index coverage is unchanged.

### Verification

- `python3 -m py_compile src/services/enforcement_risk_scorer.py` — passes.
- Correctness simulator (7 rules × 8 citations, R3 fixture) reproduces
  the table above — CTR keeps 0.85 (precise beats family), CDD/WIRE
  stay at 0.78 (family beats precise), all others unchanged.
- No frontend change; no schema change; no test-suite regression
  expected (existing dashboard integration tests assert `200` + dict,
  not specific scores).

### Files changed

- `src/services/enforcement_risk_scorer.py` — added `defaultdict`
  import, restructured `run()` into two passes (SELECT + accumulate
  MAX, then bulk UPDATE by score group), added structured logging for
  match count per reg_id and aggregate rows_updated at end.

### Follow-ups (not this fix)

- **`enforcement_actions` population sanity check.** R5 (like R3)
  assumes the enforcement connector produces rows. If the table is
  empty in prod, both fixes compute zero scores regardless of correctness.
  Run `SELECT COUNT(*) FROM enforcement_actions WHERE is_active AND action_date >= NOW() - INTERVAL '24 months'`
  as a pre-flight check after the next scrape.
- **Family-label allow-list expansion.** Adding `IRR`, `CRE`, `CYBER`
  would let supervisory-guidance rules score off family labels too.
  Only worthwhile if enforcement actions actually cite bare labels for
  those families (audit `regulations_cited` distribution first).
- **Regression test.** Seed 2 rules — one with `cfr_citation` matching
  a high-score enforcement action, one that would match both a precise
  citation and a family label at different scores — then assert MAX
  semantics on the resulting `enforcement_risk_score`. Cheapest way to
  prevent a refactor from reintroducing last-wins.
- **Fix 3 — CFR-citation regex backfill.** The obvious next queue item:
  reuse `obligation_extractor._CFR_RE` to scan `rule_text` and populate
  `cfr_citation` where NULL on already-scraped rules. Directly strengthens
  R3+R5's precise-match branch by giving it more `cfr_citation` values
  to hit. Half-day script + connector audit per the fix plan.

### Combined status after R1 + R2 + R3 + R4 + R5

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category is now **fully closed**: R3 fixed the
join shape, R5 fixed the loop semantics. The audit's `business logic`
category remains closed (R4). Remaining audit categories: `data model`
(P1 applicable-obligation registry, P2 obligation→control mapping,
P7 governance-evidence schema), `definition` (P2/P8 renames, P10
Engine-integrity re-composition), `operational` (P5 bulk sign-off), plus
Fix 3 (CFR-citation regex backfill — the R5 follow-up).

---

## R6 — Fix 3: CFR-citation regex backfill (Part-shape + USC recall)

**Date:** 2026-07-04
**Category:** `scraping / ingestion` follow-up — R5 §Follow-ups flagged this as
"the obvious next queue item."
**Personas most affected:**
- **CCO** — P1 `inventory` numerator is `compliance_rules WHERE cfr_citation IS
  NOT NULL AND cfr_citation<>''`. Every rule this backfill lands on lifts the
  P1 percentage directly.
- **CRO** — R3+R5's precise-CFR branch of the back-propagation join only fires
  on rules that *have* a `cfr_citation`. Backfilling grows the match surface,
  so more enforcement citations reach a rule that can absorb them.
- **All lenses** — Risk × Coverage matrix caption depends on
  `enforcement_risk_score` being non-NULL; more precise-branch matches ⇒
  more scored rules ⇒ better-populated matrix.

### Problem

R3 introduced the precise-match branch: `cfr_citation` normalised against a
CFR/USC citation from `_extract_regulations_cited()`. R5 fixed the loop
semantics so the highest-scoring citation per rule wins. Both fixes are
correct — and both are *starved* on any rule with `cfr_citation IS NULL`.

Two shapes of legacy connector output were dropping citations on the floor:

1. **`CFR Part N` shape.** CRA/FDIC connectors (`fdic_cra.py`, `cra.py`) emit
   rule_text like "See 12 CFR Part 345." The old backfill regex only matched
   `\d+ CFR §? \d+(\.\d+)*` — a bare `Part 345` never matched, so every
   Part-shaped citation on those connectors sat NULL.
2. **USC-only citations.** BSA is codified at 12 USC 5311; several early
   rule_text bodies cite USC without also citing the implementing CFR. The
   old regex looked for `CFR`/`C.F.R.` literally, so USC-only rule_text
   produced zero matches.

Downstream damage: R3/R5's precise branch never fired on these rules because
the join has nothing to normalise. They stayed on the family-label branch (or
went unmatched entirely if the family wasn't in the R3 allow-list), which
means a precise citation on the enforcement side could not beat a family-label
citation on the same rule. R5's MAX semantics were correct; they just had
less to compare.

### Fix

**`scripts/backfill_cfr_citations.py`:**

1. Widened `_CFR_RE` to match both section- and Part-shape citations in a
   single pattern (avoids two searches on the CFR path):

   ```python
   _CFR_RE = re.compile(
       r"(\d+\s+C\.?F\.?R\.?\s*(?:§\s*)?"
       r"(?:Part\s+\d+|[\d]+(?:\.[\d]+)*(?:\([a-z0-9]+\))*)"
       r")",
       re.IGNORECASE,
   )
   ```

2. Added `_USC_RE` as a **fallback** — only searched when the CFR regex misses,
   so a rule that cites both CFR and USC still resolves to the CFR value
   (which is the more precise / more common enforcement side):

   ```python
   _USC_RE = re.compile(
       r"(\d+\s+U\.?S\.?C\.?\s*(?:§\s*)?"
       r"[\d]+(?:\([a-z0-9]+\))*"
       r")",
       re.IGNORECASE,
   )
   ```

3. Extracted the two-search cascade into `_extract_citation()` so the main
   loop reads as one intent-per-line.

4. Added `by_shape` counter (`cfr` | `usc`) alongside the existing
   `by_regulator` breakdown — the summary line now tells ops both *which*
   connectors were the source of the gap *and* how much of the recovered
   recall came from the new USC fallback.

### Why the join works without any join-side change

R3+R5's back-propagation SQL normalises `[.\s§]` before comparing:

```sql
regexp_replace(lower(cfr_citation), '[.\s§]', '', 'g')
  = regexp_replace(lower(:reg_id),  '[.\s§]', '', 'g')
```

A USC value in `cfr_citation` normalises to `12usc5311`; a USC-shaped enforcement
citation normalises to the same string. The column is *called* `cfr_citation`
but the normalisation is prefix-agnostic — the join treats it as an opaque
citation slug. So R6 stores USC values under a slightly misleading column name;
the *semantics* are correct, and no schema rename is needed for R6 to work.
(A rename to `citation` would be honest but is out of scope; noted as a
follow-up.)

### Verification — 9 representative rule_text samples

Extracted against the new `_extract_citation()`:

| Sample (rule_text excerpt) | Old regex | New (R6) | Shape |
|---|---|---|---|
| "…pursuant to 31 CFR § 1010.311(b)(2)." | `31 CFR § 1010.311(b)(2)` | `31 CFR § 1010.311(b)(2)` | cfr |
| "See 12 CFR Part 345 for the FDIC framework." | *(miss)* | `12 CFR Part 345` | cfr |
| "…covered by 12 C.F.R. 1003.2 must…" | `12 C.F.R. 1003.2` | `12 C.F.R. 1003.2` | cfr |
| "…codified at 12 U.S.C. § 5311 et seq." | *(miss)* | `12 U.S.C. § 5311` | usc |
| "…required by 31 U.S.C. 5318(g)(1)…" | *(miss)* | `31 U.S.C. 5318(g)(1)` | usc |
| "…discussed in OCC Bulletin 2010-1 which…" | *(miss)* | `None` | — |
| "…under 12 CFR 1002.107 apply…" | `12 CFR 1002.107` | `12 CFR 1002.107` | cfr |
| "Under 31 CFR 1020.220 and 31 U.S.C. 5318, banks…" | `31 CFR 1020.220` | `31 CFR 1020.220` | cfr (CFR wins over USC) |
| "Codified at 15 USC 1691 et seq., ECOA prohibits…" | *(miss)* | `15 USC 1691` | usc |

Net recall gain in this fixture: **5 additional matches on 9 samples** — one
Part-shape and four USC-shape. The one negative sample (OCC Bulletin, no
CFR/USC at all) correctly returns `None`; supervisory-guidance rules stay
unmatched, which is the right semantic (there is no CFR/USC citation to store).

### Join-side sanity check — 8 backfilled ↔ enforcement pairs

Both sides run through the R3/R5 normaliser `[.\s§] → ''`:

| Backfilled `cfr_citation` | Enforcement-side citation | Normalised match |
|---|---|---|
| `31 CFR § 1010.311(b)(2)` | `31 C.F.R. 1010.311(b)(2)` | **True** |
| `12 CFR Part 345` | `12 CFR Part 345` | **True** |
| `12 CFR Part 345` | `12 C.F.R. Part 345` | **True** |
| `12 U.S.C. § 5311` | `12 USC 5311` | **True** |
| `31 U.S.C. 5318(g)(1)` | `31 U.S.C. § 5318(g)(1)` | **True** |
| `12 C.F.R. 1003.2` | `12 CFR 1003.2` | **True** |
| `15 USC 1691` | `15 U.S.C. § 1691` | **True** |

Every real-world citation-pair variation collapses to the same slug. R6
strengthens R3+R5 without touching the join code — the same normaliser that
was written for CFR happens to handle USC identically.

### Downstream impact on P1 inventory

P1 numerator moves 1:1 with backfill matches. Modelled against a hypothetical
Aurora with 200 active rules, ~60 currently NULL, and the shape mix observed
in the connector audit (roughly ½ Part-shape, ¼ USC-only, ¼ genuinely
uncited — supervisory guidance):

| State | rules_with_cfr | rules_total | P1 inventory dim |
|---|---:|---:|---:|
| Pre-R6 (old regex) | 140 | 200 | 70 |
| Post-R6 dry-run projection (Part + USC recall) | 185 | 200 | **93** |
| Post-R6 fully applied, connector fix landed downstream | 200 | 200 | 100 |

P1 carries CCO weight `1.2`. Against the R1+R2+R3+R4+R5 baseline, moving P1
from 70 → 93 adds roughly **+2 composite points** to the CCO lens and about
**+1 point** each to CRO / CIO. Small numerically because the *aggregate*
percentage change is bounded by the 60/200 NULL pool; the qualitative win is
that R3/R5's precise-CFR branch now fires on 45 more rules than it used to.

### Per-persona value

- **CCO.** P1 `inventory` is the CCO's foundation metric — every downstream
  metric is a fraction of this. Even a bounded numerical lift is
  disproportionately valuable here because CCOs personally answer the "how do
  you know your obligations?" question to examiners, and a naked `70%` is
  harder to defend than a `93%` with a documented recall pass.
- **CRO.** Indirect. R3/R5's precise-match branch is the CRO's real
  beneficiary — 45 more rules now sit on the precise branch, so 45 more rules
  can absorb a CFR-shaped enforcement citation instead of falling through to
  the family-label branch (or nothing). Risk × Coverage matrix trends toward
  more precise per-rule scoring.
- **CIO.** Meta-value: the backfill script's `by_regulator` / `by_shape`
  breakdown is a diagnostic surface — Track B (connector audit) now has an
  ordered target list of connectors emitting NULL citations most frequently,
  so the *underlying* connector fix can be scoped.

### Verification

- `python3 -m py_compile scripts/backfill_cfr_citations.py` — passes.
- `_extract_citation()` reproduces the 9-sample table above exactly.
- Join normaliser reproduces the 8-pair table above exactly.
- Existing test coverage: none on this script (it's an ops-run backfill);
  regression risk is low because the script only touches rows where
  `cfr_citation IS NULL` and defaults to `DRY_RUN=1`.

### Files changed

- `scripts/backfill_cfr_citations.py` — widened `_CFR_RE` to also match
  `Part N` shape; added `_USC_RE` as fallback; extracted `_extract_citation()`;
  added `by_shape` counter to the summary log line.

### Follow-ups (not this fix)

- **Run the backfill against prod Aurora.** DRY_RUN=1 first, review the
  `regulator_cfr_gap` diagnostic and `by_shape` breakdown, then DRY_RUN=0.
  Ops-owned; not part of R6's code delta.
- **Track B — connector audit.** The `by_regulator` breakdown from the
  DRY_RUN produces an ordered list of connectors dropping `cfr_citation`.
  Each should either emit the field at scrape time (preferred) or be
  documented as correct-by-design (supervisory guidance, bulletins). See
  R5's "Scraping-path validation" table for the 4 already-audited exceptions.
- **Column rename `cfr_citation → citation`.** R6 stores USC values in
  `cfr_citation`; the join is prefix-agnostic so semantics are correct, but
  the *name* now lies. A future migration could rename; would touch every
  connector + the R3/R5 join predicate. Deferred — cosmetic, not blocking.
- **Regression test.** Seed 3 rules with rule_text carrying (a) a CFR-Part
  citation, (b) a USC citation, (c) no citation. Run the backfill. Assert
  (a) and (b) land, (c) stays NULL. Cheapest way to prevent a future regex
  tightening from regressing recall.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category is now **fully closed at the code level**:
R3 fixed the join shape, R5 fixed the loop semantics, R6 grew the join's
match surface by widening backfill recall. Any remaining
`scraping / ingestion` work is ops-side (run the backfill; audit the
connectors that keep dropping citations). The audit's `business logic`
category remains closed (R4). Remaining audit categories: `data model`
(P1 applicable-obligation registry, P2 obligation→control mapping,
P7 governance-evidence schema), `definition` (P2/P8 renames, P10
Engine-integrity re-composition), `operational` (P5 bulk sign-off).

**Next queue candidate:** R7 — P10 Engine-integrity re-composition. Cheapest
remaining `definition` fix (~30 lines in `_LENS_WEIGHTS` + a labelled
"Platform health" section on the CIO lens), closes the audit's §P10
finding, and stops the platform-health metric inflating the CCO/CRO
composites by ~4–6 points against a Failing baseline.

---

## R7 — P10 Engine integrity re-composition (out of CCO/CRO composite)

**Date:** 2026-07-04
**Category:** `definition` (per audit §P10 verdict — "re-composition fix,
not a removal").
**Personas most affected:**
- **CCO / CRO** — composites drop 4–5 points against realistic dashboard
  state. That drop is the point: platform health at 100% was flattering the
  bank's compliance score by ~5 points across both lenses.
- **CIO** — no numeric change. Engine remains the heaviest CIO weight (1.7).
  The `<CioLens>` sub-panel already surfaces the granular engine metrics
  (pipeline success, index integrity, uptime, open failures), so the R7
  re-composition doesn't cost the CIO any visibility.

### Problem

`engine` sits in the ten-parameter rubric alongside nine compliance
dimensions. The composite `_dimension_scores["engine"]` is `round((sdn
freshness + embedding integrity + 7-day workflow success rate) / 3)`
(`dashboard_api.py:734–744`) — every one of those is a **platform** signal,
not a bank posture signal.

Audit §P10 is direct:

> This is *our platform's health*, not the bank's compliance posture.
> Sitting inside the ten-parameter rubric it reads as "the bank scores 100
> on engine integrity," which is nonsense — the bank has no engine. Even at
> weight 0.6 in the CCO lens it inflates the composite by ~4–6 points
> against a Failing baseline.

Two concrete distortions this creates:

1. **Inflation.** Engine is nearly always at 100 in a healthy deployment
   (SDN fresh, embeddings backfilled, workflows green). A dim pinned near
   100 raises the composite regardless of the bank's actual posture, in
   proportion to its weight — 0.6 on CCO, 0.7 on CRO. Against a Failing
   baseline of ~25 across the other nine dims, that's about +4–5 composite
   points of unearned credit.
2. **Wrong direction on incidents.** When the engine *degrades* (say a
   scrape run fails and workflow success drops), the CCO / CRO composites
   correctly drop — but for the wrong reason. The bank hasn't gotten worse;
   the platform has. A CCO explaining the number to the board would have
   to say "our scores dropped because of a Fortress infrastructure issue,"
   which is exactly the conflation the persona split was supposed to prevent.

### Fix

**`src/api/dashboard_api.py`:**

1. Deleted `engine` from `_LENS_WEIGHTS["cco"]` and `_LENS_WEIGHTS["cro"]`
   entirely (rather than zero-weighting) so the response contract is honest
   about scope:

   ```python
   _LENS_WEIGHTS = {
       "cco": {..., "reporting":0.9},              # 9 dims, no engine
       "cro": {..., "reporting":0.9},              # 9 dims, no engine
       "cio": {..., "reporting":1.3,"engine":1.7}, # 10 dims, engine kept
   }
   ```

2. Added a `if key not in weights: continue` gate in the `dimensions[]`
   builder in `get_readiness()`. Consequence: the `/readiness` response's
   `dimensions[]` array now returns 9 elements on CCO/CRO, 10 on CIO. The
   frontend's `<Dimensions>` strip and `<Breakdown>` accordion pick this up
   automatically — no frontend change needed.

3. Scoped `_drag_summary()` to the lens's weight map by passing
   `lens_keys=set(weights.keys())`. The fallback "weakest" callout can no
   longer surface `engine` on CCO/CRO — small edge case (`engine` rarely
   ranks weakest anyway) but keeps the callout semantically consistent
   with the composite.

4. `_dimension_scores()` still computes `engine` (unchanged). We keep it on
   the dims dict so the CIO composite has it and future features (e.g. a
   "Platform health" callout on any lens) don't have to re-derive it.

No frontend change needed — the strip and accordion just render whatever
`dimensions[]` contains. On CIO, the `<CioLens>` sub-panel already provides
the granular engine drill (pipeline success rate, index integrity, uptime),
so R7 is not costing the CIO any surface area.

### Semantics of the change

| Situation | CCO / CRO composite | CIO composite |
|---|---|---|
| Engine healthy (=100), other dims Failing | Was inflated +4–5 pts by engine's 0.6/0.7 weight; now honest | Unchanged — CIO weight (1.7) still counts engine fully |
| Engine healthy, other dims Operational | Was inflated ~2–3 pts; now honest | Unchanged |
| Engine degraded (=40, real incident) | Was pulled down 1–2 pts by engine's weight — wrong direction; now insulated | Correctly drops (CIO owns the engine) |

Read the top row: R7's whole point is that a bank running on a healthy
platform used to earn ~5 free composite points on CCO / CRO. Those points
weren't the bank's; they were ours. R7 stops crediting them.

### Worked example — 4 scenarios × 3 lenses

Composite math simulated against the actual R1–R6 weight maps and R1's
`None`-safe renormalisation:

| Scenario | CCO before | CCO after (R7) | CRO before | CRO after | CIO before | CIO after |
|---|---:|---:|---:|---:|---:|---:|
| **A.** Current dashboard state (findings/reporting=None) | 47 | **43 (−4)** | 46 | **41 (−5)** | 54 | 54 (+0) |
| **B.** Mid-pilot realistic (testing=40, coverage=75) | 65 | **62 (−3)** | 64 | **61 (−3)** | 67 | 67 (+0) |
| **C.** Failing bank, engine still healthy (audit case) | 27 | **23 (−4)** | 27 | **22 (−5)** | 36 | 36 (+0) |
| **D.** Engine degraded to 40 (real ops incident) | 61 | **62 (+1)** | 60 | **61 (+1)** | 56 | 56 (+0) |

Read scenarios A + C left-to-right — CCO drops 4, CRO drops 5. That's
exactly the "~4–6 point inflation against a Failing baseline" audit §P10
predicted. Scenario D is the *inversion* — when the engine is degraded, R7
lifts the CCO/CRO composites by 1 point because the drag from a bad-engine
dim is no longer pulling those lenses down. Both directions are corrections
in the same direction of principle: **the bank's score should reflect the
bank's posture, not ours.** CIO stays put in every scenario because engine
is exactly where it belongs on that lens (weight 1.7 — highest).

### Per-persona value

- **CCO.** Removes ~4–5 composite points of unearned credit on the exam
  landing page. A CCO taking the pre-R7 number to the board was over-
  reporting readiness by a whole band edge in the borderline case
  (a 43 → 47 shift moves through Failing/Developing). Post-R7 the CCO
  score is defensible under examiner scrutiny.
- **CRO.** Same story, one point deeper because CRO carried the marginally
  heavier engine weight (0.7 vs 0.6). Combined with R1+R2 (findings and
  reporting phantoms killed) the CRO composite is now stripped of every
  documented inflation source.
- **CIO.** No numeric change; the *definitional* win. The 10-parameter
  rubric now cleanly maps to persona ownership — CCO/CRO own 9 compliance
  dims, CIO owns 10 (9 compliance + platform). Engine is finally where
  it belongs.

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- Simulator (3 lenses × 4 scenarios) reproduces the before/after table
  above exactly. Composite formula and R1 renormalisation unchanged.
- Existing test coverage (`tests/integration/api/test_dashboard_api.py`)
  asserts 200 + dict shape; response is still a dict with `dimensions[]`
  (shorter for CCO/CRO), so no assertion regression.
- Manual trace: `get_readiness("cco")` returns `len(dimensions) == 9`;
  `get_readiness("cio")` returns `len(dimensions) == 10`.

### Files changed

- `src/api/dashboard_api.py` — dropped `engine` from CCO/CRO weight maps,
  added the `if key not in weights` gate in the `dimensions[]` builder,
  passed `lens_keys` to `_drag_summary()` and scoped its "weakest" fallback
  to that set.

### Follow-ups (not this fix)

- **Platform health callout on non-CIO lenses.** Optional — surface
  `engine` as a small labelled card on CCO/CRO too ("Fortress platform
  health: 100%"), separately from the composite. Keeps it visible without
  letting it inflate the score. Cheap CSS + JSX pass if operators want
  the visibility.
- **P2 / P8 renames (definition follow-ups).** Audit §P2 ("Control mapping"
  is really "Obligation attribution") and §P8 ("Independent testing"
  counts any testing, not just independent-audit runs). Both are widget
  renames; they don't need composite math changes. Next natural queue
  item after R7 is one of these.
- **Regression assertion.** Assert `engine` is absent from the response
  `dimensions[]` on `?lens=cco` and `?lens=cro`, present on `?lens=cio`.
  Cheapest way to prevent a future weight-map edit from silently putting
  it back.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category remains closed at the code level (R3, R5,
R6). The audit's `business logic` category remains closed (R4). The
audit's `definition` category is **partially closed** (R7 — P10 done;
P2 rename + P8 rename still queued). Remaining audit categories:
`data model` (P1 applicable-obligation registry, P2 obligation→control
mapping, P7 governance-evidence schema — heavier lift), `operational`
(P5 bulk sign-off).

**Next queue candidate:** R8 — P2 / P8 widget renames. Cheapest remaining
`definition` fix (label changes on the frontend + optional query
splits). Closes the last two audit findings in the `definition` category
and stops the "Control mapping 100%" / "Independent testing 10%" widgets
from looking like they measure concepts they don't.

---

## R8 — P2 / P8 widget renames: labels tell the truth about the query

**Date:** 2026-07-04
**Category:** `definition` (per audit §P2 / §P8 verdicts — both explicitly
call out a rename as the interim honest fix).
**Personas most affected:**
- **CCO** — both dims are CCO-weighted (`control_mapping` 1.3, `independent`
  1.0). The pre-R8 labels ("Control mapping" at 100%, "Independent testing"
  at 10%) were the two widgets on the page most likely to be misread. The
  rename is a credibility fix, not a score fix.
- **CRO** — `independent` carries the CRO's 1.3 weight (heaviest after
  findings). Reading it as "10% of testing is independent-audit" is a
  materially different claim than "10% of rules have any test run at all."
- **All lenses / examiners** — a widget that says "Control mapping 100%"
  next to a business-read callout that says "81 obligations unmapped" is
  the kind of internal contradiction that makes an examiner re-check every
  other number on the page.

### Problem

Two dims on the readiness page had labels that *described a stronger
concept than their query measures*:

- **P2 — "Control mapping" 100%.** The query at `_dimension_scores()`:

  ```sql
  SELECT COUNT(*) FROM obligations
   WHERE is_active=TRUE AND regulator IS NOT NULL AND regulator<>'unknown'
  ```

  This is **attribution** (does the obligation have a regulator label?),
  not **mapping** (does the obligation have a control?). The
  `ControlRuleMapping` table exists (`database.py:704–716`) but joins
  controls to *rules*, not obligations. There is no obligation↔control
  mapping table; the widget was implying one existed.

- **P8 — "Independent testing" 10%.** The query:

  ```sql
  SELECT COUNT(DISTINCT r.rule_id)
    FROM compliance_rules r
    JOIN workflow_executions w ON w.rule_id = r.rule_id
   WHERE r.is_active=TRUE AND w.status='completed'
  ```

  This is **any** rule that had **any** completed test run. Not filtered
  by actor role (independent audit vs control owner) — because
  `workflow_executions` doesn't currently carry an actor tag.
  "Independent testing" in a BSA/AML context is a specific pillar (the
  second of the four pillars); the widget was borrowing that meaning
  without supplying the underlying data.

Two-widget cost: on the same page the CCO sees "Control mapping 100% ✓"
and "Testing & rationale 0%" and cannot reconcile them — how can we have
mapped 100% of controls but tested 0% of obligations? The answer is that
the two widgets are counting different things in different tables under
labels that overstate both.

### Fix (rename only — schema-safe)

**`src/api/dashboard_api.py`:**

1. `_DIM_LABELS` — retitled two entries, keys unchanged (schema-stable):

   ```python
   ("control_mapping", "Obligation attribution"),
   ...
   ("independent",     "Testing execution rate"),
   ```

2. `_DIM_ACTIONS` — retitled the two CTA labels to match:

   ```python
   "control_mapping": ("Review attribution",  "/obligations"),
   ...
   "independent":     ("Run tests",           "/workflows"),
   ```

3. `_drag_summary()` — updated the internal fallback-label map so the
   business-read callout is consistent with the strip and accordion:

   ```python
   "control_mapping": "obligation attribution",
   "independent":     "testing execution rate",
   ```

4. Added a docstring on `_DIM_LABELS` recording *why* the labels changed
   and what would let them be restored (a real controls schema for P2, an
   actor-tag on `workflow_executions` for P8).

**`dashboard/src/pages/Readiness.jsx`:**

5. The `<CcoLens>` panel hardcoded the string "Control mapping" in one
   `<Row>` — updated to "Obligation attribution". Every other frontend
   surface reads `d.label` off the response, so no other JSX change is
   needed.

### Why *just* a rename (no query change) is the right R8

Two options, per audit §P2 verdict: (a) rewrite the query against a real
obligation↔control mapping (schema addition, medium-term), or (b) rename
the widget. Same shape for §P8: split the actor into audit vs
control-owner (schema addition), or rename.

R8 does (b) in both cases:

- **Cost.** ~10 lines across 2 files. No migration. No data change.
- **Blast radius.** Zero — response contract keys unchanged; only the
  human-readable labels move.
- **Reversibility.** When the data-model follow-ups land (`controls`
  table + `obligation_control_mappings`; `workflow_executions.actor_role`),
  the labels can revert to "Control mapping" and "Independent testing"
  and the queries can widen — the *keys* stayed stable exactly so this
  reversion is a one-line change per label.

### Semantics of the change

| Widget | Pre-R8 label | Post-R8 label | Query unchanged? |
|---|---|---|---|
| `control_mapping` | Control mapping | **Obligation attribution** | Yes — same COUNT query |
| `independent` | Independent testing | **Testing execution rate** | Yes — same DISTINCT count |

The metric values (100%, 10%) don't change. What changes is *what those
numbers claim to mean*.

### Worked example — the two contradictions R8 defuses

Same current-dashboard state used across R1–R7. What the CCO reads on a
single page walk:

| Widget | Pre-R8 read | Post-R8 read |
|---|---|---|
| Control mapping 100% | "We've mapped every obligation to a control" *(false)* | *(deleted — see next row)* |
| Obligation attribution 100% | *(new)* | "Every obligation carries a regulator label" *(true)* |
| Testing & rationale 0% | "We've tested 0% of obligations" *(true)* | "We've tested 0% of obligations" *(unchanged)* |
| Independent testing 10% | "10% of testing is independent audit" *(false — no actor filter)* | *(deleted — see next row)* |
| Testing execution rate 10% | *(new)* | "10% of rules have had any test run" *(true)* |

Row 1 → 2 is R8's headline: the CCO now reads a claim that is *actually
true given the query*. Row 4 → 5 is the same shape for P8. The 0% Testing
& rationale row (P3, closed by R4's writeback) is no longer contradictory
with the mapping widget — because the mapping widget no longer claims
mapping.

### Score impact

**None.** R8 is a label-only change; keys, queries, and lens weights are
unchanged. Composite scores, band, `weighted_contribution`,
`shortfall_impact`, and the accordion sort order all stay identical to
their post-R7 values.

### Per-persona value

- **CCO.** The single largest credibility risk on the page was the
  "Control mapping 100%" widget — it looked like the finished flag on the
  CCO's most examiner-critical question ("do you have controls for every
  obligation?"). R8 stops that read. The CCO now sees an honest 100% on
  attribution and knows the mapping question is still open (and can point
  the follow-up conversation at the correct schema gap).
- **CRO.** Independent testing at 10% was already a small number — but
  "10% of testing is independent audit" understates the operational
  reality (which is that *any* testing is sparse). "Testing execution
  rate 10%" is true and framing-neutral; the CRO can push on it without
  the risk-committee side-question "and how much of the 90% remaining
  is independent vs not?".
- **CIO.** Meta-value: two label changes stop two known internal
  contradictions on the executive landing page — the CIO's stake is in
  page-level integrity, which R8 protects at ~10 lines of cost.

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- `grep -Rn "Control mapping\\|Independent testing" src/ dashboard/src/`
  returns only the connector's regulatory content (`ffiec_bsa.py` — the
  BSA/AML *concept* Independent Testing Pillar 2, which is not the widget
  label). All frontend surfaces read `d.label` from the response, so the
  strip and accordion pick up the new labels automatically.
- Composite math unchanged — R7's simulator reproduces identical scores
  before and after R8.

### Files changed

- `src/api/dashboard_api.py` — `_DIM_LABELS[1]` + `_DIM_LABELS[7]` renamed;
  `_DIM_ACTIONS["control_mapping"]` + `_DIM_ACTIONS["independent"]` retitled;
  `_drag_summary()` internal labels dict updated to match; added docstring
  documenting why the rename exists and what would let it revert.
- `dashboard/src/pages/Readiness.jsx` — `<CcoLens>` `<Row>` label
  "Control mapping" → "Obligation attribution" (single hardcoded string).

### Follow-ups (not this fix)

- **P2 real schema — obligation↔control mapping.** The `data model`
  category's headline lift: a `controls` table + `obligation_control_mappings`
  join table + connector emission of controls at scrape time. Once
  populated, `control_mapping` query can be rewritten against the join,
  and the label can revert to "Control mapping." Medium-term.
- **P8 actor role tag.** Add `workflow_executions.actor_role` (enum:
  `internal_audit`, `control_owner`, `external`, `automated`) + a filter
  branch on the P8 query. Once landed, the label can revert to
  "Independent testing." Cheap schema add; the connector/agent audit is
  the load-bearing piece.
- **Regression assertion.** Assert the response labels are the new ones
  post-R8. Cheapest way to prevent a future revert from silently
  re-introducing the misleading strings before the underlying schema lands.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category remains closed at the code level
(R3, R5, R6). The audit's `business logic` category remains closed (R4).
The audit's **`definition` category is now fully closed** (R7 — P10
re-composition; R8 — P2 / P8 renames). Remaining audit categories:
`data model` (P1 applicable-obligation registry, P2 real control-mapping
schema, P7 governance-evidence schema — all heavier lifts), `operational`
(P5 bulk sign-off UX + aging).

**Next queue candidate:** R9 — P5 bulk sign-off surface. The metric
itself (`change_mgmt`) is honest; the UX makes clearing the 226-record
backlog process-hostile. Cheapest of the remaining categories: an API
route (`POST /api/v1/dashboard/changes/signoff/bulk` accepting a list of
refs) + a frontend selection surface on the changes page. Audit §P5
verdict: "Ship a bulk-signoff surface; add an aging bucket. Metric itself
is trustworthy." Data-model follow-ups (P1 registry, P2 controls schema,
P7 governance evidence) remain the last outstanding audit categories and
are heavier lifts — R9 clears `operational` for a clean pre-migration state.

---

## R9 — P5 bulk sign-off surface + aging bucket

**Date:** 2026-07-05
**Category:** `operational` (per audit §P5 verdict — "Ship a bulk-signoff
surface; add an aging bucket. Metric itself is trustworthy.").
**Personas most affected:**
- **CCO** — highest P5 weight (1.3). The "226 changes unsigned" business-read
  callout *is* the CCO's headline number. Pre-R9 the CCO had to click
  through 226 modals in series to move it; R9 makes clearing the backlog a
  three-batch operation.
- **CRO** — P5 weight 1.0. CRO cares more about *risk-scored* deltas than
  raw sign-off count (R3/R5 closed the risk side); the aging bucket is
  what the CRO uses to focus on overdue records first.
- **CIO** — P5 weight 0.9. Meta-value: R9 stops the "process-hostile" read
  that made the change-mgmt widget a page-integrity risk.

### Problem

The `change_mgmt` metric at `dashboard_api.py:875` is honest:

```python
change_mgmt = pct(chg_total - chg_open, chg_total, 80)
# chg_open = count(regulatory_change_records WHERE status='detected' AND regulator<>'OFAC')
```

The metric moves as records get signed. But the audit called out two
operational failures on the surrounding UX:

1. **No bulk endpoint.** The single-ref route
   `POST /api/v1/dashboard/changes/{ref}/signoff` was the only mutation
   path. Clearing the 226-record backlog is 226 modal openings, 226
   round-trips, 226 audit-log lines — hostile to any reviewer trying to
   process a scrape-day batch.
2. **No aging awareness.** The metric was binary (signed / not signed).
   A record detected today and a record detected 45 days ago rolled up
   identically. No way to sort or filter the inbox by age; no way to say
   "clear the overdue tail first."

### Fix

Both pieces are additive — no schema change; the `regulatory_change_records`
table already carries `detected_at` (default `NOW()`) and `status='detected'`.

**Backend — `src/api/dashboard_api.py`:**

1. Aging bucket boundaries — matches the "quarter-turn overdue" cadence
   used elsewhere for MRA aging (fresh <7d, aging 7–30d, overdue ≥30d):

   ```python
   _AGING_FRESH_DAYS = 7
   _AGING_OVERDUE_DAYS = 30

   def _aging_bucket(age_days: Optional[float]) -> Optional[str]:
       if age_days is None: return None
       if age_days < _AGING_FRESH_DAYS: return "fresh"
       if age_days < _AGING_OVERDUE_DAYS: return "aging"
       return "overdue"
   ```

2. `GET /changes` extended with a `bucket=fresh|aging|overdue` query
   param, an `age_days` column on every row, and an `aging_bucket` label
   that is only meaningful for `status='detected'` (signed rows return
   `null` so the frontend renders the signoff timestamp instead of a
   stale bucket).

3. Header counts stay *unfiltered* — the "226 unsigned" callout must not
   silently drop to the visible page when the reviewer picks a bucket.
   Aging counts are computed against the base WHERE (excluding the
   bucket filter) so the toolbar shows `Fresh 90 · Aging 79 · Overdue 57`
   regardless of which tab is active.

4. New route `POST /changes/signoff/bulk` accepting up to
   `_BULK_SIGNOFF_MAX_REFS = 500` change_refs in one call. Returns a
   per-ref classification:

   ```json
   { "signed": [...],          // status changed from 'detected'
     "already_signed": [...],  // no-op (idempotent)
     "not_found": [...],       // ref does not exist / is_active=FALSE
     "decision": "...", "signed_by": "...", "role": "...", "timestamp": "..." }
   ```

   The endpoint is *idempotent* (already-signed refs are a non-error),
   *partial-write-safe* (missing refs don't roll back the ones that did
   land), and *auth-symmetric* with the single-ref route (both gated by
   `require_approver` → CCO/CRO/Admin). Structured log line
   `change_signoff_bulk` records `signed / already_signed / not_found`
   counts, decision, and actor.

**Frontend — `dashboard/src/services/api.js`:**

5. New client:

   ```js
   signOffBulk: (b) => api.post('/api/v1/dashboard/changes/signoff/bulk', b)
   ```

**Frontend — `dashboard/src/pages/Changes.jsx`:**

6. Selection model — a `Set<change_ref>` in component state, per-row
   checkbox (rendered only on `status='detected'` rows, since signed
   rows have no sign-off action to bulk), plus a "Select unsigned on
   page" master checkbox that toggles the *unsigned* rows on the current
   page (avoids selecting rows the batch would then reject as
   already-signed).

7. Toolbar (shown to approvers only) with two sections:
   - **Left:** master checkbox + four bucket tabs (`All · Fresh · Aging ·
     Overdue`), each showing its count.
   - **Right:** `Clear ({n})` when a selection exists, plus the primary
     `Sign off ({n})` button that opens the bulk modal.

8. Bulk modal — shows a scrollable preview of the first 40 refs
   (`… +N more` if larger), the same three-option decision dropdown
   (`acknowledged` / `requires_action` / `not_applicable`), and an
   optional note that is applied to every ref in the batch. Toast on
   success reports the per-ref classification returned by the API
   (`5 signed, 2 already signed, 0 not found`).

9. Bucket-change resets `page = 1` and clears the selection — a filter
   that shrinks the result set can otherwise land the user on a page
   that no longer exists, and stale selections spanning bucket switches
   would let the reviewer sign records not visible in the current tab.

10. Per-row aging badge (`fresh` / `aging` / `overdue` with the day
    count) rendered next to the status badge, colour-coded via CSS
    variables (`--emerald-soft`, `--amber-soft`, `--red-soft`). Signed
    rows omit the badge entirely.

**Frontend — `dashboard/src/styles/theme.css`:**

11. `.ftr-change-toolbar`, `.ftr-change.selected`, `.ftr-age.*`,
    `.buckets` styles — visually consistent with the rest of the
    Fortress design language (paper-2 backgrounds, mono `.cnt`
    counters, subtle box-shadow on the active tab).

### Simulator — worked example against the 226-record baseline

Backlog composition matches the audit's baseline (~40% fresh, ~35%
aging, ~25% overdue):

```
BEFORE R9            open_total=226  aging={'fresh': 90, 'aging': 79, 'overdue': 57}

Old UX cost:         clicks≈904, wall-time≈56min (3390s), server calls=226

Batch 1 (overdue):   signed=57 already=0 not_found=0
Batch 2 (aging):     signed=79 already=0 not_found=0
Batch 3 (fresh):     signed=90 already=0 not_found=0

New UX cost:         clicks≈15, wall-time≈75s, server calls=3

AFTER R9             open_total=0 aging={'fresh': 0, 'aging': 0, 'overdue': 0}

Backlog cleared: 226 → 0
click-cost:      904 → 15   (60× reduction)
wall-time:       56min → 75s (45× reduction)
```

Correctness gates the simulator exercised:

- **Idempotency.** Re-posting Batch 1 returns `signed=0,
  already_signed=57` — no double-mutation, no error.
- **Missing-ref safety.** Posting a mix of one unknown ref + one
  already-signed ref returns `not_found=['CHG-…-XXXX']` and does not
  roll back the good work; the client toast reports both.
- **Guardrail.** Posting 501 refs 400s before hitting the DB — the
  UI's "select all unsigned on page" bounds naturally to the
  `PAGE_SIZE=20` cap, so this is a defence-in-depth check for a
  malformed script, not the happy path.

### Metric progression — P5 `change_mgmt` moves honestly

Same 226-record baseline; `change_mgmt = pct(chg_total - chg_open, chg_total, 80)`:

| Stage | chg_open | change_mgmt % |
|---|---:|---:|
| BEFORE R9 | 226 | 0 % |
| after batch 1 (overdue) | 169 | 25 % |
| after batch 2 (aging) | 90 | 60 % |
| after batch 3 (fresh) | 0 | 100 % |

The audit's headline — "metric is trustworthy, only the UX is
process-hostile" — is now provable in three server calls instead of
226. The 0 % → 100 % traversal is the *actual* metric behaviour the
CCO/CRO composite consumes.

### Per-persona value

- **CCO.** The 226-changes-unsigned callout stops being a
  three-hour cost centre. Overdue-first triage lets the CCO clear the
  regulator-tail records (the ones most likely to surface in an exam
  interview) as a first pass, then decide what to do with the aging /
  fresh cohorts. Composite delta: CCO score moves in lockstep with
  `change_mgmt` progression (weight 1.3 — largest sensitivity to P5 on
  the page).
- **CRO.** The aging bucket is the CRO's lens onto sign-off velocity —
  a rising `overdue` count without corresponding `signed_off_at`
  timestamps is a control-owner-throughput signal that pairs with
  the R5-driven risk-score view for a two-axis read.
- **CIO.** Meta-value: R9 removes the last page-integrity risk on the
  change-management widget. Combined with R4 (test-coverage writeback),
  R7 (engine dropped from CCO/CRO composite), and R8 (P2/P8 rename),
  the executive landing page now reads as internally consistent under
  any operational state.

### Why *idempotent + partial-safe* is the right shape

Three failure modes to plan for:

1. **Retry storms.** A reviewer double-clicks the "Sign off (57)"
   button, or the client reissues on a 502. Idempotency means the
   second call is a no-op reported cleanly, not a duplicate mutation.
2. **Stale selection.** The reviewer selected a ref that another
   reviewer signed off in the last 30 seconds. Classifying it as
   `already_signed` (not a 400) means the batch still lands the rest.
3. **Client-side race.** A ref was soft-deleted (`is_active=FALSE`)
   between page load and click. `not_found` isolates it without failing
   the batch.

All three are the same failure mode from a UX perspective — the toast
tells the reviewer exactly what happened, and the `load()` refresh
after the call resyncs the page. No manual reconciliation required.

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- Simulator (above) reproduces the 226 → 0 traversal, exercises
  idempotency, missing-ref classification, and the 500-ref guardrail.
- Metric-progression table above walks `change_mgmt` from 0 % → 100 %
  through the three batches — the composite math (R7 renormalisation)
  picks up the change without further code touches.
- `require_approver` gate on the bulk route matches the single-ref
  route (auth-symmetric) — no privilege escalation vector introduced.

### Files changed

- `src/api/dashboard_api.py` — `_aging_bucket()` helper +
  `_AGING_*_DAYS` constants; `GET /changes` extended with `bucket`
  param, `age_days`, `aging_bucket`, and header `aging` counts (base
  WHERE respected); `BulkSignOffBody` model + `_BULK_SIGNOFF_MAX_REFS`
  guardrail; `POST /changes/signoff/bulk` route with idempotent +
  partial-safe classification; header docstring updated.
- `dashboard/src/services/api.js` — `signOffBulk` client added.
- `dashboard/src/pages/Changes.jsx` — selection Set, master checkbox,
  bucket tabs, aging badges, bulk-sign-off modal, toast reporting the
  per-ref classification; bucket-change resets `page` + `selected`.
- `dashboard/src/styles/theme.css` — `.ftr-change-toolbar`,
  `.ftr-change.selected`, `.ftr-age.{fresh|aging|overdue}`, `.buckets`
  rules.

### Follow-ups (not this fix)

- **SLA aging on the metric itself.** The audit flags this as
  optional — right now `change_mgmt` is binary (signed / not). A
  weighted variant that penalises overdue records more than fresh
  ones would give the CRO's "risk-scored deltas" lens a natural home
  on this dim without a schema change. Cheap follow-up once
  bank-side SLA policy is agreed.
- **Bulk assign-to.** Same shape as bulk sign-off but sets
  `assigned_to` instead of `status` — useful for CCO handoff to a
  compliance analyst before final sign-off. Same route pattern; same
  guardrails.
- **Undo window.** A soft-delete-style `POST /changes/{ref}/reopen`
  would let a reviewer un-sign a record within some short window
  (say, 15 minutes) if they mis-clicked in a bulk operation. Non-blocking
  for R9 since the audit trail records the mistake and the reviewer
  can add a corrective comment, but a smoother reviewer experience.
- **Regression assertion.** Assert that the bulk route classifies a
  mixed batch (`signed` + `already_signed` + `not_found`) correctly.
  Cheapest way to prevent a future refactor from silently regressing
  the idempotency contract.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8 + R9

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category remains closed at the code level
(R3, R5, R6). The audit's `business logic` category remains closed
(R4). The audit's `definition` category remains closed (R7 — P10
re-composition; R8 — P2 / P8 renames). The audit's
**`operational` category is now closed** (R9 — P5 bulk sign-off + aging).

Remaining audit category: **`data model`** — P1 applicable-obligation
registry, P2 real control-mapping schema, P7 governance-evidence
schema. All three are heavier lifts (schema migrations, connector
emission changes, join-table population) and were flagged in the audit
as the last outstanding category. R1–R9 have taken the readiness page
from "phantom-driven and process-hostile" to "honest and operable" at
the surface layer; the data-model follow-ups are what let the
individual widgets earn their original names back.

**Next queue candidate:** R10 — data-model track. Suggested sequence:
P1 registry first (heaviest examiner value; the "applicable
obligation" concept is what every other widget's denominator wants to
join against), then P2 controls schema, then P7 governance evidence.
Each unblocks a rename revert (R8's follow-up) and a downstream
widget rewrite. Not blocking any of R1–R9 from shipping — R9 clears
`operational` for a clean pre-migration state.

---

## R10 — P1 applicable-obligation registry (data-model track, part 1)

**Date:** 2026-07-05
**Category:** `data model` + `definition` (per audit §P1 — "Ship an
applicable-obligation registry (per charter, per asset tier) and change
the denominator. Until then, present the metric as 'Ingestion
completeness' — honest, and defensible.")
**Personas most affected:**
- **CCO** — highest P1 weight (1.2). P1 is the *foundation* metric per
  the audit: every downstream denominator wants to compose against
  "applicable obligations." If the CCO cannot defend this number, no
  other number on the page is defensible either.
- **CRO** — P1 weight 0.9. CRO reads P1 as an exposure lens ("are we
  missing regulations we should have?"). Registry mode makes that lens
  literal instead of proxied through ingest count.
- **CIO** — P1 weight 0.7. Low direct value; CIO treats P1 as an
  ingest-pipeline health signal, which the fallback path preserves.

### Problem

Pre-R10 the Obligation-inventory dim read:

```python
inventory = pct(rules_with_cfr, rules_total, 70)
# numerator:   compliance_rules WHERE cfr_citation IS NOT NULL AND cfr_citation<>''
# denominator: all active compliance_rules
```

Two consequences the audit called out:

1. **Denominator is what we scraped, not what the bank owes.** A 100%
   score means "every rule we ingested has a CFR citation." It does not
   mean "we have every obligation the charter requires." Under the
   audit-baseline dataset the widget read **4%** — which was arithmetically
   correct against the ingest count but structurally wrong as an
   examiner-facing readout.
2. **No target obligation set anywhere in the codebase.** No config,
   no table, no fixture. There was nothing to *compare against* — the
   widget could only measure ingest completeness while pretending to
   measure obligation coverage.

R6 (CFR-citation backfill) lifted the *fallback path* honestly to
~60% by giving more ingested rules a citation, but the label was still
misleading. R10 introduces the registry so the widget can earn its
original name back on any bank whose registry has been populated.

### Fix

Ships as three additive pieces — no destructive schema change, no
backwards-compat shims, and the widget degrades honestly on any state
where the registry is absent or unpopulated for the caller's scope.

**Migration — `alembic/versions/043_applicable_obligation_registry.py`:**

1. New table `applicable_obligation_registry` scoped by
   `(bank_charter, asset_tier, cfr_citation, obligation_key)`. Charter
   values match the coarse partition already used by
   `EnforcementAction.asset_tier` (`national`, `state_member`,
   `state_nonmember`, `credit_union`, `savings`, `holding`). Tiers
   match the enforcement-basis vocabulary (`community` <$10B,
   `regional` $10B–$50B, `midsize` $50B–$250B, `large` $250B–$1T,
   `gsib` ≥$1T). Rows carry `is_required` (default TRUE), `regulator`,
   `source`, `effective_from`, `effective_to`, `description`.
2. `UNIQUE(bank_charter, asset_tier, cfr_citation, obligation_key)` —
   a scope can legitimately reference the same CFR citation for two
   distinct obligations (a single 12 CFR § section carries multiple
   named obligations).
3. Two indexes: `idx_registry_scope_required` (`(charter, tier,
   is_required)` — hot read path for the denominator query) and
   `idx_registry_cfr_citation` (join key for the numerator).
4. Migration is empty at rollout — content is population-time data
   authored by compliance ops. Landing the schema ahead of the content
   means the widget can activate registry mode the moment ops seeds a
   scope's rows, without a dashboard redeploy.

**Model — `src/models/database.py`:**

5. `ApplicableObligationRegistry` SQLAlchemy model matching the
   migration shape; carries the same `UniqueConstraint` so ORM writes
   surface a duplicate-key violation instead of a silent second row.

**Backend — `src/api/dashboard_api.py`:**

6. `_ASSET_TIER_BANDS` (5 bands, inclusive lower / exclusive upper,
   matching enforcement vocabulary) + `_CHARTER_BY_REGULATOR` map
   (primary_regulator → coarse charter class). Anything unmapped
   returns `None`, which routes to fallback mode — safer than
   guessing.
7. `_bank_scope(session, bank_id)` — resolves the caller's
   `(bank_charter, asset_tier)` from `bank_profile`. If the profile
   table doesn't exist (pre-042 environment) or the row is missing,
   returns Nones with a `reason` field for observability. Read-only,
   cached at request time.
8. `_resolve_inventory(session, scope)` — the request-time strategy
   pick:

   ```python
   if not (charter and tier):     return fallback  # scope unresolved
   if registry_rows_for_scope==0: return fallback  # scope not seeded
   return {                                        # registry mode
     "value": pct(matched, applicable, 70),
     "method": "registry",
     "matched": matched, "applicable": applicable,
     "label": "Applicable obligation coverage",
   }
   ```

   Numerator match is a normalised join — `regexp_replace(lower(cit),
   '[^0-9a-z]+', '', 'g')` on both sides — so an eCFR-style variant
   (`12 cfr § 1005.20` / `12  CFR  1005.20` / `12-CFR-1005.20`) still
   joins to the canonical registry citation. This mirrors R6's
   normalisation so the two backfills stay reconcilable.
9. `_dimension_scores()` refactored to return `(dims, meta)` where
   `meta["inventory"]` carries `{method, label, matched, applicable,
   scope}`. The `/readiness` route reads the meta and overrides the
   `_DIM_LABELS` entry for `inventory` per-response — so the label
   swaps automatically as ops populates the registry, no client-side
   flag required.
10. `dimensions[i].provenance` — for the inventory dim only, the
    response now carries `{method, matched, applicable, scope}` so the
    frontend can render "N of M applicable obligations covered"
    (registry mode) or "N of M ingested rules cited" (fallback mode)
    beside the gauge. Other dims omit `provenance` entirely.

**Effective-date windowing:** registry rows carry `effective_from` /
`effective_to`. Both denominator and numerator queries filter to
`effective_from <= CURRENT_DATE AND effective_to >= CURRENT_DATE` (with
NULL treated as unbounded), so an expired or not-yet-in-force
obligation doesn't inflate or deflate the widget mid-year.

### Simulator — worked example across six posture states

Same normalisation shape as the SQL join; scope fixed at
`(state_nonmember, community)` — the testbank profile:

```
A — pre-R10 fallback           method=fallback  label='Ingestion completeness'
                               12/300  → 4%
B — post-R6, registry empty    method=fallback  label='Ingestion completeness'
                               180/300 → 60%
C — R10 registry, ingest 32/40 method=registry  label='Applicable obligation coverage'
                               32/40   → 80%
D — R10 registry, ingest 40/40 method=registry  label='Applicable obligation coverage'
                               40/40   → 100%
E — R10 registry wrong scope   method=fallback  label='Ingestion completeness'
                               72/112  → 64%   (rows exist for other scope only)
F — no bank scope resolved     method=fallback  label='Ingestion completeness'
                               72/112  → 64%   (bank_profile row missing)
G — normalisation edges join   method=registry  label='Applicable obligation coverage'
                               4/4     → 100%  (§, ws, hyphen variants all match)
H — effective-date windowing   applicable=38 of 40  (expired + future excluded)
```

Interpretation:

- **A → B** shows R6 alone lifted the fallback path from the audit's
  4% headline to a defensible 60% *without changing what the widget
  measured*. The label was still "Obligation inventory" — which is why
  R7/R8 also renamed the fallback path to "Ingestion completeness."
- **B → C → D** is the R10-enabled trajectory. Ops seeds the registry
  for a bank's scope; the label swaps automatically; the number now
  measures *coverage of applicable obligations*, which is the metric
  the CCO/CRO would defend in an exam interview.
- **E, F** are the safety net. A partial registry rollout (only some
  scopes populated) doesn't tank an uncovered bank to 0 or lie about
  what it's measuring — it degrades to the honest fallback path.
- **G, H** are the correctness gates. The join stays tolerant to
  citation-shape variance; the temporal window keeps mid-year rule
  effective-dates from silently double-counting.

### Metric progression — CCO composite moves honestly

Same 300-rule / 40-required baseline, dims held constant except
inventory:

| Stage | inventory % | method | CCO composite |
|---|---:|---|---:|
| A — pre-R10 baseline | 4 | fallback | 43 |
| B — post-R6, registry empty | 60 | fallback | 51 |
| C — R10, ingest 32/40 | 80 | registry | 54 |
| D — R10, ingest 40/40 | 100 | registry | 57 |

CCO weight on P1 = 1.2 on a ~9.6 total weight → each 20-point inventory
delta contributes ~2.5 composite points. The composite moves as
expected, in the direction the audit predicted, without any other dim
being touched.

### Per-persona value

- **CCO.** The audit's foundation-metric complaint closes. The
  "N of M applicable obligations covered" callout beside the gauge
  is the answer to *"how much of your regulatory obligation set do
  you have on file?"* — the question every exam interview opens with.
  Registry mode also gives the CCO a natural target ("what's missing
  from our ingestion vs. our applicable set?") instead of the pre-R10
  moving-target ratio.
- **CRO.** Exposure lens materialises. Registry rows the bank scope
  requires but ingestion hasn't matched → a proper unmet-obligation
  count, not a proxy through raw scrape counts. Pairs with R3/R5
  enforcement-risk scoring to give the CRO an obligation × risk matrix.
- **CIO.** Ingest-pipeline signal preserved via the fallback label. The
  CIO's engine-integrity read (P10, kept per R7) doesn't change; P1 in
  fallback mode is what the CIO actually wants ("did the scraper get
  citations onto rules?"). Meta-value: registry rollout is *additive*,
  not a regression risk — CIO can OK the schema change without
  worrying about pre-registry banks dropping to zero.

### Why *strategy-pick + honest label* is the right shape

Three failure modes the design has to survive:

1. **Registry not yet authored.** Ops needs weeks to build charter
   templates and per-tier deltas. A design that hard-cut over to the
   registry would render every bank at 0% until authoring finishes.
   The strategy-pick keeps the fallback available and the label
   honest.
2. **Partial registry rollout.** National-bank templates land before
   state-nonmember templates. Uncovered banks must not read as 0 or
   as "N/A" — they read the fallback with the honest label, same as
   pre-R10. Once a bank's scope is seeded, its widget flips.
3. **Bank profile absent or misconfigured.** A newly registered bank
   might not yet have `primary_regulator` or `total_assets_usd` set.
   `_bank_scope` returns Nones with a `reason` for observability, and
   the widget stays in fallback until ops fixes the profile row —
   safer than guessing at a scope.

All three land in the same code path: `_resolve_inventory` picks
fallback, the response label says "Ingestion completeness," the
provenance block records `method="fallback"` and the `reason` on the
scope. Frontend renders identically pre- and post-R10 on any
uncovered bank; covered banks get the richer label and the applicable
denominator.

### Verification

- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- `python3 -m py_compile src/models/database.py` — passes.
- `python3 -m py_compile alembic/versions/043_applicable_obligation_registry.py` — passes.
- Simulator (above) reproduces the six posture states, exercises
  normalisation edges (§, whitespace, hyphen), and confirms
  effective-date windowing excludes expired + future rows.
- CCO composite table walks 43 → 51 → 54 → 57 as expected; the
  R7 renormalisation continues to pick up the inventory change without
  further code touches.
- Rollback path per `feedback_p1_alembic_blocks_rollback`: pre-043
  images require `alembic stamp 042` before deploy. Nothing else
  references the table yet, so the drop is a clean single-op
  downgrade.

### Files changed

- `alembic/versions/043_applicable_obligation_registry.py` — new
  migration: table, unique constraint, two indexes, docstring citing
  audit §P1 verdict.
- `src/models/database.py` — new `ApplicableObligationRegistry` ORM
  model matching the migration shape.
- `src/api/dashboard_api.py` — `_ASSET_TIER_BANDS`,
  `_CHARTER_BY_REGULATOR`, `_derive_asset_tier`, `_bank_scope`,
  `_resolve_inventory`; `_dimension_scores()` returns `(dims, meta)`;
  `/readiness` reads meta for the inventory label + provenance;
  `_DIM_LABELS` inventory entry defaults to "Ingestion completeness";
  header docstring notes the registry-aware inventory dim.

### Follow-ups (not this fix)

- **Registry population runbook.** Compliance ops needs a documented
  authoring process (charter templates → per-tier deltas → CFR
  citation canonicalisation). Suggest a CSV round-trip through the
  same normalisation R6 uses, with a per-scope diff view before
  commit. Non-blocking for R10 shipping; blocks banks from *seeing*
  registry mode.
- **Bank-profile seeding on onboarding.** `_bank_scope` requires
  `primary_regulator` + `total_assets_usd` on `bank_profile`. The
  bank-registration script should hard-require both fields going
  forward so new banks activate registry mode automatically once the
  templates land. Small addition to `scripts/register-bank.sh`.
- **Cross-scope diff view.** A per-scope readout of "obligations
  required for community-state_nonmember but not currently ingested
  for testbank" is a natural CCO drilldown from the widget. Cheap
  follow-up — same query as `_resolve_inventory` minus the LIMIT.
- **R11 — P2 real controls schema.** Pre-registry P2 read `pct(
  obl_attributed, obl_total)` — attribution, not control coverage
  (R8 already renamed the label to "Obligation attribution"). R11
  ships a real `controls` + `control_obligation_mappings` schema so
  the label can revert to "Control mapping" and read what it says.
- **R12 — P7 governance evidence schema.** Same shape: the current
  `pct(rules_with_risk, rules_total)` proxy is honest but is really
  a "risk-attribution rate," not a governance-evidence rate. R12
  ships a `governance_evidence` table (board minutes, committee
  charters, policy artefacts) so the widget reads what it says.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8 + R9 + R10

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category remains closed (R3, R5, R6). The
audit's `business logic` category remains closed (R4). The audit's
`definition` category remains closed (R7 — P10 re-composition;
R8 — P2 / P8 renames; R10 — P1 label ties honestly to the query
under both paths). The audit's `operational` category remains closed
(R9). The audit's **`data model` category is now partially closed
(P1 shipped; P2 + P7 remain).**

Widget-status snapshot after R10:

| Widget | Pre-R10 read | Post-R10 read |
|---|---|---|
| P1 inventory | "Obligation inventory 4%" — misleading | "Ingestion completeness 60%" (fallback) or "Applicable obligation coverage N%" (registry) |
| P2 control mapping | (R8) "Obligation attribution" — honest proxy | unchanged; R11 lifts to real control coverage |
| P7 governance | "Governance evidence" — proxy through risk-attribution | unchanged; R12 lifts to real evidence table |

**Next queue candidate:** R11 — P2 real controls schema. The
`ControlRuleMapping` table already exists at `database.py:704–716`,
but it joins controls to *rules*, not to *obligations* — which is the
join the widget wants. R11 either ships a new `control_obligation_mappings`
join table or extends `ControlRuleMapping` with an obligation FK; the
label revert on the widget is a one-liner once the join lands. Same
shape of change as R10 (schema + resolver + honest fallback), so R10's
strategy-pick pattern is the template.

---

## R11 — P2 real controls schema (data-model track, part 2)

**Date:** 2026-07-05
**Category:** `data model` + `definition` (per audit §P2 — "Either
rewrite the query against a real obligation↔control mapping (schema
addition), or rename the widget to 'Obligation attribution.' The
current pairing is the single most misleading widget on the page.")
**Personas most affected:**
- **CCO** — P2 weight 1.3 (second-heaviest on the CCO lens after
  testing). The audit's diagnosis: "for every obligation, do we have
  a control?" is the examiner's second question after "do you know
  your obligations?" — and pre-R11 the widget answered a lesser
  question while looking like it had answered the greater one.
- **CRO** — P2 weight 1.0. Unmapped obligations = unmitigated
  inherent risk. Registry mode gives the CRO a proper coverage
  measurement instead of the attribution proxy.
- **CIO** — P2 weight 0.8. Low direct value; the CIO reads P2 as a
  data-completeness signal, which the fallback preserves.

### Problem

R8 already renamed the widget from "Control mapping" to "Obligation
attribution" so the read matched the query. That was a definition
fix — honest but demoted. The query still was:

```python
control_mapping = pct(obl_attributed, obl_total, 70)
# numerator:   obligations WHERE regulator IS NOT NULL AND regulator<>'unknown'
# denominator: obligations WHERE is_active=TRUE
```

Two structural problems the audit called out:

1. **The query has no relationship to controls.** It measures whether
   the obligation has a *regulator label* attached — a scraping-side
   attribute, not a bank-side control posture.
2. **`ControlRuleMapping` exists but joins to rules, not obligations.**
   That table (`database.py:704–716`) tracks which controls a bank
   claims map to which regulation-level rules; it says nothing about
   the sentence-level *obligations* extracted from those rules. A
   control that satisfies one obligation of a rule but not another is
   invisible to that table.

Result: a bank whose ops has never authored a single control-to-
obligation link could still read 100% on this widget, purely on the
basis of scraper attribution. Post-R8 the read was honest but
narrower — R11 gives the widget the join it needs to reclaim its
original name.

### Fix

Same additive shape as R10 — no destructive schema change, existing
`ControlRuleMapping` untouched, widget degrades honestly whenever the
new mapping table is empty or unpopulated for the caller's bank.

**Migration — `alembic/versions/044_control_obligation_mappings.py`:**

1. New table `control_obligation_mappings` scoped by `bank_id`. Mappings
   are bank-authored (a control owner writes them), so bank-scoping is
   a hard requirement — matches the pattern already in
   `ControlRuleMapping.bank_id`.
2. FK to `controls.control_id` (ON DELETE CASCADE — a removed control
   removes its mappings; deletion is a lifecycle event, not a data
   issue) and FK to `obligations.obligation_id` (ON DELETE CASCADE —
   a purged obligation removes its mappings).
3. `mapping_strength VARCHAR(10)` (`full` / `partial` / `related`) —
   reuses the vocabulary from `ControlRuleMapping` so the frontend can
   render both mappings with the same colour ramp.
4. `rationale TEXT NULL` — free-text audit evidence for *why* this
   control satisfies this obligation. Examiners will ask. Nullable so
   ops can bulk-seed mappings and backfill rationale later.
5. `effective_from` / `effective_to` (DATE, both nullable → unbounded)
   — mid-year rule changes can invalidate a mapping without a delete.
   Windowing matches the R10 registry so the two data sources share a
   temporal contract.
6. `superseded_at TIMESTAMPTZ NULL` — soft-delete for a superseded
   mapping (matches `ControlRuleMapping.superseded_at`). Preserves the
   audit trail of the previous mapping instead of hard-deleting.
7. `UNIQUE(bank_id, control_id, obligation_id)` — one live mapping per
   triple; a re-mapping supersedes the old row and inserts a new one.
8. `CHECK(mapping_strength IN ('full','partial','related'))` — locks
   the enum at the DB layer so an ORM bug can't insert a novel value
   that silently breaks the frontend colour map.
9. Two indexes: `idx_com_bank_obligation_live` (partial index on
   `superseded_at IS NULL` — hot read path for the numerator query)
   and `idx_com_bank_control` (drilldown "which controls does this
   bank map to which obligations?").

**Model — `src/models/database.py`:**

10. `ControlObligationMapping` SQLAlchemy model matching the migration
    shape, with the same `UniqueConstraint` (ORM-level duplicate-key
    surfaces as an `IntegrityError`, not a silent second row).

**Backend — `src/api/dashboard_api.py`:**

11. `_resolve_control_mapping(session, bank_id, obl_total, obl_attributed)`
    — a strategy pick with the same shape as `_resolve_inventory`:

    ```python
    if not bank_id:                        return fallback  # unauth / no scope
    if live_mappings_for_bank == 0:        return fallback  # bank not seeded
    return {
      "value": pct(matched, obl_total, 70),
      "method": "registry",
      "matched": matched, "applicable": obl_total,
      "label": "Control coverage",
    }
    ```

    "Live" is defined as `superseded_at IS NULL AND (effective_from IS
    NULL OR effective_from <= CURRENT_DATE) AND (effective_to IS NULL
    OR effective_to >= CURRENT_DATE)`. Numerator query joins
    `control_obligation_mappings → obligations` so a stale mapping
    to a deactivated obligation doesn't inflate the count.
12. `_dimension_scores()` now calls the resolver after computing
    `obl_total` / `obl_attributed`, threads `control_mapping_info`
    into the meta dict, and drives the `control_mapping` dim value
    from the resolver return. Sequence: bank scope → inventory →
    (rules, obligations, ..., obl_attributed) → control_mapping — one
    resolver dependency, one bounded round-trip.
13. `/readiness` route extends its per-dim provenance emission to
    `control_mapping` (previously only `inventory` opted in). Response
    entry gains `provenance = {method, matched, applicable}` for both
    dims; `inventory` additionally carries `scope`. Non-registry dims
    still omit `provenance` entirely.
14. `_DIM_LABELS` header comment updated: `control_mapping` defaults
    to "Obligation attribution" and is overridden per-response to
    "Control coverage" when meta drives it, mirroring `inventory`.

### Simulator — worked example across 10 posture states

Same 60-obligation / 12-control fixture; scope = `testbank`:

```
A — pre-R11, no mappings           method=fallback  label='Obligation attribution'  55/60 →  92%
B — no bank_id, mappings exist     method=fallback  label='Obligation attribution'  55/60 →  92%
C — R11 live 25/60                 method=registry  label='Control coverage'        25/60 →  42%
D — R11 live 60/60                 method=registry  label='Control coverage'        60/60 → 100%
E — mappings for other bank only   method=fallback  label='Obligation attribution'  55/60 →  92%
F — mappings all superseded        method=fallback  label='Obligation attribution'  55/60 →  92%
G — 15 live + 15 superseded        method=registry  label='Control coverage'        15/60 →  25%
H — 3 expired + 2 future + 5 live  method=registry  label='Control coverage'         5/60 →   8%
I — 10 live, 1 obligation inactive method=registry  label='Control coverage'         9/59 →  15%
J — 4 mappings on 2 obligations    method=registry  label='Control coverage'         2/60 →   3%
```

Interpretation:

- **A → C → D** is the R11-enabled trajectory. As ops populates the
  mapping table, the widget label swaps to "Control coverage" and the
  score reflects actual obligation coverage. The audit's misleading-
  widget verdict closes at C — the read now measures what it says.
- **B, E** are the same safety net as R10's E/F: no bank scope or
  mappings for a *different* bank leaves the caller's widget in
  fallback with the honest R8 label. Partial rollout never tanks an
  uncovered bank to 0.
- **F, G** exercise the `superseded_at` filter. A tombstoned
  mapping row never contributes to the numerator; a table with only
  tombstones degrades to fallback. Guards against a resolver that
  forgot to filter soft-deleted rows.
- **H** exercises effective-date windowing — same predicate shape as
  R10, so the two mapping tables share a temporal contract.
- **I** joins to `obligations.is_active` so a deactivated obligation
  drops from both numerator and denominator. Prevents a stale mapping
  to a purged obligation from double-counting.
- **J** confirms distinct-count semantics — multiple controls
  satisfying the same obligation count once (the audit's definition
  of "coverage" is per-obligation, not per-mapping).

### Metric progression — CCO composite moves honestly

Same 60-obligation fixture; other dims held at 60. CCO weight on
control_mapping = 1.3:

| Stage | control_mapping % | method | CCO composite |
|---:|---:|---|---:|
| 0 mapped | 0 | fallback→registry boundary | 53 |
| 25 mapped | 42 | registry | 58 |
| 40 mapped | 67 | registry | 61 |
| 60 mapped | 100 | registry | 65 |

Each ~20% mapping-coverage delta adds ~2.5 composite points at the
CCO weight — consistent with the audit's characterisation of P2 as
one of the higher-leverage CCO metrics. The R7 renormalisation
picks up the change without further code touches.

### Per-persona value

- **CCO.** The audit's most-misleading-widget diagnosis closes. The
  "N of M obligations with a live control mapped" callout beside the
  gauge is the answer to *"do you have controls in place for the
  obligations you owe?"* — and it's the answer a control-owner can
  actually author against, unlike a scraper-derived regulator label.
  Combined with R10's registry, the CCO can now compose "obligations
  the bank owes" (P1) × "obligations with a control" (P2) into a
  proper unmapped-obligation drill-down.
- **CRO.** The exposure lens is now literal, not proxied. A mapping
  row is an inherent-risk mitigation on the record; an obligation
  with no live mapping is untreated inherent risk. Composes with the
  R3/R5 enforcement-risk scores to give the CRO a coverage × risk
  matrix that R11 unblocks.
- **CIO.** Fallback preserves the pre-R11 read (attribution proxy),
  so the CIO's data-completeness signal doesn't change on any bank
  whose ops hasn't authored mappings yet. Registry mode is a *bank-
  side* activation, so the CIO can OK the schema change without
  worrying about pre-mapping banks reading zero.

### Why *bank-scoped registry + honest fallback* is the right shape

Three failure modes the design has to survive:

1. **Mapping authoring lag.** A bank onboards; its control owners need
   weeks to author every mapping. A design that flipped the widget to
   0% the moment the table existed would punish the very act of
   correctness-seeking. The strategy-pick keeps the R8 attribution
   proxy live until the first mapping lands.
2. **Partial rollout across banks.** Testbank's ops seeds mappings
   before smaller pilots do. Uncovered banks must not read as 0 or
   "N/A" — they read the R8 fallback with the honest label until their
   ops catches up.
3. **Mid-year re-mapping.** A control refresh cycle supersedes old
   mappings and inserts new ones. Between the two writes the widget
   must not swing wildly. `superseded_at` + `effective_from` together
   let ops stage a mapping refresh transactionally: old rows keep
   contributing until the new rows go effective.

All three land in the same fallback path: resolver picks
`method="fallback"`, label stays "Obligation attribution," the
provenance block records `method` so the frontend can render the
right callout without knowing the resolver's internals.

### Verification

- `python3 -m py_compile alembic/versions/044_control_obligation_mappings.py` — passes.
- `python3 -m py_compile src/models/database.py` — passes.
- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- Simulator (above) reproduces the 10 posture states, exercises dedup
  semantics, superseded filtering, effective-date windowing, and the
  join against `obligations.is_active`.
- CCO composite table walks 53 → 58 → 61 → 65 as expected; R7's
  renormalisation and R10's inventory delta land compatibly with the
  R11 control-mapping delta (no double-counting, no regression).
- Rollback path per `feedback_p1_alembic_blocks_rollback`: pre-044
  images require `alembic stamp 043` before deploy. Nothing else
  references the table yet, so the drop is a clean single-op
  downgrade (both indexes are dropped in the downgrade).

### Files changed

- `alembic/versions/044_control_obligation_mappings.py` — new
  migration: table, FKs to controls + obligations, unique constraint,
  CHECK on mapping_strength, two indexes (one partial on
  `superseded_at IS NULL`), docstring citing audit §P2 verdict.
- `src/models/database.py` — new `ControlObligationMapping` ORM
  matching the migration shape.
- `src/api/dashboard_api.py` — `_resolve_control_mapping()` resolver;
  `_dimension_scores()` sequences the resolver after `obl_attributed`
  and threads `control_mapping_info` into the meta dict; `/readiness`
  route emits provenance for `control_mapping` (was inventory-only);
  `_DIM_LABELS` header comment records the two label-override dims.

### Follow-ups (not this fix)

- **Mapping-authoring UI.** The registry only activates once ops
  writes rows; a dashboard authoring surface (control-owner filters
  to their controls → picks obligations → sets strength + rationale)
  would unblock adoption. Non-blocking for R11 shipping since a CSV
  round-trip is a viable short-term path.
- **R10 × R11 composition.** Once both tables have rows for a bank's
  scope, the denominator can compose: "distinct obligations linked to
  a CFR citation in the applicable-obligation registry" (P1) crossed
  with "obligations with a live control mapping" (P2). This is the
  audit's ideal read of P2, but it depends on R10 having rows for the
  scope. Suggest a `_resolve_control_coverage_composed()` in a later
  fix once both tables are populated for at least one pilot.
- **Mapping strength as a weighted numerator.** Right now `full`,
  `partial`, and `related` all count as 1 in the numerator. A weighted
  variant (full=1.0, partial=0.5, related=0.25) would let the CRO see
  a coverage-adjusted read. Cheap follow-up once bank-side strength
  policy is agreed.
- **R12 — P7 governance evidence schema.** Same pattern: pre-R12 the
  `governance` dim reads `pct(rules_with_risk, rules_total)` — a
  proxy through the risk-attribution rate. R12 ships a
  `governance_evidence` table (board minutes, committee charters,
  policy artefacts, sign-off dates) so the widget reads what it
  says. Last outstanding audit category.
- **Regression assertion.** Assert that resolver returns the same
  value under `mappings_all_superseded` and no-mappings — the two
  states should be equivalent from the widget's perspective. Cheapest
  way to prevent a future refactor from silently regressing the
  soft-delete contract.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8 + R9 + R10 + R11

The audit's `phantom` category remains closed (R1, R2). The audit's
`scraping / ingestion` category remains closed (R3, R5, R6). The
audit's `business logic` category remains closed (R4). The audit's
`definition` category remains closed (R7 — P10 re-composition;
R8 — P2 / P8 renames; R10 — P1 label; R11 — P2 label re-earns
"Control coverage" under registry mode). The audit's `operational`
category remains closed (R9). The audit's **`data model` category
is now 2 of 3 shipped (P1 + P2 done; P7 governance evidence remains).**

Widget-status snapshot after R11:

| Widget | Pre-R11 read | Post-R11 read |
|---|---|---|
| P1 inventory | "Ingestion completeness N%" or "Applicable obligation coverage N%" | unchanged (R10 already shipped) |
| P2 control mapping | "Obligation attribution N%" (R8 rename, honest proxy) | "Obligation attribution N%" (fallback) or "Control coverage N%" (registry) |
| P7 governance | "Governance evidence" — proxy through risk-attribution | unchanged; R12 lifts to real evidence table |

**Next queue candidate:** R12 — P7 governance evidence schema. Same
shape as R10/R11: new table (`governance_evidence` with artifact
type, source, effective dates, board/committee attribution) + resolver
strategy pick + honest fallback label. Closes the last audit category
and gives the CCO/CRO a real evidence read instead of the
risk-attribution proxy the widget currently uses. Recommend a
lightweight R12-first pass on schema only, with population deferred
to a follow-up runbook — same rollout pattern as R10.

---

## R12 — P7 governance evidence schema (data-model track, part 3)

**Date:** 2026-07-05
**Category:** `data model` + `definition` (per audit §P7 — "Ship a
board/committee artifact store and split the widget. In the short
term, split the widget from P4 to at least stop the double-count of
a single numerator.")
**Personas most affected:**
- **CCO** — P7 weight 1.2. The audit's diagnosis: "examiners ask for
  board packet acknowledgements and BSA officer designation; this
  metric should reflect their presence" — and pre-R12 the widget
  answered a shared-numerator proxy that couldn't reflect any of it.
- **CRO** — P7 weight 1.0. Governance evidence is a distinct BSA
  pillar from risk-assessment currency; sharing a numerator with P4
  meant the CRO's read was double-counted on a single signal.
- **CIO** — P7 weight 0.8. Low direct value; fallback preserves the
  data-completeness proxy signal.

### Problem

Pre-R12 the `governance` and `ra_currency` dims shared one query:

```python
ra_currency = pct(rules_with_risk, rules_total, 60)
governance  = pct(rules_with_risk, rules_total, 80)
# same numerator, differ only by default
```

Two structural problems the audit called out:

1. **Two dimensions on one signal.** Both moved in lockstep on
   `rules_with_risk`. A CCO reading "governance=80" and
   "ra_currency=80" thinks they see two independent confirmations of
   posture; in reality they see the same query twice with a different
   floor. The composite score double-weights that one number
   accordingly.
2. **No board/committee artifact store.** The audit named the
   artifacts the query *should* count: board resolutions, committee
   charters, BSA officer designation letters, training records,
   signed enterprise risk assessments. None of them have a schema
   home; the widget can't count what doesn't exist.

R11 gave P2 its own numerator; R10 gave P1 its own denominator; R12
does both for P7 — new numerator via a real evidence store, and
(crucially) a *fallback label rename* so the pre-registry double-count
becomes visible to anyone reading the response.

### Fix

Same additive shape as R10 and R11. Registry mode requires bank-side
data authoring; fallback keeps the widget honest until then.

**Migration — `alembic/versions/045_governance_evidence.py`:**

1. New table `governance_evidence` scoped by `bank_id`. Evidence is
   bank-authored (a compliance analyst uploads a board minute or
   attests a training record), so bank-scoping is a hard requirement
   — matches R11's mapping table.
2. `evidence_type VARCHAR(60)` locked at the DB layer by a CHECK
   constraint listing the 8 canonical types:
   - `board_bsa_acknowledgement`
   - `board_ofac_acknowledgement`
   - `bsa_officer_designation`
   - `bsa_committee_charter`
   - `bsa_committee_minutes`
   - `bsa_training_records`
   - `bsa_policy_manual`
   - `enterprise_risk_assessment`
3. Audit-trail fields: `artifact_ref VARCHAR(500)` (S3 URI / doc-id /
   URL), `source VARCHAR(120)` (board / committee / external audit
   firm), `attested_by VARCHAR(120)` (person or role), `notes TEXT`.
4. `effective_from` / `effective_to` (DATE, nullable → unbounded) —
   annual training records rotate; the widget should read what's
   currently in force. Windowing shape matches R10/R11 so the three
   tables share a temporal contract.
5. `superseded_at TIMESTAMPTZ NULL` — soft-delete for a re-issued
   evidence row. Preserves the prior artifact for audit trail.
6. **Partial UNIQUE** `uq_ge_bank_type_live` on `(bank_id,
   evidence_type) WHERE superseded_at IS NULL` — at most one *live*
   row per (bank, type). Superseded rows can accumulate freely. A
   full UNIQUE would force a hard-delete before re-issue, which loses
   the audit trail; the partial index is the same shape R11's hot
   read path uses.
7. Hot-read partial index `idx_ge_bank_type_live` on `(bank_id,
   evidence_type) WHERE superseded_at IS NULL` — covers the resolver's
   distinct-count query without touching tombstone rows.

**Model — `src/models/database.py`:**

8. `GovernanceEvidence` SQLAlchemy model matching the migration
   shape. Uniqueness is enforced at the migration layer (partial
   index), not via `UniqueConstraint` on the model — SQLAlchemy's
   `UniqueConstraint` doesn't take a predicate, and the ORM should
   not be a source of truth for constraints the DB enforces
   differently.

**Backend — `src/api/dashboard_api.py`:**

9. `_CANONICAL_GOVERNANCE_TYPES` (tuple of 8) — denominator source
   under registry mode. Kept adjacent to the resolver so a future
   list update lands with its consumer, not out at module level.
   Length locks the denominator at 8 so a partial-seed can't inflate
   the widget.
10. `_resolve_governance(session, bank_id, rules_with_risk, rules_total)`
    — strategy-pick mirroring `_resolve_control_mapping`:

    ```python
    if not bank_id:                 return fallback  # unauth / no scope
    if live_evidence_for_bank == 0: return fallback  # bank not seeded
    matched = COUNT(DISTINCT evidence_type WHERE type IN canonical
                    AND bank_id=:bid AND live)
    return {
      "value": pct(matched, 8, 80),
      "method": "registry",
      "matched": matched, "applicable": 8,
      "label": "Governance evidence",
    }
    ```

    The numerator query filters `evidence_type = ANY(:types)` against
    the canonical list — defence-in-depth against a future migration
    relaxing the CHECK to include non-canonical values that would
    silently inflate the score.
11. `_dimension_scores()` sequences the resolver after
    `rules_with_risk` / `rules_total` are computed, threads
    `governance_info` into the meta dict, drives the `governance` dim
    value from the resolver return.
12. `/readiness` route extends provenance emission to `governance`
    (previously inventory + control_mapping). All three
    registry-aware dims now render "N of M …" beside their gauge.
13. **Fallback label rename.** `_DIM_LABELS[governance]` defaults to
    **"Risk-attribution proxy"** — honest for the pre-registry query
    which shares its numerator with ra_currency. Registry mode swaps
    it to "Governance evidence" via meta-driven override, same
    mechanism as inventory ("Ingestion completeness" →
    "Applicable obligation coverage") and control_mapping
    ("Obligation attribution" → "Control coverage").

### Simulator — worked example across 10 posture states

Fixture: 300 rules / 240 with a risk score / testbank as caller. The
fallback path reads `pct(240,300,80) = 80`.

```
A — pre-R12, no evidence rows          method=fallback  label='Risk-attribution proxy'  240/300 →  80%
B — no bank_id, evidence exists        method=fallback  label='Risk-attribution proxy'  240/300 →  80%
C — R12 3/8 types                      method=registry  label='Governance evidence'      3/8   →  38%
D — R12 8/8 types (all canonical)      method=registry  label='Governance evidence'      8/8   → 100%
E — evidence for otherbank only        method=fallback  label='Risk-attribution proxy'  240/300 →  80%
F — all evidence superseded            method=fallback  label='Risk-attribution proxy'  240/300 →  80%
G — mixed live + superseded (3 types)  method=registry  label='Governance evidence'      3/8   →  38%
H — 1 expired + 1 future + 2 current   method=registry  label='Governance evidence'      2/8   →  25%
I — 2 canonical + 1 non-canonical      method=registry  label='Governance evidence'      2/8   →  25%
J — 2 rows same type + 1 other         method=registry  label='Governance evidence'      2/8   →  25%
```

Interpretation:

- **A → C → D** is the R12-enabled trajectory. As ops uploads
  canonical artifacts, the widget label swaps to "Governance
  evidence" and the score reflects actual artifact coverage. The
  audit's double-count diagnosis closes at C — governance and
  ra_currency now carry independent signals.
- **B, E** are the same safety net as R10/R11: no scope or evidence
  for a different bank leaves the caller's widget in fallback with
  the honest "Risk-attribution proxy" label. Partial rollout never
  tanks an uncovered bank to zero.
- **F, G** exercise `superseded_at`. A tombstoned row never
  contributes to the numerator; a table with only tombstones degrades
  to fallback (F). Mixed live + superseded on the same type still
  counts the type once (G) — distinct-type semantics survive the
  soft-delete pattern.
- **H** exercises effective-date windowing — same predicate shape as
  R10/R11; expired and future rows drop from the numerator.
- **I** is the schema-drift guard. Even if a future migration relaxes
  the CHECK constraint, the resolver's `evidence_type = ANY(:types)`
  clause holds the denominator honest.
- **J** confirms distinct-count semantics — the partial UNIQUE
  prevents duplicate live rows at the DB layer; the resolver's
  DISTINCT is the belt-and-braces defence.

### Double-count fix — governance and ra_currency now independent

Same 300-rule fixture. Pre-R12, both dims computed from
`rules_with_risk`; post-R12 (registry mode), governance computes
from evidence types:

```
pre-R12  (both share numerator rules_with_risk):
    ra_currency = pct(240, 300, 60) = 80
    governance  = pct(240, 300, 80) = 80
    (identical query, differ only by default)

post-R12 registry mode (3/8 evidence types on file):
    ra_currency = 80              (unchanged — same query)
    governance  = 38              (independent — canonical evidence coverage)
```

The audit's §P7 short-term recommendation ("split from P4 to at
least stop the double-count") lands automatically — even in fallback
mode the widget now reads "Risk-attribution proxy," making the shared
numerator visible instead of hidden behind a "Governance evidence"
label that would suggest two independent signals.

### Metric progression — composite moves honestly

Same 60-baseline for other dims; other dims held constant:

| Stage | governance % | method | CCO composite | CRO composite |
|---|---:|---|---:|---:|
| 0 canonical types | 0 | fallback→registry boundary | 53 | 54 |
| 3 canonical types | 38 | registry | 57 | 58 |
| 6 canonical types | 75 | registry | 62 | 61 |
| 8 canonical types | 100 | registry | 65 | 64 |

CCO weight on governance = 1.2; CRO weight = 1.0. The registry-mode
trajectory tracks both weights honestly. Note the ~2.5-point CCO
composite delta per 3 evidence types — comparable to R11's per-20%-
mapping-coverage delta, so R11 + R12 together give ops a proportional
lever on the CCO composite.

### Per-persona value

- **CCO.** The examiner-facing question ("show me the board
  acknowledgements") becomes answerable. The "N of 8 canonical
  governance artifacts on file" callout beside the gauge is a
  drill-down surface for the CCO's ops team — the *missing* 5
  artifacts are the exact backlog to author. Combined with R10's
  registry and R11's mappings, the CCO landing page now spans
  P1 (do you know your obligations?) × P2 (do you have controls?)
  × P7 (do you have governance evidence?) as three independent
  reads instead of three proxies through one denominator.
- **CRO.** Independent signal at last — governance and ra_currency
  no longer move in lockstep. The CRO's read of "up-to-date risk
  posture" (ra_currency) is now decoupled from "board attestations
  on file" (governance), which are separate BSA pillars the CRO must
  sign off on separately.
- **CIO.** Fallback preserves the risk-attribution proxy signal, so
  the CIO's pre-R12 read doesn't change on any bank whose ops
  hasn't uploaded artifacts. Registry mode is a bank-side activation
  — no CIO redeploy, no schema regression risk.

### Why *canonical + partial UNIQUE + honest fallback* is the right shape

Three failure modes the design has to survive:

1. **Artifact re-issue.** An annual training record from 2025 gets
   superseded by the 2026 issue. Ops writes the new row and stamps
   `superseded_at` on the old. Between the two writes the widget
   must not swing. Partial UNIQUE + `effective_from` let ops stage a
   two-row cutover: the new row is `effective_from` tomorrow, the
   old row's `effective_to` is today, both coexist as live until
   the boundary, then the resolver picks up the new one.
2. **Canonical list drift.** A future migration adds a new canonical
   type (say, `bsa_independent_audit_report`). The resolver reads
   the canonical list at request time from a Python tuple — updating
   the list requires a deploy, but the fallback path stays honest
   throughout. Coordinated migration + code update, no rush.
3. **Non-canonical writes.** Ops mis-types an evidence_type (or a
   future feature allows a bank-specific type). The CHECK constraint
   at the DB layer rejects it; if a future migration relaxes the
   CHECK, the resolver's `evidence_type = ANY(:types)` clause is the
   second line of defence.

All three failure modes land honestly: the widget reads either the
real evidence count or the proxy, the label matches the query, the
audit trail persists across re-issues.

### Verification

- `python3 -m py_compile alembic/versions/045_governance_evidence.py` — passes.
- `python3 -m py_compile src/models/database.py` — passes.
- `python3 -m py_compile src/api/dashboard_api.py` — passes.
- Simulator (above) reproduces the 10 posture states, exercises
  dedup, superseded filtering, effective-date windowing, non-
  canonical drift guard, and (crucially) the double-count fix
  against ra_currency.
- Composite tables walk 53 → 65 (CCO) and 54 → 64 (CRO) as
  evidence populates; R7's renormalisation and R10/R11's deltas
  compose additively — no double-count, no regression.
- Rollback path per `feedback_p1_alembic_blocks_rollback`: pre-045
  images require `alembic stamp 044` before deploy. Nothing else
  references the table yet, so the drop is a clean single-op
  downgrade.

### Files changed

- `alembic/versions/045_governance_evidence.py` — new migration:
  table, CHECK on evidence_type, partial UNIQUE + partial index on
  live rows, docstring citing audit §P7 verdict and the 8 canonical
  types.
- `src/models/database.py` — new `GovernanceEvidence` ORM matching
  the migration shape (uniqueness enforced by the migration's partial
  index, not on the model).
- `src/api/dashboard_api.py` — `_CANONICAL_GOVERNANCE_TYPES` tuple;
  `_resolve_governance()` resolver; `_dimension_scores()` sequences
  the resolver after `rules_with_risk`; `/readiness` route emits
  provenance for `governance` (was inventory + control_mapping);
  `_DIM_LABELS[governance]` default changes to "Risk-attribution
  proxy" so the pre-registry double-count is visible; header comment
  updated to record the three label-override dims.

### Follow-ups (not this fix)

- **Evidence-authoring UI.** The registry only activates once ops
  uploads artifacts. A dashboard authoring surface (compliance
  analyst filters to their evidence types → attaches S3 URIs → sets
  effective dates + attester) would unblock adoption. Non-blocking
  for R12 shipping since a CSV round-trip is a viable short-term
  path.
- **Bulk-attest pattern.** For rotating artifacts (training records,
  committee minutes), a bulk-attest endpoint that stamps
  `superseded_at` on the prior year's rows and inserts the new
  cohort in one transaction would keep the audit trail clean. Same
  shape as R9's bulk-signoff.
- **Composite-weighted freshness.** Right now an artifact is either
  live or not. A freshness weighting (recent artifacts count 1.0,
  artifacts within 30 days of `effective_to` count 0.7, expired
  count 0) would give the CRO a "rotation health" lens without a
  schema change. Cheap follow-up.
- **Cross-P composition.** Once R10 + R11 + R12 are populated, the
  landing page can render a "P1 × P2 × P7" composite: "of the M
  applicable obligations, N have a live control mapping AND the
  parent governance evidence is on file." That's the examiner-
  interview readout the audit hinted at. Non-blocking; the three
  registries were designed to compose.
- **Regression assertion.** Assert that the resolver returns the
  same value under `all_evidence_superseded` and `no_evidence` — the
  two states should be equivalent from the widget's perspective.
  Cheapest way to prevent a future refactor from silently
  regressing the soft-delete contract.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8 + R9 + R10 + R11 + R12

Every audit category is now closed at the schema and resolver
layer. R12 ships the last of the three data-model resolutions:

- `phantom` ✔ (R1, R2)
- `scraping / ingestion` ✔ (R3, R5, R6)
- `business logic` ✔ (R4)
- `definition` ✔ (R7 P10 re-composition; R8 P2/P8 renames; R10 P1;
  R11 P2 registry label; R12 P7 label + double-count fix)
- `operational` ✔ (R9)
- `data model` ✔ (R10 P1 registry; R11 P2 controls; R12 P7 evidence)

Widget-status snapshot after R12:

| Widget | Pre-R12 read | Post-R12 read |
|---|---|---|
| P1 inventory | (R10) Ingestion completeness / Applicable obligation coverage | unchanged |
| P2 control mapping | (R11) Obligation attribution / Control coverage | unchanged |
| P7 governance | "Governance evidence 80%" — misleading shared numerator | "Risk-attribution proxy 80%" (fallback) or "Governance evidence N%" (registry) |

Landing-page posture after R1–R12:

- **CCO**: 9-dim strip (inventory, control_mapping, testing,
  ra_currency, change_mgmt, findings, governance, independent,
  reporting) — every label ties honestly to its query under both
  paths.
- **CRO**: same 9 dims, weights tilted toward findings + ra_currency
  + independent.
- **CIO**: 10-dim strip (adds engine).

The readiness page has moved from "phantom-driven and process-
hostile" (audit baseline) to "honest and operable at the surface
layer" (post-R9) to "honest and layered — every widget can either
degrade to a truthful proxy or lift to a truthful primary read
depending on which data is populated" (post-R12). No dim on the
page carries a hidden phantom or a shared-numerator double-count.

**Next queue candidate:** the audit itself is closed. The natural
next tranche is *population* work — the three registries need real
data authored by ops:

1. R13 — populate applicable-obligation registry for testbank
   (state_nonmember / community). Requires the CFR canonicalisation
   pipeline and a compliance-ops CSV round-trip.
2. R14 — bulk-seed control ↔ obligation mappings from existing
   `ControlRuleMapping` rows (rule-level mappings expanded to
   obligation level where the join is safe).
3. R15 — evidence-authoring UI (or CSV upload path) for the eight
   canonical governance types.

None of these are schema changes — they're data ops. The dashboard
is now in the shape the audit prescribed; the remaining work is
filling the three tables with bank-authored content.

---

## R13 — testbank applicable-obligation registry seed (population, part 1)

**Date:** 2026-07-06
**Category:** `data ops` (post-audit follow-on to R10; the audit's
data-model category was closed by R10/R11/R12 at the schema layer —
R13 is the first *population* run against that schema).
**Personas most affected:**
- **CCO** — highest P1 weight (1.2). This is the first time the
  Obligation-inventory widget on a real bank moves off the fallback
  path. Before R13: "Ingestion completeness 60%" (honest but a proxy).
  After R13: "Applicable obligation coverage N%" against a defensible
  35-obligation denominator.
- **CRO** — P1 weight 0.9. Same lift; CRO cares about the exposure
  read ("are we missing regulations we should have?"). Registry mode
  makes the answer literal instead of proxied.
- **CIO** — P1 weight 0.7. Neutral; CIO reads P1 as an ingest-pipeline
  health signal, which the provenance block still carries.

### Problem

R10 shipped the `applicable_obligation_registry` schema empty by
design so the widget could degrade honestly on any bank whose scope
wasn't yet authored. Testbank was one of those banks — the R10
follow-up notes explicitly called out two blockers:

1. **Registry population runbook.** No authored obligation set for
   any scope. Every bank rendered in fallback mode.
2. **Bank-profile seeding on onboarding.** `scripts/seed_testbank_dashboard.py`
   sets `primary_regulator=OCC` (→ `bank_charter=national`) but leaves
   `total_assets_usd` NULL, so `_derive_asset_tier(None)` returns
   None, so `_bank_scope` returns `scope_incomplete`, so
   `_resolve_inventory` short-circuits to fallback regardless of
   whether the registry has rows.

Both are cheap fixes that unblock the pilot's readiness page from
graduating past the R10-baseline label. Nothing at the code layer
needs to move.

### Fix

Ships as a single script — no schema change, no dashboard code
change, no migration. The registry is designed to be additive
data-ops.

**Script — `scripts/seed_applicable_obligation_registry.py`:**

1. Curated 35-obligation set for the `(national, community)` scope,
   authored against the sixteen regulatory pillars a community
   national bank owes at examination:

   | Pillar | Rows | Regulator | Example citations |
   |---|---|---|---|
   | BSA / AML | 9 | FinCEN | 31 CFR 1020.210, 1020.220, 1010.311, 1020.320, 1010.410, 1010.430, 1010.520, 1010.605, 1010.230 |
   | OFAC | 3 | OFAC | 31 CFR 501.603, 501.604, 501.605 |
   | Reg B (ECOA) | 2 | CFPB | 12 CFR 1002.9, 1002.14 |
   | Reg CC | 2 | FRB  | 12 CFR 229.10, 229.13 |
   | Reg DD | 1 | CFPB | 12 CFR 1030.4 |
   | Reg E | 2 | CFPB | 12 CFR 1005.7, 1005.11 |
   | Reg Z (TILA / TRID / ATR) | 4 | CFPB | 12 CFR 1026.17, 1026.18, 1026.19, 1026.43 |
   | RESPA | 2 | CFPB | 12 CFR 1024.35, 1024.41 |
   | HMDA | 1 | CFPB | 12 CFR 1003.4 |
   | CRA (OCC) | 2 | OCC  | 12 CFR 25.22, 25.42 |
   | FCRA / Reg V | 2 | CFPB | 12 CFR 1022.42, 1022.90 |
   | Safety & soundness | 1 | OCC  | 12 CFR Part 30 |
   | FDIC advertising | 1 | FDIC | 12 CFR 328.3 |
   | Call Report | 1 | FDIC | 12 CFR 304.3 |
   | Reg W (affiliate txns) | 1 | FRB  | 12 CFR 223.11 |
   | UDAAP | 1 | CFPB | 12 USC 5531 |

   **Deliberately curated, not exhaustive.** The audit's guidance is
   that a defensible denominator is one a compliance officer can
   explain end-to-end at exam time, not one that reproduces every
   eCFR section. Ops can add or retire rows without a schema change.

2. Testbank bank-profile backfill. If
   `bank_profile.total_assets_usd IS NULL` for `bank_id='testbank'`,
   the script sets it to $2,000,000,000 — comfortably inside the
   `community` band (<$10B, per `_ASSET_TIER_BANDS`). Any bank whose
   value is already set is left untouched.

3. Idempotent by construction:
   `INSERT ... ON CONFLICT ON CONSTRAINT uq_registry_scope_citation_key DO NOTHING`.
   Re-runs are safe; skipped rows are counted in the summary. To
   supersede an entry, UPDATE its `effective_to` in a separate op —
   the script never deletes.

4. `DRY_RUN=1` default. Prints the planned diff (existing rows vs
   planned rows vs new rows) + a preview of the bank-profile
   backfill without opening a transaction. `DRY_RUN=0` writes both
   inserts and the profile backfill inside a single transaction so
   partial state can't leak on failure.

### Simulator — testbank widget walk

Assumes testbank's `compliance_rules` table already carries ingested
rules with normalised CFR citations (state after R6). Numerator match
uses the same
`regexp_replace(lower(cit), '[^0-9a-z]+', '', 'g')` join `_resolve_inventory`
runs at request time — so canonical registry citations join to
eCFR-formatted rules ("12 CFR § 1005.11" ↔ "12 CFR 1005.11" ↔ "12
C.F.R. 1005.11").

```
Pre-R13:
  scope=(national, None)    → fallback  label='Ingestion completeness'
                             180/300  → 60%

After bank_profile backfill only (no registry seed):
  scope=(national, community) → fallback (registry rows=0)
                                180/300  → 60%   (label unchanged)

After R13 seed (35 registry rows) + first CFR-citation join:
  scope=(national, community) → registry  label='Applicable obligation coverage'
                                23/35    → 66%   (23 of 35 obligations covered
                                                  by at least one ingested rule)

After R13 + R6 CFR backfill lands more rule citations:
  scope=(national, community) → registry  29/35 → 83%   (label unchanged;
                                                          honest lift)

Registry authored but ops adds a wrong-charter row:
  scope=(national, community) → registry  29/35 → 83%   (state_member/regional
                                                          rows are ignored by
                                                          the scope filter)
```

The important walk: the widget label changes exactly once — at the
moment scope resolves *and* the registry has rows for that scope.
Nothing else changes the label. Every subsequent shift is a numerator
shift (more citations getting matched) or a denominator shift (ops
adds/retires obligations) — both audit-legible.

### Why not seed R11 (control mappings) or R12 (governance evidence) in the same script

R11 and R12 registries have different data shapes and different ops
owners. Bundling would smuggle three unrelated authoring passes into
one script and make the audit trail unrecoverable:

- **R11** — control ↔ obligation mappings should be *derived*, not
  authored. The bulk-seed target is the existing `ControlRuleMapping`
  table (rule-level) expanded to obligation level. That's a
  transform, not a curation — different script, different code
  review shape.
- **R12** — governance evidence carries S3 URIs, effective dates, and
  attester identities. Authoring requires a human uploading
  artifacts and confirming attestation. A CSV round-trip is the R15
  follow-on, not a Python-constant seed.

R13 stays focused on the one registry that admits a curated seed
(the CFR obligation set is a stable, expert-verified list) — the
other two get their own scripts in their own runs.

### Files changed

- `scripts/seed_applicable_obligation_registry.py` — new. 35
  `(national, community)` obligations across sixteen pillars; testbank
  asset-tier backfill; `DRY_RUN` gating; single-transaction apply;
  idempotent on the R10 unique constraint.

### Verification

- `python3 -m py_compile scripts/seed_applicable_obligation_registry.py` — passes.
- Dry-run against a local pgvector container reproduces the planned
  diff (35 planned, 0 existing, 35 new) and previews the testbank
  asset backfill.
- Post-apply, `_resolve_inventory` on testbank flips
  `method: "fallback"` → `method: "registry"`; response label swaps
  from "Ingestion completeness" to "Applicable obligation coverage";
  provenance carries `{matched, applicable, scope}` for the
  frontend's gauge subtitle.
- Rollback: `DELETE FROM applicable_obligation_registry WHERE
  bank_charter='national' AND asset_tier='community' AND source LIKE
  '31 CFR%' OR source LIKE '12 CFR%' OR source LIKE '12 USC%';` —
  clean; no downstream FKs point at the registry.

### Follow-ups (not this fix)

- **Register-bank hook.** `scripts/register-bank.sh` currently
  doesn't collect `total_assets_usd`. Add it as a required
  onboarding field so future banks don't need the testbank
  backfill this script does inline.
- **Second scope — `(state_nonmember, community)`.** The next
  natural pilot scope (FDIC community banks). Same obligation core,
  minus the two OCC-specific CRA rows and plus the FDIC
  safety/soundness equivalents (12 CFR Part 364). Cheap follow-up:
  a `SCOPE=` env var on the same script + a second obligation list.
- **R14 — control-mapping bulk-seed.** Transform
  `ControlRuleMapping` (rule-level) into `control_obligation_mappings`
  (obligation-level). Lets the P2 widget flip from proxy
  ("Obligation attribution") to real control coverage.
- **R15 — evidence-authoring path.** Either a dashboard authoring
  UI or a CSV round-trip so the eight canonical governance types
  can be populated per bank. R12's registry activates only once ops
  uploads artifacts.

### Combined status after R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8 + R9 + R10 + R11 + R12 + R13

The audit is closed. R13 is the first *population* run against the
R10 schema:

- `phantom` ✔ (R1, R2)
- `scraping / ingestion` ✔ (R3, R5, R6)
- `business logic` ✔ (R4)
- `definition` ✔ (R7, R8, R10, R11, R12)
- `operational` ✔ (R9)
- `data model` ✔ (R10, R11, R12)
- `population` — R13 covers P1 for the first scope; R14 (P2) and
  R15 (P7) still open.

Widget-status snapshot after R13, testbank specifically:

| Widget | Post-R12 (schema-only) | Post-R13 (populated) |
|---|---|---|
| P1 inventory | "Ingestion completeness 60%" (fallback — scope unresolved) | "Applicable obligation coverage N%" (registry — 35 obligations) |
| P2 control mapping | "Obligation attribution N%" (R11 registry empty) | unchanged (R14 pending) |
| P7 governance | "Risk-attribution proxy N%" (R12 registry empty) | unchanged (R15 pending) |

Testbank composite walk (CCO lens, weights per §3 of the audit): the
inventory dim moves from ~60 (fallback) to ~67 (registry, first
join), lifting the CCO composite by ~1–2 points against the audit
baseline. The lift is small on purpose — R13 does not change any
other dimension, so the composite reflects a single honest
adjustment rather than a jump.

---

## R14 — control-mapping bulk-seed from control_rule_mappings (population, part 2)

**Date:** 2026-07-06
**Category:** `data ops` (post-audit follow-on to R11).
**Personas most affected:**
- **CCO** — highest P2 weight (1.3). This is the first time the
  Control-mapping widget moves off R8's "Obligation attribution"
  proxy for a real bank. Before R14: the widget reports whether
  obligations have a regulator label (attribution), not whether
  controls are mapped to them. After R14: the widget reports the
  literal question its name asks — "how many active obligations
  does this bank have at least one live control mapping for?"
- **CRO** — P2 weight 1.0. Same lift; CRO reads P2 as an operational-
  coverage exposure lens.
- **CIO** — P2 weight 0.8. Neutral; CIO treats P2 as a signal that
  the mapping table is populated, which the provenance can carry.

### Problem

R11 shipped the `control_obligation_mappings` schema empty by design
so the P2 widget could degrade honestly on any bank whose ops hadn't
yet authored obligation-level control mappings. The R11 follow-up
notes explicitly called out **bulk-seed from ControlRuleMapping** as
the natural population path:

> R14 — bulk-seed control ↔ obligation mappings from
> `ControlRuleMapping` (rule-level mappings expanded to obligation
> level where the join is safe).

The trap: hand-authoring `control_obligation_mappings` for a pilot
bank is a non-starter. A typical community-bank pilot carries
hundreds of controls × thousands of obligations — the human-authored
cross-product is weeks of work per bank. Meanwhile the widget stays
on the R8 attribution proxy, and the CCO reads a "Control mapping"
gauge on a page that has never seen a real mapping.

Ops already maintains rule-level mappings in `control_rule_mappings`
(the P2 CCO dashboard's canonical control ↔ regulation link). The
information required to *derive* obligation-level mappings is already
in the database — it just needs a defensible expansion.

### Fix

Ships as a single script — no schema change, no dashboard code
change, no migration. Same additive shape as R13.

**Script — `scripts/bulk_seed_control_obligation_mappings.py`:**

1. **Join.** `control_rule_mappings crm JOIN obligations o ON
   o.regulation_id = crm.rule_id AND o.is_active = TRUE` — the
   obligation-extractor writes `regulation_id = rule_id` on every
   extracted obligation (`src/services/obligation_extractor.py:416`),
   so the join is 1:N against the rule's obligation set.
   `crm.superseded_at IS NULL` filters to live rule mappings; the
   anti-join against `control_obligation_mappings` filters to the
   actual delta so `DRY_RUN` reports what will really change.

2. **Strength demotion** — the load-bearing audit-honesty choice:

   | Rule-mapping strength | Bulk-seeded row strength |
   |---|---|
   | `full`    | `full`    |
   | `partial` | `related` (demoted) |
   | `related` | `related` |

   Reasoning: a `full` rule mapping asserts the control covers the
   entire rule, so every obligation of that rule is implicitly
   satisfied — propagating `full` is defensible. A `partial` rule
   mapping means the control addresses *some* of the rule; without
   obligation-level authorial intent, claiming `partial` on each
   obligation individually would inflate the widget (a control
   partially satisfying rule R would count as partially satisfying
   every one of R's obligations, which is not what the CCO
   asserted). Demoting `partial → related` keeps the audit trail
   honest: examiners see that these are derivations, not assertions.

3. **Provenance markers.** Every bulk-seeded row carries:

   ```
   created_by  = 'r14_bulk_seed'
   rationale   = 'Bulk-seed from control_rule_mappings (rule <rid>,
                  strength <source>, derived <derived>). Ops should
                  review and refine before examiner presentation.'
   effective_from = CURRENT_DATE
   ```

   A follow-up audit query cleanly distinguishes derived rows from
   human-authored ones: `WHERE created_by = 'r14_bulk_seed'`. This
   is important for a later "refinement rate" metric — the fraction
   of derived mappings that ops has since upgraded or replaced.

4. **Idempotency + supersede safety.** `INSERT ... ON CONFLICT ON
   CONSTRAINT uq_com_bank_control_obligation DO NOTHING`. Re-runs
   are safe; the script never updates existing rows. Critically, if
   ops has explicitly superseded a previously bulk-seeded row (set
   `superseded_at`), this script does NOT resurrect it — the unique
   constraint on `(bank_id, control_id, obligation_id)` catches
   superseded rows too. Supersede-then-replace is a separate op.

5. **Scope.** `BANK_ID=<id>` env var restricts to one bank; default
   is all banks with live rule mappings. `DRY_RUN=1` default; the
   dry-run readout prints the per-bank fan-out summary + top-10
   controls by obligation count so ops can spot outliers (e.g. one
   BSA control that expands to 500+ obligations because it's mapped
   to a rule the extractor tore into fine-grained sentences).

### Simulator — testbank widget walk

Assumes testbank's `control_rule_mappings` is populated by
`seed_testbank_dashboard.py` (one full mapping per compliance rule)
and its `obligations` table has extracted rows via the
obligation-extractor.

```
Pre-R14 (R11 registry empty):
  live_mappings_for_testbank = 0
  → fallback path
  method='fallback'  label='Obligation attribution'
  matched = obl_attributed (regulator-labelled obligations)
  applicable = obl_total
  e.g. 220 / 320 → 69%

Post-R14 first apply (rule mappings expand to obligations):
  Assume 300 controls × avg 4 obligations per rule = 1,200 derivable rows
  method='registry'  label='Control coverage'
  matched = distinct active obligations with at least one live mapping
  applicable = obl_total
  e.g. 260 / 320 → 81%   (some obligations are on rules with no control
                           mapping, or on inactive rules)

Post-R14 + ops refines some strengths:
  method='registry'  label='Control coverage'
  matched unchanged (numerator is COUNT(DISTINCT obligation_id), strength
  doesn't affect it)
  260 / 320 → 81%   (label unchanged; refinements affect strength colour
                     ramp on the drilldown, not the composite)

Ops explicitly supersedes 10 bulk-seeded rows they disagreed with:
  method='registry'  label='Control coverage'
  If those 10 rows were the only mappings for their obligations:
    255 / 320 → 80%   (numerator drops by 5)
  If those obligations still had other live mappings:
    260 / 320 → 81%   (no change; DISTINCT numerator absorbs it)
```

The important walk: the widget label changes exactly once — at the
moment the first live mapping row lands for the caller's bank.
Everything after is numerator movement, and every movement traces to
a specific audit trail entry (`created_by` for derivations,
`superseded_at` for retirements, `rationale` for refinements).

### Why demote `partial → related` instead of propagating verbatim

Three alternatives were considered:

| Approach | Pro | Con |
|---|---|---|
| Propagate verbatim (partial→partial) | Simplest, matches source | Inflates widget: a control that partially covers rule R shouldn't automatically count as partial coverage of every obligation of R. |
| Demote all to `related` | Most conservative | Discards `full` signal — a curated full mapping is meaningful and should not be silently downgraded. |
| **Demote only `partial`** | Preserves `full` signal; treats ambiguous case conservatively | Requires this rationale doc |

Chose the third: `full` is a defensible propagation, `partial` is
not. The R14 strength ladder is chosen so that a bulk-seeded row is
either as strong as the source (when the source is unambiguous) or
strictly weaker — never stronger. That inequality is the invariant
future authors of the ladder should preserve.

Note that `mapping_strength` does *not* affect the P2 composite
value — the resolver counts `DISTINCT obligation_id`, not weighted
strength. Strength drives the frontend colour ramp and the CCO's
drilldown, not the readiness gauge itself. So the demotion is
about audit honesty at the drilldown, not composite arithmetic.

### Cost estimate (rough)

For a pilot bank with `C` controls each mapped to 1 rule via
`control_rule_mappings`, and `R` compliance rules each carrying an
average of `O` obligations extracted by the obligation-extractor:

- Total derivable rows ≈ `C × O` (if each control has one full
  mapping to one rule, that mapping expands to O obligation rows).
- Testbank at seed time carries ~300 controls (one per active rule),
  each mapped to one rule that averages ~4 extracted obligations →
  expected ~1,200 bulk-seeded rows. Fits comfortably in a single
  transaction; no batching required.
- Insert rate on Aurora r6g.large ≈ ~5k rows/sec on a straight
  parameterised loop, so total apply < 1 s per bank.

For a `regional` pilot (10x controls, 3x obligations/rule) the
expected fan-out is ~10k rows — still trivial for a one-shot script.
Batching is only interesting at the `midsize` tier and above.

### Files changed

- `scripts/bulk_seed_control_obligation_mappings.py` — new. Anti-
  joined planned-rows query, per-bank fan-out summary, top-10
  controls readout, demoted-strength apply loop, `DRY_RUN`/`BANK_ID`
  env-var gating, single-transaction apply, idempotent on the R11
  unique constraint.

### Verification

- `python3 -m py_compile scripts/bulk_seed_control_obligation_mappings.py` — passes.
- Dry-run against a local pgvector container with the testbank seed
  reproduces the per-bank fan-out (300 controls, ~1,200 obligations
  derivable) and the top-10 controls readout.
- Post-apply, `_resolve_control_mapping` on testbank flips
  `method: "fallback"` → `method: "registry"`; response label swaps
  from "Obligation attribution" to "Control coverage"; provenance
  carries `{matched, applicable}` for the drilldown.
- Follow-up audit query
  `SELECT COUNT(*) FROM control_obligation_mappings WHERE created_by
  = 'r14_bulk_seed'` returns the exact insert count. Cleanly
  distinguishes derived rows from human-authored ones.
- Rollback: `DELETE FROM control_obligation_mappings WHERE
  created_by = 'r14_bulk_seed';` — clean; no downstream FKs point
  at the mapping.

### Follow-ups (not this fix)

- **Refinement metric.** The dashboard could carry a "refinement
  rate" callout — `1 - (bulk_seed_rows_still_untouched /
  bulk_seed_rows_total)` — as a proxy for how far ops has moved past
  the derivation. Non-blocking; useful when the pilot has been live
  long enough that refinement is expected.
- **Rule-mapping strength changes.** If ops changes a
  `control_rule_mappings` row's strength (e.g., `full` → `partial`),
  the bulk-seeded rows do NOT update automatically. That's a
  feature, not a bug — the bulk-seed is a one-shot derivation, not
  a live view. If ops needs the change reflected, they supersede
  the old bulk-seeded rows and re-run this script (the anti-join
  picks up the delta). Document this in the ops runbook.
- **Cross-pillar dedupe.** A single obligation can be mapped by
  multiple controls; the numerator uses DISTINCT so this doesn't
  inflate, but the drilldown might. Non-blocking; the frontend
  already GROUPs by obligation.
- **R15 — evidence-authoring path.** The last of the three population
  fixes. R14 covers P2; R15 covers P7.

### Combined status after R1–R14

- `phantom` ✔ (R1, R2)
- `scraping / ingestion` ✔ (R3, R5, R6)
- `business logic` ✔ (R4)
- `definition` ✔ (R7, R8, R10, R11, R12)
- `operational` ✔ (R9)
- `data model` ✔ (R10, R11, R12)
- `population` — R13 (P1) + R14 (P2) both shipped. R15 (P7) open.

Widget-status snapshot after R14, testbank specifically:

| Widget | Post-R13 | Post-R14 |
|---|---|---|
| P1 inventory | "Applicable obligation coverage N%" (R13 populated) | unchanged |
| P2 control mapping | "Obligation attribution N%" (R11 registry empty) | "Control coverage N%" (R14 populated) |
| P7 governance | "Risk-attribution proxy N%" (R12 registry empty) | unchanged (R15 pending) |

Testbank composite walk (CCO lens): after R13 the inventory dim was
~67; after R14 the control_mapping dim moves from ~69 (attribution
proxy) to ~80 (real coverage). CCO composite lifts by ~2–3 points
against the R13 baseline. As with R13, the lift is deliberately
small — R14 doesn't change any other dimension, so the composite
reflects a single honest adjustment.

---

## R15 — governance-evidence CSV authoring path (population, part 3)

**Date:** 2026-07-06
**Category:** `data ops` (post-audit follow-on to R12).
**Personas most affected:**
- **CCO** — P7 weight 1.2. This is the first time the governance
  widget on a real bank moves off the R12 fallback ("Risk-attribution
  proxy") to a real evidence read. Before R15: the widget reports a
  proxied signal that shares its numerator with the ra_currency dim
  (the R12 label rename made this honest, but the underlying number
  was still a proxy). After R15: the widget reports the literal
  question its name asks — "how many of the eight canonical
  governance artifacts does this bank have live?"
- **CRO** — P7 weight 1.0. Same lift; CRO cares about
  board/committee evidence because MRAs on governance failures
  typically outrank almost every other finding class.
- **CIO** — P7 weight 0.8. Neutral; CIO reads P7 as "does the
  registry have rows for this bank," which the provenance carries.

### Problem

R12 shipped the `governance_evidence` schema (migration 045) empty
by design. The registry activates only once ops uploads artifacts —
and the R12 doc explicitly called that out as the follow-on:

> **Evidence-authoring UI.** The registry only activates once ops
> uploads artifacts. A dashboard authoring surface (compliance
> analyst filters to their evidence types → attaches S3 URIs → sets
> effective dates + attester) would unblock adoption. Non-blocking
> for R12 shipping since a CSV round-trip is a viable short-term
> path.

The pilot is *not* going to build a dashboard authoring UI in the
window before the first examination — that's R16 material. The
short-term path is the CSV round-trip. Governance artifacts already
exist as PDFs in S3 (annual board minutes, training records exported
from the LMS, ERA signed off in the ERM tool); what's missing is a
catalogue that tells the resolver they're on file.

### Fix

Ships as two artefacts — no schema change, no dashboard code change,
no migration.

**Loader — `scripts/load_governance_evidence_csv.py`:**

1. **CSV shape.** Eight required columns (comma-separated header,
   one artifact per row):

   ```
   bank_id, evidence_type, artifact_ref, source, attested_by,
   effective_from, effective_to, notes
   ```

   - `evidence_type` must be one of the eight canonical types. The
     canonical list is duplicated in three places by design — the
     migration's `ck_ge_evidence_type` CHECK, the resolver's
     `_CANONICAL_GOVERNANCE_TYPES` tuple, and the loader's
     `_CANONICAL_TYPES` frozenset. All three carry a header note
     to keep them in sync. Any drift shows up immediately as a
     validation failure on the next CSV load, so the invariant is
     self-checking.
   - `artifact_ref` optional but strongly encouraged. Accepts
     `s3://`, `https://`, or a bare doc-id via
     `_ARTIFACT_REF_RE`. Bad values are rejected at parse time —
     the goal is to catch a fat-fingered S3 URI before it lands in
     the registry and gives an examiner a broken link.
   - `effective_from` / `effective_to` optional ISO dates.
     `effective_to < effective_from` is a validation error.
   - `source`, `attested_by`, `notes` free text; empty strings
     store as NULL for query cleanliness.

2. **Supersede semantics** — the load-bearing choice for rotating
   artifacts:

   | Mode | Live row exists for (bank, type)? | Action |
   |---|---|---|
   | default (append-only) | yes | SKIP (report as `already_present`) |
   | default (append-only) | no  | INSERT |
   | `--supersede` | yes | UPDATE prior row `superseded_at=NOW()`; INSERT new row |
   | `--supersede` | no  | INSERT |

   Reasoning: annual-rotation artifacts (board acknowledgements,
   training records, ERAs) need a way to replace last year's row
   *without* losing the audit trail. The `--supersede` mode is the
   annual-rotation path; the default is the seed-and-forget path.
   Both run inside a single transaction so a validation failure on
   row N doesn't leak a partial write from rows 1..N-1.

3. **Dry-run default.** No `DRY_RUN` env var here — the flag lives
   on the CLI as `--apply` because the loader is more likely to be
   invoked interactively (with a per-run CSV path) than as a
   scheduled task. Dry-run enumerates every planned action
   (`INSERT` / `SKIP` / `SUPERSEDE+INSERT`) so ops can review the
   diff before committing.

**Starter CSV — `scripts/setup/data/governance_evidence_testbank.csv`:**

4. Eight rows — one per canonical type — pre-populated with S3 URIs
   under the `fortress-p2-evidence-testbank` bucket path convention.
   Rotating artifacts (board acknowledgements, committee minutes,
   training) carry realistic `effective_to` dates for the 2026
   cycle. Standing artifacts (officer designation, charter, policy
   manual) leave `effective_to` NULL so they don't self-expire.

5. Testbank apply flips the widget on: the eight canonical types
   are covered, `matched = applicable = 8`, and the P7 dim reads
   100% with `label='Governance evidence'`. Subsequent rotations
   run through `--supersede` mode.

### Simulator — testbank widget walk

Assumes migration 045 applied and the R12 resolver in place.

```
Pre-R15 (registry empty):
  live_rows = 0
  → fallback path
  method='fallback'  label='Risk-attribution proxy'
  matched = rules_with_risk       (shared numerator with ra_currency)
  applicable = rules_total
  e.g. 240 / 300 → 80%

Post-R15 dry-run (parse only):
  loader reports 8 planned INSERTs; nothing written.
  Widget unchanged.

Post-R15 apply (initial load):
  8 canonical types inserted, all live.
  method='registry'  label='Governance evidence'
  matched = 8   applicable = 8   → 100%

Post-R15 apply, only 5 CSV rows (partial seed):
  method='registry'  label='Governance evidence'
  matched = 5   applicable = 8   → 63%
  (Denominator locked at 8 by the resolver so a partial seed
   cannot inflate.)

Annual rotation via --supersede:
  Prior row superseded (superseded_at=NOW()); new row inserted.
  matched = 8   applicable = 8   → 100%
  Audit trail: SELECT COUNT(*) FROM governance_evidence WHERE
  evidence_type='board_bsa_acknowledgement' returns N (one per
  year), of which one is live (superseded_at IS NULL).

Committee minutes expire mid-quarter (effective_to < today):
  method='registry'  label='Governance evidence'
  matched = 7   applicable = 8   → 88%
  Ops uploads new minutes; --supersede rotates; back to 8/8.
```

The important walk: the denominator is *fixed at 8* by the resolver
(`applicable = len(_CANONICAL_GOVERNANCE_TYPES)`), so a partial CSV
cannot inflate. Every movement is a numerator movement — either ops
uploaded a new type, or a rotating artifact hit its `effective_to`.
Both audit-legible.

### Why not build the dashboard authoring UI now

The R12 follow-up notes explicitly frame this as a choice:

| Option | Time to first bank live | Ops learning curve | Audit trail |
|---|---|---|---|
| **CSV round-trip (R15)** | days | low — ops already exports CSVs from LMS/ERM | the CSV itself is the audit artifact |
| Dashboard authoring UI (R16) | weeks | medium — new surface, new roles | DB audit only |

CSV wins on all three axes for the pilot. R16 becomes worthwhile
once (a) the pilot has more than ~5 banks each running quarterly
rotations, or (b) ops explicitly asks for a UI. Neither condition
holds today, so we ship R15 and defer R16.

### Rotation runbook (short form)

For any annual rotation (board acknowledgement, ERA, training,
policy manual) or quarterly rotation (committee minutes):

1. Compliance analyst uploads the new PDF to
   `s3://fortress-p2-evidence-<bank>/governance/<yyyy>/<name>.pdf`.
2. Analyst appends a row to the bank's CSV with the new
   `artifact_ref`, updated `source` / `attested_by`, and rotation
   `effective_from` / `effective_to`.
3. Analyst runs
   `python -m scripts.load_governance_evidence_csv --csv <path>
    --apply --supersede`. The old row is marked superseded (audit
   trail preserved); the new row is live.
4. The dashboard widget reads the change on the next request — no
   cache invalidation, no redeploy.

Full detail belongs in `docs/runbooks/governance-evidence-authoring.md`
(R16 material).

### Files changed

- `scripts/load_governance_evidence_csv.py` — new. argparse CLI
  (`--csv`, `--apply`, `--supersede`); dry-run default; CSV shape
  + evidence-type + artifact-ref + date-range validation;
  single-transaction apply; canonical-type frozenset kept in sync
  with the migration + resolver.
- `scripts/setup/data/governance_evidence_testbank.csv` — new.
  Eight-row starter set for testbank covering all canonical types
  with realistic S3 URIs, effective windows, and audit-trail
  fields.

### Verification

- `python3 -m py_compile scripts/load_governance_evidence_csv.py` — passes.
- Parse-check with `_load_csv` against
  `scripts/setup/data/governance_evidence_testbank.csv` returns
  8 rows and covers all 8 canonical types (verified via
  set-diff against `_CANONICAL_TYPES`).
- Validation failure modes are enumerated and rejected at parse
  time: non-canonical `evidence_type`, malformed `artifact_ref`,
  `effective_from > effective_to`, missing header columns.
- Post-apply against testbank, `_resolve_governance` flips
  `method: "fallback"` → `method: "registry"`; response label
  swaps from "Risk-attribution proxy" to "Governance evidence";
  `matched=8, applicable=8` → 100% until a rotation lapses.
- Rollback: `DELETE FROM governance_evidence WHERE bank_id =
  'testbank';` — clean; no downstream FKs.

### Follow-ups (not this fix)

- **R16 — dashboard authoring UI.** Only build once pilot volume
  or ops request justifies it. The CSV round-trip covers the
  pilot's needs at day one.
- **S3 URI existence check.** The loader validates URI *shape* but
  does not confirm the object exists. A follow-up could do a
  `HEAD s3://...` per row (with IAM read scoped to the evidence
  buckets) so an ops fat-finger surfaces at load time, not on the
  examiner's screen. Non-blocking; a broken URI in the widget
  provenance is a self-evident bug ops will catch on first
  examination rehearsal.
- **Rotation reminders.** A daily job that surfaces evidence rows
  within 30 days of `effective_to` as a "needs rotation" callout on
  the dashboard would prevent silent lapses. Cheap follow-up on
  the R12 registry.
- **Multi-bank CSV loading.** The loader accepts arbitrary
  `bank_id` values per row, so a single CSV can carry multiple
  banks. That's already useful but should be documented in the
  runbook.

### Combined status after R1–R15

Every audit category is closed, and every population fix has
shipped:

- `phantom` ✔ (R1, R2)
- `scraping / ingestion` ✔ (R3, R5, R6)
- `business logic` ✔ (R4)
- `definition` ✔ (R7, R8, R10, R11, R12)
- `operational` ✔ (R9)
- `data model` ✔ (R10, R11, R12)
- `population` ✔ (R13 → P1, R14 → P2, R15 → P7)

Widget-status snapshot after R15, testbank specifically:

| Widget | Post-R14 | Post-R15 (apply) |
|---|---|---|
| P1 inventory | "Applicable obligation coverage N%" | unchanged |
| P2 control mapping | "Control coverage N%" | unchanged |
| P7 governance | "Risk-attribution proxy N%" (proxy) | "Governance evidence N%" (registry) |

Testbank composite walk (CCO lens): after R15 the governance dim
moves from ~80 (proxy against ra_currency) to 100 (initial load
covers all 8 canonical types); CCO composite lifts by ~2 points
against the R14 baseline. As with R13 and R14, the movement is a
single honest adjustment — the label change reflects a real read,
and every subsequent tick is either a rotation or a lapse, both of
which have a specific audit trail entry.

Landing-page state after R1–R15: every dim on every persona lens
either reads from a real registry (P1, P2, P4, P6, P7 for banks
with populated registries), a fallback labelled honestly (P1, P2,
P7 for banks pre-population), or a directly-measured proxy that
the label reflects (all others). No hidden phantom, no shared-
numerator double-count, no widget label that lies about its query.
The population queue is closed.

**Next queue candidate:** the readiness page is done at the code
+ population layer. The remaining work is documentation
(governance-evidence rotation runbook, per-bank population playbooks)
and platform work (evidence-authoring UI as R16 if/when the pilot
volume justifies it). None of these are dashboard corrections —
the audit-driven correction track that started at R1 is closed at
R15.

---

<!-- Next resolution lands here. -->


