# Examination Readiness — Fix Plan

**Status:** Score sits at **29 / 100 (Failing)** as of 2026-06-11.
**Goal:** Document the use case, root-cause problem, and solution for each of
the six issues blocking score recovery and adjacent CCO-dashboard panels.

---

## Background — how the score is built

`GET /api/v1/dashboard/readiness` (`src/api/dashboard_api.py:488-505`) returns
a weighted average of 10 posture dimensions. Each dimension is a 0–100
percentage; the score is `Σ(dim × weight) / Σ(weights)` with
`Σ(weights) = 11.1`.

| Dimension | Weight | What it measures | Current state |
|---|---|---|---|
| `testing` | 1.5 | `obligations.has_test_coverage = TRUE` ratio | 0 / 81 — floor |
| `change_mgmt` | 1.3 | `(chg_total − chg_open) / chg_total` | 225 open — floor |
| `control_mapping` | 1.3 | obligations with regulator set | OK |
| `inventory` | 1.2 | rules with `cfr_citation IS NOT NULL` | grid shows "—" — floor |
| `governance` | 1.2 | rules with `enforcement_risk_score` | all "Low" — defaults |
| `findings` | 1.1 | **hardcoded 80** | 80 |
| `independent` | 1.0 | distinct rules with completed `workflow_executions` | ~16 / 224 |
| `ra_currency` | 1.0 | rules with risk score > 0 | floor |
| `reporting` | 0.9 | **hardcoded 40** | 40 |
| `engine` | 0.6 | SDN freshness + embed coverage + pipeline success | mixed |

The four bold rows in red are the load-bearing fixes. Together they account
for ~5.0 weight units of zeroed score — roughly **40+ points** of recoverable
ground.

---

## Fix 1 — Test-coverage writeback bug *(P0 — bug)*

**Use case.**
When the 6-agent pipeline (`discovery → understanding → framework → generation
→ validation → execution`) successfully generates a test for an obligation,
that obligation should be marked as covered so the testing dimension reflects
real work. Today the testing dimension carries 1.5× weight — the highest of
all ten — and is stuck at 0%, capping the entire score at ~85 even in the
best case.

**Problem.**
`obligations.has_test_coverage` is **never set to `TRUE`** by any code path in
the repository.

- Schema: `src/models/database.py:374` — `Boolean, nullable=False, default=False`.
- Only writer: `src/services/obligation_extractor.py:118` — hardcoded `False`
  on insert.
- Confirmed absence: `grep -r "has_test_coverage\s*=\s*True"` returns zero
  matches. `src/agents/*` has zero references to `obligation_id` at all, so
  the pipeline doesn't even know which obligations its tests cover.

Net effect: every dashboard read evaluates `0 / 81 = 0%` permanently, no
matter how many agent runs complete successfully.

**Solution.**
End-to-end thread the `obligation_id` through the agent context, then write
back on success:

1. **Agent 1 — discovery** (`src/agents/discovery.py`): when JIRA story →
   `ComplianceRule(s)` resolved, also resolve matching `Obligation` rows
   (join on `regulation_id`) and add `obligation_ids: list[str]` to the
   workflow context dict.
2. **Agent 6 — execution** (`src/agents/execution.py`): on successful pytest
   run + PR merge, execute:
   ```sql
   UPDATE obligations
      SET has_test_coverage = TRUE,
          coverage_updated_at = NOW(),
          test_scenario_ids = test_scenario_ids || :new_ids
    WHERE obligation_id = ANY(:targeted_obligation_ids);
   ```
3. **Migration 024** *(deferred — see decision below)*: add
   `workflow_executions.covered_obligation_ids JSONB DEFAULT '[]'` so the
   `independent` dimension can join on real coverage rather than rule count.

   **Decision (2026-06-11): defer.** Alembic head is already `033`, so the
   real number would be `034`. The current writeback already stores
   `{jira_key}::{scenario_name}` strings in `obligations.test_scenario_ids`
   (JSONB) and sets `coverage_updated_at`, which gives full audit traceability
   without a new column. The `independent` dim
   (`dashboard_api.py:535-540, 598`) still counts DISTINCT `rule_id` joined to
   `workflow_executions` — that signal continues to work because every
   successful run already writes its `rule_id` on the row. Adding
   `covered_obligation_ids` would be pure denormalisation of data we can read
   from `obligations` and is not on the score-recovery critical path.
   Revisit if/when "show me every obligation covered by workflow W" becomes a
   per-request query (today it's a per-obligation read). When that happens
   the migration is ~5 lines:

   ```python
   op.add_column(
       'workflow_executions',
       sa.Column('covered_obligation_ids', postgresql.JSONB(),
                 server_default='[]', nullable=False),
   )
   ```
4. **Backfill script**: for `workflow_executions` already in `status='completed'`,
   infer obligation_ids from `regulation_id` and flip flags so the dashboard
   reflects existing work, not just future work.
5. **Test**: add a unit test asserting `has_test_coverage` flips after a
   happy-path agent run end-to-end (use the existing P2 test bank fixture).

---

## Fix 2 — Run `enforcement_risk_scorer` *(wiring)*

**Use case.**
Two dimensions read `compliance_rules.enforcement_risk_score`:
- `governance` (weight 1.2)
- `ra_currency` (weight 1.0)

These represent whether the bank has *measured* its risk exposure per rule
— a regulator-table-stakes view. They also feed the dashboard's risk-band
grid (Low / Medium / High / Critical), which today shows all 224 rules in
"Low" — visually flattening the panel.

**Problem.**
`src/services/enforcement_risk_scorer.py:166` exists and contains the right
UPDATE statement:

```python
update(ComplianceRule)
  .where(ComplianceRule.rule_id.contains(reg_id))
  .values(enforcement_risk_score=score)
```

But it isn't invoked in the production scrape cycle (`scraper_scheduler.py`
runs obligation extraction post-scrape but not the risk scorer), so risk
scores stay NULL. The `governance` and `ra_currency` dimensions fall back to
hardcoded defaults of 60 / 80.

Secondary concern: the join `rule_id.contains(reg_id)` is a substring match.
Even if invoked, it may not match given the rule_id format currently in use.

**Solution.**

1. Verify upstream data: `SELECT COUNT(*) FROM enforcement_actions;` — if 0,
   the connector that feeds the scorer is the blocker, not the scorer itself.
2. Dry-run the scorer against prod Aurora and log how many rows the
   `rule_id.contains(reg_id)` join touches. If it touches 0, fix the join key
   (probably switch to exact `regulator + section` match).
3. Wire `scorer.run()` into `scraper_scheduler.py` immediately after
   `obligation_extractor.run()`.
4. Add `make backfill-risk` Makefile target for an immediate one-shot
   population (don't wait for the next scrape cycle).
5. Acceptance check: `SELECT COUNT(*) FROM compliance_rules WHERE
   enforcement_risk_score > 0` must be > 0 after the backfill, and the
   dashboard risk-band grid must show rules outside "Low".

---

## Fix 3 — Backfill `cfr_citation` on `compliance_rules` *(data)*

**Use case.**
The `inventory` dimension (weight 1.2) measures whether each rule is
properly attributed to a CFR citation — the regulator-recognized identifier
for "what statute does this implement." Without it, the rule is technically
in the inventory but examiners can't trace it. Dashboard grid currently shows
every CFR column as "—" for all 224 rules.

**Problem.**
`cfr_citation` is populated only at scrape time — via `sync_api.py:155` and
`scraper_scheduler.py:642/681`. There is no UPDATE path elsewhere.
Connectors that don't emit `cfr_citation` (or that emitted it before the
field existed) leave it NULL forever.

**Solution.**
Two parallel tracks:

- **Track A — regex backfill** (immediate). The obligation extractor already
  has the right pattern: `_CFR_RE = re.compile(r"(\d+\s+C\.F\.R\.?\s*§\s*[\d.]+(?:\([a-z0-9]+\))*)", re.I)`
  at `obligation_extractor.py:29`. Reuse it: scan `compliance_rules.rule_text`,
  write the first hit into `cfr_citation` where NULL.

- **Track B — connector audit** (correct fix). For each of the 17 federal +
  5 state connectors in `src/connectors/regulatory/usa/`, confirm it emits
  `cfr_citation`. Patch the offenders so future scrapes don't re-introduce
  the gap.

**Workflow.**

```bash
# Diagnose: which connectors are the offenders?
SELECT regulator, COUNT(*) FROM compliance_rules
 WHERE cfr_citation IS NULL GROUP BY regulator ORDER BY 2 DESC;
```

Then `scripts/backfill_cfr_citations.py` (dry-run + commit modes,
transactional, with row-count assertion). Acceptance: dashboard inventory
grid populates the CFR column.

---

## Fix 4 — Bulk-acknowledge 225 unsigned `regulatory_change_records` *(process)*

**Use case.**
The `change_mgmt` dimension (weight 1.3) = `(chg_total − chg_open) / chg_total`.
For an examiner, this represents "every regulatory change the bank has
detected has been triaged by a human." Sitting on 225 untriaged changes is
exactly the audit finding the dashboard is supposed to prevent.

Commit `72e5f3c` already excludes OFAC SDN variants from this count, so the
225 are real rule mutations, not SDN list noise.

**Problem.**
Two failure modes coexist:
1. There's no CCO-facing UI surface to acknowledge a change record. The
   workflow API exists (`src/api/workflows.py`) but isn't wired to a
   dashboard page.
2. With no UI, even small batches accumulate.

**Solution.**
Decide based on context:

- **Pilot / clean-slate go-live**: seed sign-off. Write `signed_off_at =
  NOW()`, `signed_off_by = "bulk_seed"`, plus an audit row in
  `change_record_comments` with `comment_type='system'` and a note like
  "Initial seed sign-off; pre-existing records cleared at go-live." **Do not
  use this path on a production bank with real ops** — it falsifies the audit
  trail.

- **Production**: minimal sign-off UI. Add a "Pending changes" panel to the
  CCO dashboard:
  - table view of `regulatory_change_records WHERE signed_off_at IS NULL`
  - per-row "Acknowledge" button POSTing to the existing approval endpoint
  - filter by impact_rating so high-impact items surface first
  - bulk-select for low-impact items

**Workflow.**
- [ ] Stakeholder decision: seed vs UI (or both — seed historical, UI for
      ongoing).
- [ ] If seed: `scripts/seed_changeset_signoff.py` with explicit
      `--reason=initial_seed` flag and audit-trail rows.
- [ ] If UI: extend `dashboard/src/pages/Changes`; reuse `workflows.py`
      approval endpoint.
- [ ] Acceptance: `change_mgmt` dim trends toward 100 after sign-offs land.

---

## Fix 5 — Make the Examination Readiness score transparent *(UX)*

**Use case.**
After Fixes 1–4 land, the score should move from 29 → ~70+. But operators
need to *see* which dimension moved and which is still lagging — otherwise
"29 → 47 → 62" looks arbitrary, and they can't target the next intervention.
This fix doesn't move the score; it makes the score actionable.

**Problem.**
The tile shows "29 / 100 Failing" with a one-line business read. The
per-dimension breakdown exists internally in `dashboard_api.py:488-505` but
isn't returned to the frontend, so users have to source-dive the codebase to
know what to fix.

**Solution.**

1. **Backend.** Extend the readiness endpoint response to include:
   ```json
   "dimensions": [
     {
       "key": "testing",
       "label": "Testing",
       "weight": 1.5,
       "value": 0,
       "weighted_contribution": 0.0,
       "shortfall_impact": 13.5    // weight × (100 − value) / Σ(weights), in score points
     }
   ]
   ```
2. **Frontend.** Add an expandable accordion below the tile. Sort by
   `shortfall_impact` descending — highest-impact gap first. Each row links
   to the action that fixes it (e.g., "testing 0% → Generate tests" links to
   `/agents`).
3. **Daily delta.** Add a "+X vs yesterday" badge per dimension. Requires
   either a daily snapshot table (`coverage_snapshots` already exists for
   obligations — extend the pattern) or a simple in-memory cache for the
   first iteration.

---

## Fix 6 — Resolve OFAC Executive panel data gaps *(mixed)*

**Use case.**
The CCO board reviews a daily OFAC summary: screenings today, alerts today,
open cases, lifetime confirmed / false positives, SDN record count. This is
the regulator-facing "are we actually screening?" view — boards expect it to
read as "100s of screenings, zero hits" on a normal day, not "0 / 0 / 1 / 0
/ 0."

**Problem.**
Three independent issues feeding the same panel:

1. **Zero screenings today** — Nothing has hit `/api/v1/validate/wire`
   since 00:00 UTC. The screener correctly logs every call (CLEAR / REVIEW /
   HOLD alike) at `validators.py:603`; the count is honest. Either no demo
   traffic is configured, or the upstream caller is broken.
2. **1 open case, 0 lifetime resolutions** — At some point one screening
   hit REVIEW/HOLD and opened a case. Nobody has ever clicked "clear" or
   "confirm" because there's no UI to do so (`reviewer_action` is never set,
   so both `confirmed` and `false_positives` stay 0).
3. **19,133 SDN records but `last_list_update` may be NULL** —
   `dashboard_api.py:1614` reads the count from `ofac_sdn_entries` (legacy
   table, populated) but `last_list_update` at line 1618 reads from
   `ofac_sdn_vectors.last_seen_at`. Per memory `project_ofac_sdn_vector_rollout`,
   the vector rollout (migrations 021/022) is **mid-deployment** — the embed
   backfill and HNSW index aren't done. So the panel may show a healthy
   record count next to a NULL freshness field, which looks broken.

**Solution.**
Three parallel tracks:

1. **Traffic.** Confirm demo / pilot screening cadence with stakeholders. If
   it's expected to be happening, check ALB access logs +
   `validators.py` imports. If it isn't, add a tooltip ("No traffic in 24h —
   verify upstream caller") so the 0s read as informational, not alarming.

2. **Stale case.** Two paths:
   - One-off: SQL update to clear the case manually.
   - Correct: minimal review-queue page in the dashboard with single-button
     "Clear" / "Confirm" controls calling the existing case-resolution
     endpoint.

3. **Vector freshness.** Either:
   - Finish the rollout per existing action items in `CLAUDE.md` (run
     `scripts/setup/verify_sdn_prereqs.py`, then `python -m
     src.jobs.embed_sdn_variants`, then `alembic upgrade 022`), or
   - Until then, point `last_list_update` at `ofac_sdn_entries.last_updated`
     with a fallback to `ofac_sdn_vectors.last_seen_at` so the panel doesn't
     read NULL.

**Workflow.**
```sql
-- Confirm rollout state
SELECT COUNT(*), MAX(last_seen_at) FROM ofac_sdn_vectors;

-- Inspect the stale case
SELECT id, party_name, status, created_at
  FROM screening_cases
 WHERE status NOT IN ('cleared','closed');
```

---

## Sequencing recommendation

| Order | Fix | Why first / why later |
|---|---|---|
| 1 | **Fix 1** (test-coverage writeback) | The only true *bug*. Every other fix is wasted if successful agent runs don't flip the flag. |
| 2 | **Fix 5** (score transparency UI) | Land alongside Fix 1 so operators can *see* the testing dim move when the writeback starts firing. |
| 3 | **Fix 3** (CFR backfill) | Pure script, half a day, big inventory-dim win. |
| 4 | **Fix 2** (run risk scorer) | Half a day, two dimensions at once. Needs Fix 3's data to look coherent. |
| 5 | **Fix 4** (changeset sign-off) | Stakeholder decision required. Seed path is fast; UI path is the correct long-term answer. |
| 6 | **Fix 6** (OFAC panel) | Different panel; independent of the readiness score. Schedule alongside the in-flight SDN vector rollout. |

If Fixes 1–4 all land cleanly, projected score: **29 → ~72**.
