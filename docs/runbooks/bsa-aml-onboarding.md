# BSA/AML Onboarding — End-to-End (SME & Developer Guide)

**Audience:** a compliance SME or an engineer picking up the BSA/AML slice of
Fortress for the first time.
**Scope:** BSA/AML only — SAR, CTR, CDD/beneficial ownership, CIP, the AML
program rule, Travel Rule, OFAC/SDN screening, and 314(a). Everything below is
grounded in the actual code; file paths are clickable and carry line anchors.

> **The one-paragraph mental model.** Fortress scrapes US regulations into a
> rule corpus (Pipeline 1), extracts each rule into discrete **obligations**,
> syncs that corpus down to a bank's private environment (Pipeline 2), and —
> when a JIRA story arrives — runs a **6-agent pipeline** that retrieves the
> relevant BSA/AML rules, writes **pytest** compliance tests, and opens a
> **GitHub PR** against the bank's repo. A **CCO** reviews/approves at
> checkpoints, signs off on rule changes, and marks rules not-applicable. The
> **dashboard** shows what's left to do (untested obligations, unsigned
> changes, upcoming deadlines, exam readiness). The bank then wires those tests
> to its core banking system and runs them in CI.

---

## 1. What BSA/AML looks like in the system today

Real counts from a synced environment (`compliance_rules`):

| Slice | Count |
|---|---|
| BSA/AML-category rules | **186** |
| FinCEN 31 CFR Part 1010 block (new) | **175** (Critical 99 · High 39 · Medium 25 · Low 12) |
| BSA/AML obligations extracted | 16+ (grows as the extractor runs) |
| Obligations with test coverage | 0 → rises as the pipeline runs |

Representative rule_ids an SME will recognise:

| rule_id | CFR | What it is |
|---|---|---|
| `FINCEN-1010-210-b` | § 1010.210(b) | **AML program** — written, risk-based, board-approved |
| `FINCEN-1010-220-a` | § 1010.220(a) | **CIP** — Customer Identification Program |
| `FINCEN-1010-230-b` | § 1010.230(b) | **Beneficial ownership** — 25% / control prong |
| `FINCEN-1010-311` / `OCC-BSA-CTR-001` | § 1010.311 | **CTR** — currency transaction report ($10k) |
| `FINCEN-SAR-001` | § 1020.320 | **SAR** — suspicious activity report ($5k/$25k, 30-day) |
| `OFAC-SDN-001` | 31 CFR Ch. V | **OFAC/SDN** sanctions screening |

Key BSA/AML thresholds (the numbers the generated tests assert) live in
`src/services/compliance_rule_resolver.py` and `CLAUDE.md`:
`ctr_threshold=10000`, `sar_threshold=5000`, `travel_rule_threshold=3000`,
`wire_dual_approval_threshold=10000`, `ofac_fuzzy_threshold=85`,
`beneficial_ownership_pct=20` (FinCEN CHG-2026-0412).

---

## 2. Current design (the two pipelines)

```
 PIPELINE 1  (Fortress cloud)                 PIPELINE 2  (inside the bank)
 ──────────────────────────────              ─────────────────────────────────────
 scrape eCFR / FFIEC / OFAC                   /webhooks/jira  ── JIRA story in
   │  connectors/regulatory/usa/*               │
   ▼                                            ▼  6-agent pipeline
 compliance_rules  (the corpus)   ─ sync ─►   compliance_rules (replica)
   │  ObligationExtractor                       │  retrieve rules+obligations
   ▼                                            ▼  generate pytest
 obligations  (discrete duties)   ─ sync ─►   obligations ──► GitHub PR (bank repo)
 ofac_sdn_entries / deadlines /                │
 enforcement_actions / changes    ─ sync ─►   CCO dashboard + approvals + sign-off
```

- **Pipeline 1** (`PIPELINE_MODE=pipeline1`, `src/main_pipeline1.py`): scraper +
  the sync API that P2 pulls from. Owns the authoritative corpus.
- **Pipeline 2** (`PIPELINE_MODE=pipeline2`, `src/main_pipeline2.py`): the
  bank-side service — pulls the corpus, runs the agents, serves the CCO
  dashboard. Bank data never leaves here.
- They connect over the **sync API** (`src/api/sync_api.py` on P1,
  `src/services/update_service.py` on P2). See
  `docs/setup/local-dev.md` for running both locally.

### The BSA/AML data model (what to know)
| Table | Role |
|---|---|
| `compliance_rules` | one row per rule; `rule_text`, `cfr_citation`, `severity`, `domain`, `threshold_amount`, `embedding` (pgvector), `tsv` (BM25), `authority_type`, `citation_verified` |
| `obligations` | discrete duties extracted from a rule (`regulation_id` → `rule_id`); `has_test_coverage`, `test_scenario_ids` |
| `workflow_executions` | one 6-agent run per JIRA story; `status` (PENDING→RUNNING→WAITING_APPROVAL→COMPLETED/FAILED), `artifacts` |
| `checkpoint_approvals` | CCO approve/reject at a checkpoint |
| `rule_overrides` | per-bank not-applicable / threshold override |
| `regulatory_change_records` | detected rule changes awaiting CCO sign-off |
| `compliance_deadlines` | filing deadlines with `days_remaining` |

**Rule vs. obligation (important):** a *rule* is "what the regulation says"; an
*obligation* is one concrete "you must do X" clause extracted from it. One CIP
rule → several obligations. Test coverage is tracked against **obligations**,
not rules — that's what the dashboard's coverage % measures.

---

## 3. Code walkthrough — read these, in this order

Start at the data, end at the UI. Each line is "open this file to understand X".

1. **The corpus source** — `src/connectors/regulatory/usa/`
   - `fincen_sar.py` → SAR (1020.320); `occ_bsa.py` → CTR/CDD/Travel Rule;
     `ffiec_bsa.py` → AML program/CIP (1020.x); `ofac_sdn.py` → SDN list;
     `fincen_part1010.py` → the FinCEN Part 1010 general-provisions block
     (AML program, CIP, CDD, CTR, 314(a)…), seeded from
     `data/fincen_part1010_seed.json`.
   - Each connector's `extract_all_rules()` returns `regulations` +
     `requirements`; the scheduler normalises them into `compliance_rules`.
2. **The scraper orchestration** — `src/services/scraper_scheduler.py`
   (`_run_scrape_cycle`): connector → `compliance_rules` upsert → enrichment
   (ObligationExtractor, EnforcementRiskScorer, embeddings).
3. **Rule → obligation extraction** — `src/services/obligation_extractor.py`
   (splits `rule_text` into shall/must/required duties → `obligations`).
4. **The sync** — `src/api/sync_api.py` (P1 serves `/api/sync/*`) and
   `src/services/update_service.py` (P2 pulls: rules, obligations, enforcement,
   SDN, deadlines, change-records).
5. **The 6 agents** — `src/agents/` (see §5) orchestrated by
   `src/orchestration/workflow_engine.py`, entered from `src/api/webhooks.py`
   (`/webhooks/jira`).
6. **Rule retrieval for a story** — `src/agents/understanding.py`
   (`_resolve_obligation_ids` + `_hybrid_search_rule_ids`) and
   `src/services/compliance_rule_resolver.py` (the hybrid pgvector+BM25 search).
7. **CCO validation surfaces** — `src/api/dashboard_api.py`, `src/api/approvals.py`,
   `src/api/exports.py`, `src/services/authority_gate.py`.
8. **The handoff** — `src/agents/execution.py` + `src/services/github_service.py`
   (opens the PR, posts the JIRA comment, flips obligation coverage).
9. **The dashboard UI** — `dashboard/src/pages/*` (Rules, Obligations, Changes,
   Workflows, Readiness, OfacHealth, Cases).

---

## 4. How a rule becomes a test case (the core conversion)

```
compliance_rules.rule_text
   │  ObligationExtractor (regex "shall/must" + LLM fallback)
   ▼
obligations  (one testable duty, e.g. "CIP must collect name, DOB, address, ID")
   │  a JIRA story references a feature touching this area
   ▼
Agent 2 retrieves the obligation + rule (domain lookup + hybrid semantic search)
   │  Agent 3 turns it into BDD scenarios (Given/When/Then at each threshold)
   ▼
Agent 4 (Bedrock) writes pytest code asserting the thresholds/behaviours
   │  Agent 5 validates (syntax + compliance keywords + quality score)
   ▼
Agent 6 opens a GitHub PR with the test; on approve, flips
obligations.has_test_coverage = TRUE  (→ dashboard coverage % rises)
```

Concretely, a CTR obligation ("file a CTR for currency > $10,000") becomes a
pytest with boundary cases at $9,999 / $10,000 / $10,001, an aggregation case,
and a negative case — using `Decimal` for money and a mocked transaction
processor. (Prompt contract: `src/agents/generation_bedrock.py:57-116`.)

---

## 5. The 6-agent pipeline (BSA/AML walkthrough)

**Trigger:** `POST /webhooks/jira` (`src/api/webhooks.py`) → a `workflow_executions`
row → background task → the agents run in order, serialized cluster-wide by a
Postgres advisory lock (one Bedrock call at a time). A context dict threads
agent → agent.

| # | Agent (`src/agents/`) | BSA/AML input → output |
|---|---|---|
| 1 | **discovery.py** | JIRA story → detects patterns (`wire_transfer`, `sar`, `ctr`, `ofac`, `cip`, `bsa_aml`) + compliance requirements |
| 2 | **understanding.py** | patterns + story → **retrieves the rules & obligations** (see below) + scenario outline + edge cases |
| 3 | **framework.py** | → detailed BDD scenarios per threshold (below-/at-/above-CTR, OFAC exact/fuzzy/false-positive, SAR insider/threshold, Reg E timing) |
| 4 | **generation_bedrock.py** | scenarios + rules → **pytest** via Bedrock `claude-sonnet-4-5` (temp 0.3) |
| 5 | **validation.py** | test code → syntax + compliance-keyword check + quality score (0–100; ≥80 = approved) |
| 6 | **execution.py** | approved code → **GitHub PR** + JIRA comment + flips obligation coverage |

**Agent 2 is the heart of "input from rules"** (`src/agents/understanding.py`),
and it now uses **two retrieval paths, unioned**:
1. **Domain-scoped** — pattern → `compliance_rules.domain` → rules → their
   obligations (`ComplianceService.get_rules_by_domain`).
2. **Hybrid pgvector + BM25** (`_hybrid_search_rule_ids`) — embeds the story
   (Titan v2) and scores `0.7·cosine + 0.3·BM25` over the whole corpus, so
   semantically-relevant rules (e.g. the FinCEN Part 1010 CIP/AML/BO rules)
   surface even when the pattern→domain map doesn't name them. Degrades to
   BM25 when embeddings/Bedrock are unavailable.

**Where the test ends up:** a GitHub PR on branch
`fortress/{jira_key}-compliance-tests`, file `tests/compliance/test_{key}.py`
(`src/services/github_service.py`), plus a JIRA comment with the PR URL and the
CFR citation. Metadata is stored in `workflow_executions.artifacts`.

---

## 6. How the CCO validates

Four distinct validation surfaces — all audited (who, when, note):

1. **Workflow checkpoints** (`src/api/approvals.py`, `require_approver`) — a run
   pauses at `pre_generation` / `pre_execution` with
   `workflow_executions.status = WAITING_APPROVAL`. CCO `POST /approvals`
   `{checkpoint, decision: approved|rejected, note}` → `checkpoint_approvals`
   row (status PENDING→APPROVED/REJECTED). Reject → workflow FAILED.
2. **Rule applicability / threshold** (`PATCH /rules/{rule_id}/status`,
   `require_cco`) — CCO marks a rule `not_applicable` (with a required
   `na_reason`) or sets a `threshold_override` → `rule_overrides`. Not-applicable
   rules drop out of the default dashboard scope (but "Show all" still shows them).
3. **Regulatory change sign-off** (`POST /changes/{change_ref}/signoff`,
   `require_approver`) — CCO dispositions a detected change:
   `acknowledged → signed_off`, `requires_action → remediation_planned`,
   `not_applicable → closed`. Writes `regulatory_change_records.signed_off_at` +
   a `change_record_dispositions` audit row.
4. **Examiner-packet sign-off** (`POST /exports/{artifact_id}/sign`) — captures
   signer role, method (SSO/JWT/manual), IP, timestamp, typed name →
   `export_signoffs`. Required signers per report type (e.g. examiner_package =
   BSA_OFFICER + CCO + CRO).

**The authority gate** (`src/services/authority_gate.py` — `may_assert_as_law`):
a rule is presented to an examiner as **binding law** only if
`authority_type == "LAW"` **and** `citation_verified == TRUE`. Otherwise it's
**advisory** ("control expectation per <source>", not a CFR mandate). This split
drives the PR body (blocking vs advisory) and what the CCO is told is mandatory.
*(Note: freshly-scraped rules land `citation_verified = FALSE` until verified
against eCFR, so they read as advisory until then.)*

---

## 7. Pending action items — what the dashboard tells a CCO to do

All from `src/api/dashboard_api.py`:

| Dashboard surface | "To do" signal | Query basis |
|---|---|---|
| **Overview** | pending changes, nearest deadline, untested-rule health % | `/overview` |
| **Changes inbox** | unsigned changes, aged **fresh (<7d) / aging (7–30d) / overdue (≥30d)** | `/changes` (status=`detected`) |
| **Obligations** | obligations with `has_test_coverage = FALSE` ("Awaiting tests") | `/obligations` |
| **Deadlines** | filings by `days_remaining` ascending | `/deadlines` |
| **Workflows** | runs in `WAITING_APPROVAL` needing approve/reject | `/workflows` |
| **Readiness** | 10 scored dimensions; shortfalls get a CTA (e.g. testing → "Generate tests") | `/readiness?lens=cco` |

So a BSA/AML CCO's work queue is literally: **sign off overdue changes →
approve waiting workflows → generate tests for uncovered obligations → clear
upcoming deadlines → close readiness gaps.** The Obligations page is
click-to-expand (read the full duty text); the Rules page defaults to
**applicable + severity-ranked (Critical first)** with a "Show all" toggle.

---

## 8. What happens next — the bank actually implements the tests

This is the step between "Fortress generated a test" and "the bank is actually
testing its system." Fortress produces the test; the **bank wires and runs it**.

1. **Review the PR** (`tests/compliance/test_{key}.py`). The PR body lists the
   CFR citations (blocking law) vs advisory controls, the quality score, and a
   review checklist.
2. **Confirm scenarios & thresholds** match the bank's current obligations (a
   CCO/BSA-officer task — e.g. verify the CTR threshold is $10k, the BO prong is
   20%).
3. **Wire the test to the real system.** The generated tests are **deterministic
   and offline** — they mock the OFAC screener, rule resolver, and transaction
   processor (`generation_bedrock.py` prompt). The bank replaces those mocks /
   fixtures with adapters to its **core banking system** (the test's
   "system under test" — e.g. its wire processor, CTR engine, OFAC service).
   Fortress's own `src/services/validators.py`
   (`WireTransferValidator`, `OFACScreener`, `LoanApplicationValidator`) shows
   the interface shape the fixtures target.
4. **Run in the bank's CI:** `pytest tests/compliance/test_{key}.py -v`.
5. **Merge after QA sign-off.** On a successful run the pipeline flips
   `obligations.has_test_coverage = TRUE`, which raises the dashboard coverage %
   and the readiness "testing" dimension — closing the loop.

> In short: Fortress gives the bank a **ready-to-wire compliance test suite**
> keyed to specific CFR obligations; the bank connects it to its systems and
> runs it. The dashboard then reflects real coverage, and the examiner packet
> can cite which obligations are tested.

---

## 9. Onboarding a NEW BSA/AML rule — end-to-end checklist

The exact path the FinCEN Part 1010 block followed, reusable for any BSA/AML rule:

1. **Add/extend a connector** in `src/connectors/regulatory/usa/` so
   `extract_all_rules()` emits the rule (with `cfr_citation`, `domain`,
   `severity`, `authority_type="LAW"`). Register it in
   `config/connectors/usa.yaml`.
2. **Migrate** if you add a column (`alembic revision` → `alembic upgrade head`).
3. **Scrape** it into P1: `SCRAPER_ONLY_CONNECTORS=<name>` (local) or the ECS
   scrape task (AWS). This populates `compliance_rules`, extracts `obligations`,
   and embeds the rule.
4. **Sync** to P2 (`POST /internal/sync/trigger`) — the rule + obligations land
   in the bank replica.
5. **Verify citation** so the authority gate treats it as binding law
   (`citation_verified=TRUE` via eCFR verification).
6. **Drive a workflow** (JIRA story) → the 6 agents generate tests → PR.
7. **CCO reviews** applicability + approves; **bank wires & runs** the test.
8. **Dashboard** shows the obligation move from "Awaiting tests" → "Tested".

---

## 10. Run it locally

Full setup (P1 + P2 + dashboard, hot-reload, how to trigger a sync and a
workflow) is in **`docs/setup/local-dev.md`**. BSA/AML-specific quick checks:

```bash
# rules + severity for the BSA/AML slice
curl -H "Authorization: Bearer e2e-bypass" \
  "http://localhost:8002/api/v1/dashboard/rules?regulator=FINCEN&severity=Critical"
# obligations coverage
curl -H "Authorization: Bearer e2e-bypass" \
  "http://localhost:8002/api/v1/dashboard/obligations"
```

---

## Appendix — file:line anchor map

| Topic | File |
|---|---|
| FinCEN Part 1010 connector | `src/connectors/regulatory/usa/fincen_part1010.py` |
| SAR / CTR / CIP connectors | `fincen_sar.py`, `occ_bsa.py`, `ffiec_bsa.py` |
| Scrape orchestration | `src/services/scraper_scheduler.py` |
| Obligation extraction | `src/services/obligation_extractor.py` |
| Sync (P1 serve / P2 pull) | `src/api/sync_api.py`, `src/services/update_service.py` |
| 6-agent orchestration | `src/orchestration/workflow_engine.py`, `src/api/webhooks.py` |
| Agents | `src/agents/{discovery,understanding,framework,generation_bedrock,validation,execution}.py` |
| Hybrid rule retrieval | `understanding.py:_hybrid_search_rule_ids`, `compliance_rule_resolver.py` |
| CCO checkpoints | `src/api/approvals.py` |
| Rule override / change sign-off | `src/api/dashboard_api.py` (`PATCH /rules/{id}/status`, `POST /changes/{ref}/signoff`) |
| Authority gate | `src/services/authority_gate.py` (`may_assert_as_law`) |
| Export sign-off | `src/api/exports.py`, `src/exports/signoff.py` |
| Dashboard pending actions | `src/api/dashboard_api.py` (`/overview`, `/changes`, `/deadlines`, `/readiness`, `/workflows`, `/obligations`) |
| PR handoff | `src/agents/execution.py`, `src/services/github_service.py` |
| Generated-test contract | `src/agents/generation_bedrock.py:57-116` |
| Dashboard UI pages | `dashboard/src/pages/*.jsx` |
