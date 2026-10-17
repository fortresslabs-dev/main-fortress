# Runbook — Production rollout: test-generation accuracy + obligation embeddings

Ships the test-generation correctness work to production: the dashboard enum-case
fix, Fix A (trigger-rule obligation coverage), item 1 (Agent 4 grounding on the
rule's obligations + authoritative thresholds), and item 2 (per-obligation
embeddings, migration 052). All application code changes are **P2-only**; P1
needs only the migration + an embeddings backfill.

Source branch: `code_cleanup`. Key code commits: `7dcc5be` (dashboard enum fix),
`21dbb42` (Fix A), `2ab9c17` (item 1 + item 2 + migration 052),
`097df4c` (`scripts/backfill_embeddings.py`).

> **Verified sync fact (do not skip):** `/api/sync/obligations` serializes
> `obligation_id, regulation_id, source_text, …` but **NOT `embedding`**
> (sync_api.py). There is a dedicated `/api/sync/embeddings` for
> `compliance_rules` only. So **obligation embeddings do not reach P2 via sync**
> — they must be backfilled **on each P2 directly** (step 3c).

---

## 0 · Pre-flight
- [ ] Open PR `code_cleanup → main`; review the four code commits above.
- [ ] Snapshot/back up prod DBs: P1 Aurora **and every bank's P2** Aurora before any change.
- [ ] Confirm prod secrets live in SSM / Secrets Manager (not `.env`): valid `GITHUB_TOKEN`, `JIRA_ISSUE_TYPE` per project (`Idea` for MDP), and `bedrock:InvokeModel` on `amazon.titan-embed-text-v2:0` for the **P1 and P2** task roles.

## 1 · ⚠️ Enum-case coupling — settle this before/with the deploy
The committed `dashboard_api.py` uses **lowercase** workflow-status literals. It
is correct only if the DB enum labels are lowercase (what migration 016 defines).
```sql
SELECT enum_range(NULL::workflowstatus), enum_range(NULL::checkpointstatus);
```
Run on P1 and each P2.
- [ ] **If lowercase** → the old code was already 500-ing those panels; the fix is a pure improvement. Deploy freely.
- [ ] **If UPPERCASE** → rename to lowercase in the **same release** as the deploy (DB rename first, then roll the image):
  ```sql
  ALTER TYPE workflowstatus  RENAME VALUE 'PENDING' TO 'pending';   -- + RUNNING, WAITING_APPROVAL, COMPLETED, FAILED
  ALTER TYPE checkpointstatus RENAME VALUE 'PENDING' TO 'pending';  -- + APPROVED, REJECTED
  ```
  A code/DB case mismatch breaks the dashboard exactly as observed in local dev.

## 2 · Schema migration (P1 + each P2)
- [ ] `alembic upgrade head` → applies **052** (`obligations.embedding vector(1024)` + `idx_obligations_embedding_hnsw`). Nullable/additive, safe.
- [ ] Confirm `alembic current` = `052` on every DB.

## 3 · Embeddings backfill (Titan v2; idempotent — only NULL rows)
- [ ] **3a · P1 — rules + obligations:** `python -m scripts.backfill_embeddings --target both`
- [ ] **3b · compliance_rules → P2:** propagates on the next P2 sync via `/api/sync/embeddings` (or trigger the sync). Verify count after.
- [ ] **3c · obligations → each P2 (REQUIRED — not synced):** run **on every P2**:
  `python -m scripts.backfill_embeddings --target obligations`
  (P2 has Titan access; this is the only way obligation vectors land in P2 until/unless the obligations sync is extended to carry `embedding`.)

> Optional durable improvement (separate change): extend `/api/sync/obligations`
> + `update_service._sync_obligations` to carry `embedding::text` (mirroring
> `/api/sync/embeddings`) so 3c becomes automatic. Not required for this rollout.

## 4 · Deploy code (P2 only)
- [ ] Build + push the **P2 image** so Fix A + item 1 + the dashboard fix are **baked in** — prod must not rely on the local `docker-compose.override.yml` bind-mounts:
  `make build-p2 && make push-p2 BANK_ID=<bank>`
- [ ] Rolling deploy each bank's P2: `make deploy-p2-bank BANK_ID=<bank>`
- [ ] P1 image is unaffected (all code changes are P2-side); P1 needed only steps 2 + 3a.

## 5 · Verify
- [ ] `SELECT count(*) FROM compliance_rules WHERE embedding IS NULL AND is_active;` = **0** on P1 + each P2.
- [ ] `SELECT count(*) FROM obligations WHERE embedding IS NULL AND is_active;` = **0** on P1 + each P2.
- [ ] `enum_range` lowercase on all DBs.
- [ ] Dashboard **Rules** panel returns rows (200); coverage / readiness panels load.
- [ ] Generate-test on one rule → JIRA story (`Idea`) → workflow completes → PR opens → the rule's own obligation flips to `has_test_coverage = true`.
- [ ] (Optional) run the golden-set harness against a prod-like replica for a coverage baseline: `python -m scripts.eval.generate_test_goldenset --rules <ids>`.

## 6 · Rollback
- [ ] Code: redeploy the previous P2 image.
- [ ] Migration: `alembic downgrade -1` (drops `obligations.embedding` + index — additive, clean).
- [ ] Enum: rename back **only together with** redeploying the old (uppercase) image — never leave code/DB case mismatched.

---

## Not shipping to prod (local-dev scaffolding only)
`docker-compose.override.yml` bind-mounts, the `~/.aws` read-only mount,
`DEV_MODE` / `e2e-bypass` tokens, and the hand-applied local enum/embedding
fixes. Prod uses the baked image + SSM + task roles + the migration/backfill above.

## Appendix — `domain` is NULL on all rules (known, not a blocker)
`compliance_rules.domain` is NULL across the corpus. Impact:
- **Test generation: none.** Agent 2's domain-scoped retrieval (Path 1) is dead,
  but Path 2 (hybrid pgvector + BM25) + Fix A (trigger-rule injection) fully
  cover it — verified by the golden set.
- **Runtime validators: mild degradation.** `compliance_rule_resolver` Tier 1
  (`WHERE domain = …`, used by WireTransferValidator / LoanApplicationValidator /
  OFACScreener) misses and falls back to `_FALLBACK_THRESHOLDS`. Standard
  thresholds still resolve; DB-stored custom/overridden thresholds won't be
  picked up via the domain path, and rules aren't flagged as DB-sourced.
- **Recommendation:** backfill `domain` as a medium-priority follow-up to restore
  validator precision/auditability — independent of this rollout.
