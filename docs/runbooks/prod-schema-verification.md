# Runbook — Production schema verification (before shipping the dashboard/test-gen fixes)

Local validation surfaced **schema drift**: several columns, indexes, and enum
labels the code expects were missing or mismatched in the local DBs and had to
be added/renamed by hand. Before deploying branch `code_cleanup` to a bank's
**P2** (and to **P1**), verify these objects exist and match — otherwise the
affected features 500 or silently degrade exactly as they did locally.

Run the checks on **P1's Aurora and every bank's P2 Aurora**. Items are ordered
by blast radius. Most remediations are `alembic upgrade head`; a few are direct
`ALTER`s for objects a migration didn't carry locally.

---

## 0. One-shot verification query (run on each DB)

```sql
-- 1. Workflow enum labels must be LOWERCASE (migration 016 canon)
SELECT 'workflowstatus_labels' AS check,
       CASE WHEN enum_range(NULL::workflowstatus)::text
                 = '{pending,running,waiting_approval,completed,failed}'
            THEN 'PASS' ELSE 'FAIL → '||enum_range(NULL::workflowstatus)::text END AS result
UNION ALL
SELECT 'checkpointstatus_labels',
       CASE WHEN enum_range(NULL::checkpointstatus)::text = '{pending,approved,rejected}'
            THEN 'PASS' ELSE 'FAIL → '||enum_range(NULL::checkpointstatus)::text END
UNION ALL
-- 2. obligations.embedding (migration 052)
SELECT 'obligations.embedding',
       CASE WHEN to_regclass('public.obligations') IS NULL THEN 'N/A (P2-only table)'
            WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='obligations' AND column_name='embedding')
            THEN 'PASS' ELSE 'FAIL → add via migration 052' END
UNION ALL
SELECT 'obligations.embedding_hnsw_index',
       CASE WHEN to_regclass('public.idx_obligations_embedding_hnsw') IS NOT NULL
            THEN 'PASS' ELSE 'WARN → index missing (migration 052)' END
UNION ALL
-- 3. regulatory_change_records columns the sign-off UPDATE writes
SELECT 'rcr.client_id',
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='regulatory_change_records' AND column_name='client_id')
            THEN 'PASS' ELSE 'FAIL → sign-off will 500' END
UNION ALL
SELECT 'rcr.updated_at',
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='regulatory_change_records' AND column_name='updated_at')
            THEN 'PASS' ELSE 'FAIL → sign-off will 500' END
UNION ALL
-- 4. compliance_rules enrichment columns (retrieval + ranking + risk)
SELECT 'compliance_rules.'||c,
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='compliance_rules' AND column_name=c)
            THEN 'PASS' ELSE 'FAIL' END
FROM unnest(ARRAY['embedding','tsv','severity','enforcement_risk_score','cfr_citation','domain']) c
UNION ALL
-- 5. Readiness / Reports / Findings tables present (migrations 042–045)
SELECT 'table:'||t,
       CASE WHEN to_regclass('public.'||t) IS NOT NULL THEN 'PASS' ELSE 'FAIL (P2)' END
FROM unnest(ARRAY['bank_profile','applicable_obligation_registry','control_obligation_mappings',
                  'governance_evidence','findings','export_artifacts','export_signoffs',
                  'controls','bank_officers']) t
ORDER BY 1;
```

Every row should read **PASS** (HNSW index may read WARN on a fresh DB until the
embedding backfill runs). Any **FAIL** → see the matching item below.

---

## 1. Workflow enum labels — must be lowercase  🔴 blocks test-gen + dashboard
- **Expectation:** `workflowstatus = {pending,running,waiting_approval,completed,failed}`, `checkpointstatus = {pending,approved,rejected}` (migration 016).
- **Symptom if wrong:** either the 6-agent workflow INSERT (`'running'`) or the dashboard status queries 500 with `InvalidTextRepresentationError` — depending on which case the labels are in.
- **Fix if UPPERCASE:**
  ```sql
  ALTER TYPE workflowstatus  RENAME VALUE 'PENDING' TO 'pending';   -- + RUNNING/WAITING_APPROVAL/COMPLETED/FAILED
  ALTER TYPE checkpointstatus RENAME VALUE 'PENDING' TO 'pending';  -- + APPROVED/REJECTED
  ```
  Coordinate with the deploy — the committed code is lowercase, so DB + code must flip together.

## 2. `obligations.embedding` (+ HNSW index) — migration 052  🟠 obligation retrieval
- **Expectation:** `vector(1024)` column + `idx_obligations_embedding_hnsw`.
- **Symptom:** Agent-4 "related obligations" context + obligation-level retrieval unavailable.
- **Fix:** `alembic upgrade head` (applies **052**), then backfill (`docs/runbooks/embedding-backfill.md`).

## 3. `regulatory_change_records.client_id` + `updated_at`  🔴 blocks Changes sign-off
- **Expectation:** both columns exist (`client_id varchar`, `updated_at timestamptz`). The sign-off UPDATE writes them.
- **Symptom:** every Changes sign-off 500s.
- **Fix:**
  ```sql
  ALTER TABLE regulatory_change_records ADD COLUMN IF NOT EXISTS client_id varchar(80);
  ALTER TABLE regulatory_change_records ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();
  ```
  Confirm the owning migration includes these; if not, add a migration so fresh DBs get them.
- **Note:** the separate sign-off `AmbiguousParameterError` was a *code* bug — already fixed on the branch (`c1042f2`); it ships with the deploy.

## 4. `compliance_rules` enrichment columns  🟠 retrieval / ranking / risk
- **Expectation:** `embedding vector(1024)`, `tsv`, `severity`, `enforcement_risk_score`, `cfr_citation`, `domain` (migrations 013/023/051).
- **Symptom:** hybrid search, severity ranking, or the Risk view degrade.
- **Fix:** `alembic upgrade head`; then embedding backfill (runbook) for coverage. `domain` is typically NULL (known — see completion_report); affects validator threshold precision, not the dashboard.

## 5. Readiness / Reports / Findings tables — migrations 042–045  🟠 P2 views
- **Expectation (P2):** `bank_profile`, `applicable_obligation_registry`, `control_obligation_mappings`, `governance_evidence`, `findings`, `export_artifacts`, `export_signoffs`, `controls`, `bank_officers`.
- **Symptom:** readiness dimensions fall back/exclude; Reports/Findings empty or error.
- **Fix:** `alembic upgrade head` on each P2.

## 6. `control_rule_mappings` seeder drift  🟡 demo tooling only
- **Observed:** `scripts/seed_testbank_dashboard._seed_control_rule_mappings` referenced a column (`regulation_name`) that doesn't exist in the current schema → the local demo seeder errored on that section.
- **Impact:** **demo seeding only** — not a dashboard runtime path (prod banks populate controls via their own workflows). Reconcile the seeder with the live `control_rule_mappings` / `compliance_rules` schema before relying on it for demos.

---

## Remediation summary
| Item | Severity | Fix |
|---|---|---|
| Enum labels uppercase | 🔴 | `ALTER TYPE … RENAME VALUE` (coordinate w/ deploy) |
| `obligations.embedding` | 🟠 | `alembic upgrade head` (052) + backfill |
| `rcr.client_id` / `updated_at` | 🔴 | `ALTER TABLE … ADD COLUMN` (+ ensure a migration carries them) |
| `compliance_rules` enrichment cols | 🟠 | `alembic upgrade head` + embedding backfill |
| Readiness/Reports/Findings tables | 🟠 | `alembic upgrade head` |
| `control_rule_mappings` seeder | 🟡 | reconcile demo seeder (non-runtime) |

**Related runbooks:** `docs/runbooks/prod-rollout-testgen.md` (full rollout),
`docs/runbooks/embedding-backfill.md` (embeddings), `completion_report.md` (status).
Run this verification **first** in the rollout, before the migration/backfill steps.
