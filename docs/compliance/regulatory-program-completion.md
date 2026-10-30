# Regulatory Program Completion — pillars → reports

How the four always-on data pillars (**Compliance rules · Obligations · Changes
· Enforcement**) roll up into the generated reports, what "done 100% correctly"
means for each pillar, and which reports become exam-grade as each pillar lands.

> **The governing rule:** reports are *derived* from the pillars. **A report is
> only as correct as its weakest input pillar** — it is "100% correct" only when
> *every* pillar it draws from is complete and correct.

---

## 1 · What "done 100% correctly" means per pillar

| Pillar | Table(s) | Complete & correct when… |
|---|---|---|
| **Compliance rules** | `compliance_rules` + `bank_profile` | Every applicable federal + state rule is scraped and LLM-extracted (grounded: real `cfr_citation`, `severity`, `jurisdiction_level`) and **scoped to this bank's charter / asset tier** — nothing applicable missing, nothing inapplicable included, no invented citations. |
| **Obligations** | `obligations` (+ embeddings, `control_obligation_mappings`) | Every rule's testable obligation sentences are extracted and mapped to controls/tests, so coverage is computable per *obligation* (the testable unit), not just per rule. |
| **Changes** | `regulatory_change_records` (+ `change_delta`, dispositions) | Change detection runs on every scrape, per-record diffs are captured, and each real mutation is dispositioned by the CCO (and routed to remediation). No missed or false changes. |
| **Enforcement** | `enforcement_actions` + `enforcement_risk_scores` | All regulators ingested via index→detail (unique per-action links, real dates/penalties/citations); citations attributed to corpus rules → per-regulation `enforcement_risk_score` feeds "risk-assessment currency." |

---

## 2 · Reports → pillar dependencies

The six report types are defined in `src/exports/policy.yaml` (with required
officer signers). Each is 100% correct only when the pillars below are complete:

| Report (`policy.yaml`) | Depends on | 100% correct when… | Signers |
|---|---|---|---|
| **Coverage report** (`coverage`) | Compliance rules **+** Obligations | Rules + Obligations complete | CCO |
| **Change log** (`change_log`) | Changes | Changes complete | CCO |
| **MRA tracker** (`mra_tracker`) | Changes + Enforcement + findings | those complete | CCO |
| **OFAC attestation** (`ofac_attestation`) | OFAC SDN screening *(separate pillar — `OfacHealth`)* | OFAC vector store complete | BSA officer + CRO |
| **Examiner evidence package** (`examiner_package`) | **ALL 4 pillars** + findings + sign-offs | the whole program is complete | CCO + Internal Audit |
| **Board / audit-committee report** (`board_report`) | **ALL 4 pillars** (readiness aggregation) | the whole program is complete | CCO |

The live **Readiness scorecard** (`dashboard/src/pages/Readiness.jsx`) is the
dashboard version of the composite — fully trustworthy only when all four
pillars are.

### Which reports are "100% correct if done"
- Finish **Rules + Obligations** → the **Coverage report** is exam-grade.
- Finish **Changes** → the **Change log** is exam-grade.
- Finish **all four** → the **Examiner package** and **Board report** are
  exam-grade (the composite, exam-facing deliverables — the point of the program).

---

## 3 · Completion order (reports unlock progressively)

Because a report inherits its pillars' correctness, complete pillars in
dependency order and reports light up as you go:

1. **Rules + Obligations** (one P1 extraction run + control/test mapping) → **Coverage report**.
2. **Changes** detection live + dispositions → **Change log**.
3. **Enforcement** across all regulators + citation→risk loop → **risk-assessment currency** populates.
4. All four green → **Examiner package + Board report** are 100% correct.

---

## 4 · Current state — **measured in `fortress_p2`** (2026-10-30)

Not aspirational — these are live counts from the demo database. They are what
a customer would see today, and they set the completion work in §4b.

| Pillar | Measured state | Gap to "100% correct" |
|---|---|---|
| **Compliance rules** | 476 rows (472 active); **300/472 have a citation**, **175 have severity**, all embedded, **0 have an enforcement-risk score** | ~176 rules missing `cfr_citation`, ~297 missing `severity`, risk score never computed |
| **Obligations** | 198 obligations, all embedded, covering **174 of 472 rules (~37%)** | ~298 applicable rules have **no** obligations → coverage is uncomputable for them |
| **Changes** | **52,383 rows** but only **23 have a `change_delta`**; 50,562 are `added`, 1,805 `removed`, **9 `modified`**, 7 `clarified`; across only **22 distinct rules**; **0 dispositions** | 99.9% is scrape-churn noise, not real mutations; the CCO disposition workflow has never been exercised |
| **Enforcement** | **CFPB only — 9 actions** (6 with penalties, 6 cited); other regulators empty | FinCEN/OCC/FDIC/FRB not ingested; risk-scoring loop not run |
| **Reports / Readiness inputs** | 1 `bank_profile`, 2 `controls`, 50 `findings` (seeded), 10 `workflow_executions`, 6 `export_artifacts` | findings/controls are seed data; composite reports render but on partial pillars |

---

## 4b · How to complete each pillar — to show a customer

Concrete, data-grounded next steps. Each ends at a **demo-ready** bar.

**Compliance rules** — backfill the gaps, compute risk.
1. `python -m scripts.backfill_cfr_citations` → fill the ~176 missing citations.
2. Backfill `severity` on the ~297 ungraded rules (FinCEN Part 1010 map, migration 051 vocab).
3. Run the enforcement-risk scorer so `enforcement_risk_score > 0` (depends on Enforcement §below).
- **Demo-ready when:** every active rule has a citation + severity, and the Rules page ranks by severity + risk.

**Obligations** — extend coverage, map to controls.
1. Run obligation extraction for the ~298 rules with none (P1 obligation extractor).
2. Populate `control_obligation_mappings` so coverage % is real, then `python -m scripts.backfill_obligation_test_coverage`.
- **Demo-ready when:** obligations exist for all applicable rules and the Coverage report shows a true tested-vs-untested %.

**Changes** — purge the noise, show real mutations.
1. **Purge the 50k+ `added`/`removed` scrape-churn** (keep `modified`/`clarified` with a `change_delta`); the 52,383→23-with-delta ratio proves the bulk aren't real diffs.
2. Fix detection to only emit a record when `change_delta` is non-empty (a genuine rule mutation), so the feed is a handful of dated changes, not 52k.
3. Record a few CCO dispositions (`change_record_dispositions` is empty) to demo the requires-action → remediation flow.
- **Demo-ready when:** Changes shows a short, dated list of real mutations with deltas and at least one dispositioned → remediation example.

**Enforcement** — add regulators, run the risk loop.
1. Add FinCEN/OCC/FDIC/FRB via their data feeds (CFPB already live).
2. Run the citation→`enforcement_risk_score` scorer (this also unblocks Rules risk ranking and readiness "risk-assessment currency").
- **Demo-ready when:** multi-regulator feed with penalties + per-regulation risk scores feeding the Rules ranking and Readiness.

**Reports / Readiness** — once the four above are green.
- Generate `coverage` and `change_log` first (single-pillar), then `examiner_package` + `board_report` (composite), each sign-off-gated (§5).

---

## 4c · Measured demo-readiness scorecard — all 11 use cases (2026-10-30)

Full per-use-case detail (problem → root cause → resolution → steps → SQL check)
lives in `customer-demo-readiness-runbook.md`. Summary:

| # | Use case | Status | Measured | Blocker |
|---|---|---|---|---|
| 5 | OFAC / SDN screening | ✅ ready | 47,608 vectors, 100% embedded | — (lead with this) |
| 9 | Test generation (6-agent) | ✅ works | 9 completed runs, full agent trail | single-rule breadth |
| 10 | Validation (Agents 5–6) | ✅ works | quality score + sandbox execution | breadth (§9) |
| 11 | Product integration (PR) | ✅ works | 8/9 completed opened a GitHub PR | confirm bank repo/token |
| 7 | Rule onboarding | 🟡 thin | 1 `rule_overrides` | §1 backfills |
| 4 | Enforcement | 🟡 partial | CFPB 9 (6 penalties); others empty | regulators + risk scorer |
| 1 | Compliance rules | 🟡 partial | 476; 300 cited / 175 severity / **0 risk** | citations + severity + risk |
| 2 | Obligations | 🟡 partial | 198, covering 174/472 rules (~37%) | extract remaining rules |
| 8 | **CCO approval gate** | 🔴 not functional | **`checkpoint_approvals` = 0** | engine never pauses (see below) |
| 3 | **Changes** | 🔴 noisy | 52,383 rows / 23 deltas / **0 dispositions** | purge + tighten + disposition |
| 6 | Composite reports | ⏳ pending | 6 `export_artifacts`, 50 seeded findings | needs §§1–4 green |

**The two must-fix before any demo:**
1. **CCO approval gate (§8)** — a compliance buyer must see the human-in-the-loop.
   Root cause: the approvals API exists (`src/api/approvals.py`), but
   `execute_workflow` runs all six agents straight through to the PR and **never
   pauses** at a checkpoint — so nothing ever waits, and a reject can't stop a PR
   that already happened. Fix = pause before the PR step (Agent 6), gate it on an
   approval, reject → no PR. Tracked in `customer-demo-readiness-runbook.md §8`.
2. **Changes (§3)** — 99.9% scrape-churn; purge `added`/`removed` with no delta,
   emit records only on a real `change_delta`, seed a disposition.

**Method note (what these checks do and don't prove).** The measurements above
verify **completeness and gross noise** — row counts, citations/severity present,
coverage %, deltas present, PRs opened, approvals recorded. They do **not**
confirm **semantic correctness**: whether each extracted citation is the *right*
one, whether a generated test is *actually valid*, whether a penalty figure is
the *real* one. That requires SME / engineer spot-review, which is the acceptance
gate for the composite reports (§6) — "green" in the scorecard means "present and
not obviously broken," not "SME-verified correct."

---

## 5 · Governance gate (independent of data correctness)

Correct *data* is necessary but not sufficient. Every report also requires
**officer sign-offs** (`export_signoffs`) before the un-watermarked PDF unlocks —
e.g. the examiner package needs **CCO + Internal Audit**, the OFAC attestation
needs **BSA officer + CRO**. Artifacts are WORM-locked. So a report becomes
releasable only when its pillars are complete **and** its required signers have
signed.

---

*Source of truth for report types + signers: `src/exports/policy.yaml`. Pillar
→ endpoint → table mapping: `docs/design/dashboard-data-map.md`. Business-value
framing: `docs/compliance/dashboard-completion-sme-review.md`.*
