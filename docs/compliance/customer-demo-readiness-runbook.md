# Customer Demo-Readiness Runbook — per use case

Before onboarding a bank for a demo, each always-on view must be *true* on that
bank's data. This runbook documents, per use case: **what the CCO sees · the
problem found · root cause · resolution (done/pending) · steps to complete ·
the demo-ready acceptance check**.

Companion to `regulatory-program-completion.md` (the pillar→report framework).
State below is **measured in `fortress_p2` on 2026-10-30** — re-measure per bank.

> **How to run the steps locally.** The backfill/scrape jobs run in the P1
> container (it has the connectors + Bedrock creds) pointed at the bank DB:
> ```
> docker exec -e DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@postgres:5432/fortress_p2" \
>   -e DRY_RUN=0 fortress-pipeline1 python -m scripts.<job>
> ```
> Every backfill defaults to `DRY_RUN=1` (preview); set `DRY_RUN=0` to write.

---

## 1 · Compliance rules — "the bank's applicable obligation inventory"

- **What the CCO sees:** applicable rules ranked by severity + enforcement risk, with test-coverage and mark-N/A.
- **Problem (measured):** 476 rules (472 active), but only **300 have a citation**, **175 have severity**, and **0 have an enforcement-risk score**.
- **Root cause:** extraction/backfill not fully run on this corpus; the risk score depends on Enforcement attribution (§4), which hasn't run.
- **Resolution:** ⏳ *pending* — backfills exist, not yet applied here.
- **Steps to complete:**
  1. `DRY_RUN=0 python -m scripts.backfill_cfr_citations` → fill the ~176 missing citations.
  2. Backfill `severity` on the ~297 ungraded rules (FinCEN Part 1010 map; migration 051 vocab).
  3. After Enforcement (§4), run the enforcement-risk scorer so `enforcement_risk_score` populates.
- **Demo-ready check:**
  ```sql
  SELECT count(*) FILTER (WHERE cfr_citation IS NULL)      AS missing_citation,
         count(*) FILTER (WHERE severity IS NULL)          AS missing_severity,
         count(*) FILTER (WHERE enforcement_risk_score>0)  AS with_risk
  FROM compliance_rules WHERE is_active;   -- want: 0, 0, >0
  ```

---

## 2 · Obligations — "the testable units behind each rule"

- **What the CCO sees:** per-rule obligations, coverage gaps, launch point for "generate test."
- **Problem (measured):** 198 obligations (all embedded) but covering only **174 of 472 rules (~37%)**.
- **Root cause:** obligation extraction run on a subset; ~298 applicable rules have none, so coverage is uncomputable for them.
- **Resolution:** ⏳ *pending*.
- **Steps to complete:**
  1. Run the P1 obligation extractor for rules with zero obligations (the ~298).
  2. Populate `control_obligation_mappings`, then `DRY_RUN=0 python -m scripts.backfill_obligation_test_coverage`.
  3. Embeddings for any new obligations: `python -m scripts.backfill_embeddings --target obligations`.
- **Demo-ready check:**
  ```sql
  SELECT count(DISTINCT r.rule_id) FILTER (WHERE o.regulation_id IS NULL) AS rules_without_obligations
  FROM compliance_rules r LEFT JOIN obligations o ON o.regulation_id=r.rule_id
  WHERE r.is_active;   -- want: 0 (for applicable rules)
  ```

---

## 3 · Changes — "regulatory changes the CCO must act on" ⚠️ worst offender

- **What the CCO sees:** a short, dated list of rule mutations, each dispositionable → remediation.
- **Problem (measured):** **52,383 rows but only 23 have a `change_delta`**; breakdown `added`=50,562, `removed`=1,805, **`modified`=9**, `clarified`=7, across only **22 distinct rules**; **0 dispositions ever**.
- **Root cause:** the detector logs an `added`/`removed` record per rule on *every scrape cycle* (2026-06-03 → 2026-10-21), so the table is ~99.9% scrape-churn, not real change events. Only the 9 `modified` (+ deltas) are genuine mutations. The CCO disposition workflow has never been exercised.
- **Resolution:** ⏳ *pending* — diagnosed, not fixed. **Do not demo Changes as-is** (52k rows reads as broken).
- **Steps to complete:**
  1. **Purge the churn:** delete `added`/`removed` rows with no `change_delta` (keep `modified`/`clarified` that carry a real diff). Keep a sane recent window.
     ```sql
     DELETE FROM regulatory_change_records
     WHERE change_type IN ('added','removed') AND change_delta IS NULL;
     ```
  2. **Tighten detection:** emit a record only when a genuine field-level `change_delta` is produced (not per-rule-per-scrape) — so the feed is a handful of real mutations.
  3. **Seed the workflow:** disposition 1–2 changes as "requires action" to demo change → remediation epic (`change_record_dispositions` is empty).
- **Demo-ready check:**
  ```sql
  SELECT count(*) total, count(*) FILTER (WHERE change_delta IS NOT NULL) with_delta,
         (SELECT count(*) FROM change_record_dispositions) dispositions
  FROM regulatory_change_records;   -- want: total small (10s), ~all with_delta, dispositions>0
  ```

---

## 4 · Enforcement — "what peers are getting fined for" ✅ reference fix

- **What the CCO sees:** peer actions with date, institution, penalty, citation, and a working link to the source.
- **Problem (was):** every row pointed to the regulator's **listing URL** (no per-action link); summaries were page chrome; penalties/dates empty; OCC/CFPB only, inflated junk rows.
- **Root cause:** the scraper chunked a landing page and stamped one URL on every row; never followed to detail pages; Bedrock creds missing in P1; CFPB body not scraped; an `action_type` CHECK-constraint rollback.
- **Resolution:** ✅ *done for CFPB* — index→detail crawl (unique links), relevance gate, real dates, CFPB body selector, `max_pages` cap for throttling, constraint-valid `action_type`, and the P1 `~/.aws` Bedrock mount. Result: **9 CFPB actions, 6 with real penalties** (Block $130M, CFPB $15M, Honda $12.8M, Draper-Kramer $1.5M, Wise $494,955). Commits `caa04d6`, `b0ddcaa`.
- **Steps to complete (remaining):**
  1. Add FinCEN/OCC/FDIC/FRB via their data feeds (CFPB is the template).
  2. Run the citation→`enforcement_risk_score` scorer (also unblocks Rules §1 risk ranking + Readiness "risk-assessment currency").
- **Demo-ready check:**
  ```sql
  SELECT regulator, count(*) rows, count(*) FILTER (WHERE source_url NOT LIKE '%/actions/') OR true AS _,
         count(distinct source_url) distinct_links, count(penalty_amount_usd) with_penalty
  FROM enforcement_actions GROUP BY 1;   -- want: distinct_links = rows, penalties present
  ```

---

## 5 · OFAC / SDN screening — "sanctions screening health" ✅ strong

- **What the CCO sees:** SDN screening health + vector match quality (OfacHealth / SdnSearch pages); feeds the `ofac_attestation` report.
- **Problem (measured):** none material — **47,608 SDN vectors, 100% embedded**.
- **Root cause:** n/a.
- **Resolution:** ✅ *healthy* — this is a strong pillar to lead the demo with.
- **Steps to complete:** confirm per-class thresholds are loaded (SSM `/fortress/p2/ofac/threshold/{class}`) and the screener mode is set; otherwise none.
- **Demo-ready check:**
  ```sql
  SELECT count(*) sdn_vectors, count(*) FILTER (WHERE embedding IS NOT NULL) embedded
  FROM ofac_sdn_vectors;   -- want: embedded = sdn_vectors (47,608 / 47,608)
  ```

---

## 6 · Reports / Readiness — the composite deliverables

- **What the CCO sees:** exam-readiness scorecard + downloadable reports (`coverage`, `change_log`, `examiner_package`, `board_report`, `ofac_attestation`, `mra_tracker`).
- **Problem (measured):** inputs are seed-level — 1 `bank_profile`, 2 `controls`, 50 seeded `findings`, 10 `workflow_executions`, 6 `export_artifacts`. Composite reports render but on partial pillars.
- **Root cause:** a report inherits its weakest pillar; §§1–4 aren't all green yet.
- **Resolution:** ⏳ *pending* on §§1–4.
- **Steps to complete:**
  1. Single-pillar reports first: `coverage` (needs §1+§2), `change_log` (needs §3), `ofac_attestation` (§5 — ready now).
  2. Composite last: `examiner_package` + `board_report` once §§1–4 are green.
  3. Collect required sign-offs (`export_signoffs`) — examiner_package = CCO + Internal Audit; ofac_attestation = BSA officer + CRO.
- **Demo-ready check:** each report generates without null-coverage, and its required signers have signed (un-watermarked PDF unlocks).

---

## The end-to-end workflow — rule → CCO approval → test → validation → product

The pillars (§§1–6) are the *data*. This is the *action loop* a customer buys:
take a rule, generate a tested control, and land it in their product. Measured
in `fortress_p2` 2026-10-30: **10 workflow runs (9 completed, 1 failed), all for
one rule (`FINCEN-1010-660-R3`), 8 opened a GitHub PR, 0 CCO approvals.**

### 7 · Rule onboarding — "which rules apply to this bank, and is it tested"

- **What the CCO sees:** the Rules page — applicable rules, test-coverage badge, "Generate test" and "Mark N/A (with reason)".
- **Problem (measured):** only **1 `rule_overrides` row** — mark-N/A barely exercised; applicability scoping leans on the §1 data gaps (missing citations/severity).
- **Root cause:** onboarding flow works; it just hasn't been run across the corpus for this bank.
- **Resolution:** ✅ *mechanism works* / ⏳ *data breadth pending §1*.
- **Steps to complete:** finish §1 (citations/severity) so ranking is meaningful; walk the CCO through marking 2–3 rules N/A with a reason (exam-defensible scoping).
- **Demo-ready check:** Rules page lists applicable rules ranked by severity+risk, each with a coverage badge; a mark-N/A writes a `rule_overrides` row with a reason.

### 8 · CCO approval (checkpoint gate) — "human-in-the-loop before anything ships" ⚠️

- **What the CCO sees:** a workflow pauses at a checkpoint for CCO approve/reject before test-gen proceeds / a PR is opened.
- **Problem (measured):** **`checkpoint_approvals` = 0** — the approval gate has **never been exercised**. Every one of the 10 runs went straight through.
- **Root cause:** runs were triggered without the approval checkpoint engaged (or auto-approved in dev).
- **Resolution:** ⏳ *pending* — this is the single most important workflow gap for a compliance buyer (they must see the human gate).
- **Steps to complete:** trigger a workflow that stops at the CCO checkpoint; approve one and reject one from the dashboard so `checkpoint_approvals` shows both outcomes; confirm a rejected run does **not** open a PR.
- **Demo-ready check:**
  ```sql
  SELECT decision, count(*) FROM checkpoint_approvals GROUP BY 1;  -- want: both approve and reject present
  ```

### 9 · Test generation (6-agent pipeline) — "rule → runnable pytest" ✅

- **What the CCO sees:** a generated pytest module with the obligation's citation in each assertion, plus the agent trail and a quality score.
- **Problem (measured):** works (9 completed), but **all 10 runs target one rule** (`FINCEN-1010-660-R3`) — no breadth; 1 run failed.
- **Root cause:** pipeline is sound; it just hasn't been exercised across diverse rules.
- **Resolution:** ✅ *working* (Bedrock Claude Sonnet, full `discovery→…→execution` artifact trail).
- **Steps to complete:** run test-gen on 3–5 diverse rules (CTR, OFAC, Reg E, wire) so the demo isn't one rule repeated; investigate the 1 failed run.
- **Demo-ready check:**
  ```sql
  SELECT count(DISTINCT rule_id) rules, count(*) runs,
         count(*) FILTER (WHERE status='completed') ok
  FROM workflow_executions;   -- want: rules >= 3, ok/runs high
  ```

### 10 · Validation — "the generated test is correct and grounded" ✅

- **What the CCO sees:** validation result + sandbox pytest execution outcome + quality score (Agents 5 & 6).
- **Problem (measured):** present on completed runs (`validation`, `execution`, `quality_score` in artifacts); tied to the same single-rule breadth gap as §9.
- **Root cause:** n/a — validation runs as part of the pipeline.
- **Resolution:** ✅ *working*.
- **Steps to complete:** none beyond the §9 breadth runs; surface the quality score + sandbox result in the demo narrative.
- **Demo-ready check:** completed runs carry non-null `artifacts->>'validation'` and `artifacts->>'quality_score'`.

### 11 · Integration to product — "the tested control lands in the bank's codebase" ✅

- **What the CCO sees:** a GitHub pull request with the generated test, linked from the workflow + its JIRA story.
- **Problem (measured):** **8 of 9 completed runs have a `github_pr_url`**; works. (1 completed run has no PR — worth checking.)
- **Root cause:** n/a — `github_service.create_pr` opens the PR; URL stored in `artifacts.github_pr_url`.
- **Resolution:** ✅ *working* (gated on `GITHUB_TOKEN`/org/repo config; skips cleanly if unset).
- **Steps to complete:** confirm the pilot bank's GitHub org/repo + token are set so PRs land in *their* repo; demo one PR end-to-end from the dashboard link.
- **Demo-ready check:**
  ```sql
  SELECT count(*) FILTER (WHERE artifacts->>'github_pr_url' IS NOT NULL) prs
  FROM workflow_executions WHERE status='completed';   -- want: ~all completed runs
  ```

---

## Onboarding order (fastest path to a credible demo)

**Must-fix before any demo:** §8 CCO approval gate (0 approvals — a compliance
buyer must see the human-in-the-loop) and §3 Changes (never show the 52k state).

1. **Lead with OFAC (§5)** — green today.
2. **The action loop (§§7–11)** — the core value: pick a rule → CCO approves at the checkpoint → 6-agent test-gen → validation → GitHub PR. Fix §8 (seed an approve + reject) and run §9 across 3–5 diverse rules first, so it isn't one rule repeated.
3. **Enforcement (§4)** — CFPB is real; add regulators + risk scorer.
4. **Rules + Obligations (§1,§2)** — backfills → unlocks the **Coverage report**.
5. **Changes (§3)** — purge + tighten → unlocks the **Change log**.
6. **Composite reports (§6)** — examiner package + board report once the rest are green.

## Demo-readiness scorecard (measured 2026-10-30)

| # | Use case | Status | Blocker |
|---|---|---|---|
| 5 | OFAC / SDN | ✅ ready | — |
| 9 | Test generation | ✅ works | single-rule breadth |
| 10 | Validation | ✅ works | breadth (§9) |
| 11 | Product integration (PR) | ✅ works | confirm bank repo/token |
| 7 | Rule onboarding | 🟡 works, thin data | §1 backfills |
| 4 | Enforcement | 🟡 CFPB only | other regulators + risk scorer |
| 1 | Compliance rules | 🟡 partial | citations/severity/risk |
| 2 | Obligations | 🟡 37% coverage | extract remaining rules |
| 8 | CCO approval gate | 🔴 never exercised | seed approve + reject |
| 3 | Changes | 🔴 52k noise, 0 dispositions | purge + tighten + disposition |
| 6 | Composite reports | ⏳ pending | needs §§1–4 green |
