# Fortress — Examiner Report Exports: build spec for Claude Code

This folder is the **source of truth** for the dashboard's six examination-evidence exports
(the tiles on the Reports tab → `POST /api/v1/dashboard/exports`). Your job is to implement the
generators so each export matches the corresponding template here, populated from **live data**.

---

## 0. Read this first

- The six `*.html` files are **populated examples** for a fictional bank (*Meridian Valley Bank, N.A.*).
- **The structure and styling are the spec. The bank data is placeholder.** Do **not** hard-code
  "Meridian Valley Bank", the sample numbers, the names, or `v.F8A0F39` — wire every value to live data.
- The `<style>` block is **identical in all six files**: it is the shared Fortress design system
  (treat it as `_shared.css`; if you extract it to a real shared stylesheet, keep the output visually identical).
- Open any file in a browser to see the visual gold standard you are matching.

**Do not start coding yet.** First read these, then reply with a short plan and wait for my OK:
- this file
- all six `*.html` templates
- `src/api/dashboard_api.py` (the export endpoint + the data endpoints you'll source from)
- `dashboard/src/pages/Reports.jsx` (the UI that triggers exports)

Your plan should cover: the modules you'll add, how you'll render server-side, how you compute and
store the version hash + artifact, and any field in §4 you **cannot** source from existing data
(flag these — don't invent them).

---

## 1. The six reports

| # | `type` | File | Class | Cadence | Formats |
|---|--------|------|-------|---------|---------|
| 1 | `examiner_package` | `01_examiner_evidence_package.html` | Required | on demand | PDF + CSV |
| 2 | `ofac_attestation` | `02_ofac_screening_attestation.html` | Required | monthly | PDF + CSV |
| 3 | `board_report` | `03_board_audit_committee_report.html` | Governance | quarterly | PDF |
| 4 | `mra_tracker` | `04_findings_mra_tracker.html` | Tracker | on demand | PDF + CSV |
| 5 | `change_log` | `05_regulatory_change_log.html` | Change mgmt | on demand | PDF + CSV |
| 6 | `coverage` | `06_coverage_gap_report.html` | Self-assessment | on demand | PDF + CSV |

Implement **one type per PR**, starting with `examiner_package`, then `ofac_attestation`
(the two Required ones), then the rest. After each, render the output and show me before moving on.

---

## 2. Hard requirements (these are non-negotiable)

1. **Server-render, not browser-print.** Render each export server-side with headless Chromium
   (Playwright) in the export worker and store the bytes. The current "open print dialog" path is
   not archive-safe. Examiner evidence must be an immutable, stored artifact.
2. **Hash the data, not the PDF.** The `v.<hash>` stamp is a content hash of the **canonicalized
   source-data snapshot**, computed **before** rendering. It must be deterministic: identical
   as-of data → identical hash. Do **not** hash PDF bytes (PDFs embed timestamps and are not
   byte-stable). Recommended: stable-serialize the snapshot (sorted keys, fixed number/date
   formats) → SHA-256 → short prefix shown as `v.XXXXXXX`.
3. **Immutable + retained.** Store each artifact in S3 keyed by `{bank, type, as_of, hash}`,
   ≥ 5-year retention. Serve via `GET /api/v1/dashboard/exports/{id}/download`.
   **Never overwrite a prior as-of version** — re-running a past as-of must reproduce the same
   artifact/hash.
4. **Point-in-time.** Every export reflects program state *as of* the requested date. Source the
   snapshot from that as-of, not "now".
5. **Footer on every page:** `institution · report type · as-of date · v.<hash> · Page x / y`.
   (See the footer used when rendering — it's a Playwright `footerTemplate`, not CSS `@page`.)
6. **Fixed vocabulary — do not rename or invent:**
   - Screening decisions: `PASS` / `REVIEW` / `HOLD`
   - Findings use the OCC **5 Cs**: Concern, Cause, Consequence, Corrective Action, Commitment
     (Fed *Issue / Risk / Required Action* + MRIA, and FDIC *MRBA*, map to the same fields)
   - Finding status set: `open` / `past-due` / `pending-validation` / `validated-closed`
   - Change disposition: `acknowledged` / `requires_action` / `not_applicable`
7. **CSV companions** (where the table says PDF+CSV) must contain the row-level data behind the
   PDF's tables, with the columns named in §4.

---

## 3. Build-from vs. compare-against

- **Build from:** the HTML structure + the shared `<style>` block (design tokens below).
- **Compare against:** render your output and visually diff it against the matching `*.html`
  opened in a browser. Treat the template as the gold standard for layout, chips, and footer.
- **Render with the real fonts:** Spectral (display), IBM Plex Sans (body), IBM Plex Mono (code/labels).
  The templates load them from Google Fonts; in the server renderer, self-host/embed them so output
  is deterministic offline.

### Design tokens (from the `<style>` block — keep these exact)
```
--vault   #16212E   (dark letterhead / chips)
--brass   #9A6E2A   (accent, rules, "Required" chip, eyebrows)
--ink     #1A1F26   --mist #6B7785   --line #E3E6EA
semantic  ok #2E7150   warn #9A6E2A   danger #B23A2E
chips     .chip.ok (green) .chip.warn (amber) .chip.bad (red) .chip.neu (slate)
```

---

## 4. Per-report data contract

Each report = a **cover/metadata block** (institution, charter, regulator, total assets,
report type, as-of, version, document id, period, prepared-by, reviewed-by) + the sections below.
Map every field to a source; flag anything you can't source.

### 1 · `examiner_package`
- **Coverage summary:** obligations in scope, with-tested-control (+%), partial, untested, tests executed.
- **Trace table** (one row per obligation; CSV = full set):
  `rule_id, regulator, citation, obligation, risk, control_id, control_owner, test_id, threshold,
   threshold_rationale, result(PASS/PARTIAL/FAIL/NOT TESTED), executed_at, jira_key, pr_url,
   commit_hash, evidence_uri, status`
- **Exceptions/gaps appendix:** rule_id, status, reason, remediation_ref (link to a finding).
- **Sign-off:** BSA Officer/CCO + independent reviewer.
- Sources: rules, obligations, control mapping, test results, workflow/PR trace.

### 2 · `ofac_attestation`
- **List freshness:** list version + publication time, ingested-at, lag, SLA result.
- **Controls tested:** FUZZY-001 (≥92%), CLEAN-001 (no FP), OWN50-001 (≥50%), FRESH-001 (<24h) —
  each: rule_id, threshold, last_tested, result.
- **Completeness counts:** customers / beneficial owners / counterparties screened vs total;
  screening points covered (onboarding, payment, ongoing-on-update).
- **Engine coverage:** SDN entries, names incl. aliases, embedded variants (+%), algorithm.
- **Blocked/rejected + recordkeeping:** counts, 10-business-day reporting flag, annual-report window, 5-yr retention.
- **Independent validation:** last validation date + by whom.
- Sources: ofac-health, screening stats, validation log.

### 3 · `board_report`
- Exec summary tiles (readiness score + trend, coverage %, open findings, OFAC SLA).
- OFAC health; SAR/CTR + higher-risk customer metrics (this qtr vs prior); changes signed off
  (detected/dispositioned/outstanding); findings-by-severity; audits/exams/model-validations/training status; approval block.
- Sources: readiness, screening, changes, findings, training/audit status.

### 4 · `mra_tracker`
- Summary tiles (total open, past-due, pending-validation, closed-this-period, repeats).
- Tracker rows + CSV:
  `id, type(MRA/MRIA/MRBA/Violation/Observation), severity, regulatory_source, concern, cause,
   consequence, corrective_action, commitment, root_cause, root_cause_identified(bool), owner,
   identified_date, due_date, status, evidence_uri, validation, repeat_flag(bool)`
- 2–3 top findings expanded in full 5-Cs card form.
- Sources: findings/issue-management store.

### 5 · `change_log`
- Summary (detected / dispositioned / outstanding for the period).
- Rows + CSV:
  `change_ref, source, citation, published_date, effective_date, detected_date, summary,
   impact(affected control/policy), disposition, owner, impl_link(jira/policy), status`
- Sources: regulatory_change_records + sign-off store.

### 6 · `coverage`
- Summary tiles (total obligations, tested %, partial, untested, trend vs prior as-of).
- Gap register (high-risk first) + CSV:
  `rule_id, regulator, citation, risk, control_mapped(bool), tested(bool), gap_type(no-control /
   mapped-not-tested / partial), priority(P1/P2/P3), remediation_ref`
- Sources: obligations + control-mapping + test results (inverse of `examiner_package`).

---

## 5. Tests to add

- For each type: assert the rendered output contains every **required section/column** from §4.
- **Determinism:** generating an export twice from identical as-of data yields an **identical hash**.
- **Immutability:** re-requesting a past as-of returns the stored artifact, not a re-render with a new hash.
- **Vocabulary:** no status/decision/disposition value outside the fixed sets in §2.6.

---

## 6. Do NOT

- ❌ hard-code the sample bank, names, numbers, or `v.F8A0F39`
- ❌ use the browser print dialog as the generation path
- ❌ hash PDF bytes for the version stamp
- ❌ invent statuses, decisions, or dispositions outside the fixed vocabulary
- ❌ overwrite a prior as-of artifact
- ❌ change the layout/chips/footer away from the templates without flagging it for review

---

## 7. Workflow recap

1. Read the files in §0 → post a plan → wait for OK.
2. Implement `examiner_package` → render → show me → PR.
3. Repeat per type in the §1 order, one PR each.
4. Each PR includes the §5 tests.
