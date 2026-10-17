# The 6-Agent Test-Generation Pipeline — How It Works, a Real Example, and Why It Failed

**As-of:** 2026-10-16 · **Status:** baseline snapshot of the P2 "generate compliance
test" flow, captured from a real run of rule `FINCEN-1010-660-R3`. Records the
pipeline as-is, the failures observed, and the strategy — *before* the
improvements. A short "fixes applied since" note is at the end so this stays
faithful, but the body describes the baseline behaviour.

---

## 1. One-paragraph mental model

A CCO clicks **"generate test"** on a compliance rule in the dashboard. P2 creates
a **JIRA story**, then runs a **6-agent pipeline** that: understands the ask,
retrieves the relevant compliance rules (hybrid vector + keyword search over
Aurora), writes **pytest** test code with Bedrock, validates it, opens a
**GitHub PR**, and posts the PR link back on the JIRA story — flipping the
covered obligations' `has_test_coverage` flag along the way. The rule corpus it
searches is produced by **Pipeline 1** (scrape → extract → embed) and **synced
down** to the bank's Pipeline 2 database.

```
CCO clicks rule ──> POST /api/v1/rules/{rule_id}/generate-test
                      │  (creates JIRA story, inserts workflow_executions row = running)
                      ▼
          process_workflow (background task, cluster-wide advisory lock)
                      ▼
          WorkflowOrchestrator.execute_workflow()
    ┌─────────────────────────────────────────────────────────────────┐
    │ A1 Discovery → A2 Understanding → A3 Framework → A4 Generation    │
    │              → A5 Validation → A6 Execution                        │
    └─────────────────────────────────────────────────────────────────┘
                      ▼
      GitHub PR + JIRA comment + obligation writeback + audit trail
```

Code map: endpoint `src/api/generate_test_api.py` → background task
`src/api/webhooks.py::process_workflow` → `src/orchestration/workflow_engine.py`
→ agents in `src/agents/*`.

---

## 2. The six agents

| # | Agent | File | Does | Model |
|---|---|---|---|---|
| 1 | **Discovery** | `agents/discovery.py` | Parses the JIRA story: banking patterns, compliance requirements, related code symbols | heuristic |
| 2 | **Understanding** | `agents/understanding.py` | **Hybrid retrieval** over `compliance_rules` (pgvector cosine 0.7 + BM25 `ts_rank` 0.3), resolves related `rule_ids` and `obligation_ids` the test will cover | Titan v2 embed |
| 3 | **Framework** | `agents/framework.py` | Picks the test framework and generates BDD-style scenarios | heuristic |
| 4 | **Generation** | `agents/generation_bedrock.py` | Writes the actual **pytest** code from the scenarios + retrieved context | Bedrock `claude-sonnet-4.5` |
| 5 | **Validation** | `agents/validation.py` | Syntax check, scenario-coverage %, quality & compliance scores, approve/reject | heuristic |
| 6 | **Execution** | `agents/execution.py` | Opens the **GitHub PR**, posts the PR link to JIRA, writes the audit trail, flips `obligations.has_test_coverage` for the covered set | GitHub + JIRA APIs |

**Retrieval is 100% Aurora/Postgres** — the `embedding vector(1024)` column
(pgvector, Titan v2) and the generated `tsv` tsvector on `compliance_rules`, in a
single hybrid SQL query. There is **no separate vector DB** and the scraper's
JSON "chunks" are **not** read on this path.

---

## 3. The data supply chain behind it (P1 → P2)

The pipeline only works if the rule corpus is present, fresh, and **embedded** in
the bank's P2 database:

```
P1 (Fortress Cloud)                              P2 (bank VPC)
  scrape regulations                               UpdateService.sync_all()
    → extract rules + obligations                    ← /api/sync/initial  (rules)
    → embed (Titan v2, 1024-dim)  ───── sync ─────►  ← /api/sync/embeddings (vectors)
    → store in Aurora                                ← /api/sync/obligations
                                                     then pgvector ANALYZE
```

If rules land in P1 without the embed phase running, they arrive in P2 with
`embedding IS NULL` → invisible to Agent 2's semantic search. (This is exactly
the gap seen below; see `docs/runbooks/embedding-backfill.md`.)

---

## 4. Real worked example — `FINCEN-1010-660-R3`

**Rule:** FinCEN 31 C.F.R. § 1010.660 — correspondent-account obligation.
**Obligation (1):** *"Existing correspondent relationships must be screened
against this designation at onboarding and periodically thereafter"*
(`FINCEN-1010-660-R3::OBL::001`).

A CCO triggered generation on this rule. The JIRA story body (auto-built):

> *Compliance test required — Rule ID `FINCEN-1010-660-R3`, Regulator FINCEN,
> CFR § 1010.660. Acceptance criteria: all assertions cite § 1010.660; cover
> threshold boundary values; audit log entry for every compliance decision.*

### Successful-run trace (workflow_id `19a2c1a0…`, ~1m 23s)

| Time (UTC) | Stage | Outcome |
|---|---|---|
| 16:05:36.1 | A1 Discovery | pattern `limit`; 6 compliance requirements; related `check_ctr_threshold(...)` |
| 16:05:36.1→.9 | A2 Understanding | RAG hybrid search → 12 related rules (OCC-BSA-CDD, FFIEC-BSA-314A, FFIEC-CALL…); BSA knowledge context |
| 16:05:36.9 | A3 Framework | pytest + 5 BDD scenarios |
| 16:05:36.9→16:06:54 | A4 Generation (Bedrock) | **~77 s** — 31,733 chars of pytest, 5 scenarios → 25 tests, correct thresholds (CTR 10k, SAR 5k, Travel 3k, OFAC 85, BO 20%) |
| 16:06:54 | A5 Validation | syntax valid, **quality 95**, **compliance 85**, 100% scenario coverage; flagged: *PII/SSN masking missing, GLBA encryption missing* |
| 16:06:54→16:06:59 | A6 Execution | opened **PR**, posted JIRA comment, audit trail, marked obligations covered |

### Observed outputs across the runs (same rule)

| JIRA | Status | Quality | Compliance | Obl. covered | Tests | PR |
|---|---|---|---|---|---|---|
| MDP-74 | ❌ failed | — | — | — | — | — |
| MDP-75 | ✅ | 100 | 100 | 0 | 37 | none |
| MDP-76 | ✅ | 95 | 85 | 11 | 25 | #20 |

> **Caveat on scores:** quality/compliance are the system grading **itself**
> (Agent 5 scoring Agent 4). They mean "internally consistent, syntactically
> valid, scenario-complete" — **not** independently verified against the
> regulation. Tests also run against **mocks**, not the bank's real core system.
> And we have exercised **one** rule; this is end-to-end validation, not a
> benchmark.

---

## 5. Why it failed — the stacked failures

The flow was dead on arrival; each fix exposed the next failure. In order hit:

| # | Symptom | Root cause | Layer |
|---|---|---|---|
| 1 | `JIRA rejected issuetype='Task' for project MDP` | Code defaults issue type to `Task`; MDP only allows `Idea`; no override set | config (`.env`) |
| 2 | `invalid input value for enum workflowstatus: "running"` | Workflow INSERT uses lowercase `'running'`; the local DB enum labels were **UPPERCASE** (built via `create_all`, not migration 016 which defines lowercase) | DB data |
| 3 | `config profile (fortress-local) could not be found` | Agent 4 builds a Bedrock client via `AWS_PROFILE`; the container had no `~/.aws` | infra/creds |
| 4 | Dashboard "No rules match" (Rules + ~10 panels 500) | `dashboard_api.py` hardcoded **UPPERCASE** status literals (`'COMPLETED'`…) while the canonical enum is lowercase | source bug |
| 5 | PR link missing (`pr_opened=false`) | GitHub PAT returned **401 Bad credentials** | config/creds |

And one **correctness** issue beneath the infra bugs:

| # | Symptom | Root cause |
|---|---|---|
| 6 | Obligations attributed to **OCC-BSA/FFIEC**, not the clicked `FINCEN-1010-660-R3` | A6 flips `has_test_coverage` for the obligations **A2 resolved** — which is pure story-text semantic/keyword retrieval, never seeded with the trigger rule. `FINCEN-1010-660-R3` had **no embedding** and **no domain tag**, so it fell out of the top-12 and never entered the covered set, even though its obligation existed. |

**Underlying data gaps found (baseline):**
- **175 of 187** BSA/AML rules had `embedding IS NULL` (the whole FinCEN Part 1010 block) → semantically invisible.
- **`domain` NULL on all 472 rules** → Agent 2's domain-scoped retrieval path (Path 1) was dead for everyone.
- `obligation_count` on `compliance_rules` is stale vs the actual `obligations` rows (cosmetic; no data missing).

---

## 6. Current strategy (as-is) and known gaps

**Strategy as it stands:**
- Rule corpus comes from P1 (scrape → extract → embed) and is **synced** to each
  bank's P2; generation runs **inside P2** (bank VPC) so bank data stays local.
- Agent 2 retrieves context by **hybrid vector + BM25** over the synced
  `compliance_rules`; Agent 4 generates with Bedrock; Agent 6 raises a PR the
  bank wires into its own CI against its core banking system.
- Obligation coverage (REQ-010) is tracked by flipping
  `obligations.has_test_coverage` for the rules A2 resolves.

**Known gaps at this baseline:**
1. **Attribution** — coverage follows semantic neighbours, not the clicked rule (failure #6).
2. **Retrieval completeness** — unembedded rules (Part 1010) can't surface; `domain` path dead.
3. **Trust** — quality/compliance are self-assessed; no independent/ground-truth validation; tests run on mocks.
4. **Breadth** — validated on a single rule.
5. **Prod parity** — the local DB enum case, missing embeddings, and the two source bugs must be confirmed/fixed in prod P1/P2, and prod must use SSM/Secrets + Cognito (not `.env`/dev-bypass).

---

## 7. Fixes applied since this baseline (for faithfulness)

So this snapshot isn't read as the current live state, the following were applied
after capture (branch `code_cleanup`):
- #1 JIRA `JIRA_ISSUE_TYPE=Idea`; #2 enum labels renamed to lowercase (local DBs);
  #3 mounted `~/.aws` into P2; #4 `dashboard_api.py` lowercased (commit `7dcc5be`);
  #5 valid GitHub PAT.
- #6 **Fix A** — trigger `rule_id` threaded into Agent 2 and unioned into the
  covered set (commit `21dbb42`); re-run **MDP-77** then covered 12 obligations
  including `FINCEN-1010-660-R3::OBL::001`, PR #21.
- Embeddings backfilled for the 175 Part 1010 rules in `fortress_p2` +
  `fortress_dev` (local); prod procedure in `docs/runbooks/embedding-backfill.md`.

Still open: independent validation, breadth testing across the BSA/AML slice,
`domain` backfill, and prod parity — see `docs/runbooks/embedding-backfill.md`
and the production-readiness discussion.
