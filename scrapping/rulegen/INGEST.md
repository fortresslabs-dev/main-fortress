# INGEST.md

Instructions for Claude Code: run the BSA/AML extraction and move an
approved batch into the Fortress Postgres `rulegen` schema.

Read `CLAUDE.md` first — its invariants govern everything here. Where the
two disagree, `CLAUDE.md` wins.

---

## The one thing to understand before starting

`public.compliance_rules` serves the live dashboard right now, with ~284
rows of the legacy hardcoded corpus. **rulegen does not write there.** It
writes to the `rulegen` schema, an SME reviews, and a promote function
copies approved rules across one at a time.

Rationale: writing several hundred unreviewed rules into the live table —
even flagged `is_active=false` — means any query that forgets the filter
shows them to a customer. A separate schema makes forgetting impossible
and makes a bad run a `DROP SCHEMA` rather than a delete-by-predicate.

```
extract → rulegen.rules (route: auto_promote | review | block)
        → SME reviews via rulegen.v_review_queue
        → rulegen.promote() → public.compliance_rules
```

---

## Blockers

### B1 · The obligation grouping defect — FIXED

`rulegen/pipeline.py` used to store `obligation` as a string field on each
Rule; N rules produced N obligations, the 1:1 collapse this project
exists to prevent. Now fixed: `extract()` returns
`SectionRecord.obligations` and `SectionRecord.rules` as separate lists,
keyed by `obligation_key(citation, normalised_text)`
(`rulegen/pipeline.py:49`).

Verify after an ingest:

```sql
SELECT * FROM rulegen.v_decomposition_health;
```

If `avg_rules_per_obligation` is 1.00 across the board, the fix
regressed. Expect roughly 2.0–2.5.

### B2 · Provenance writes in the main scheduler — STILL OPEN

`src/services/scraper_scheduler.py` writes 20 of 47 `ComplianceRule`
columns and never writes `authority_type` or `citation_verified`.
`authority_gate.may_assert_as_law()` needs both.

This does not block the `rulegen` schema, but it does block anything
reaching `public` through the normal scraper path.
`src/services/update_service.py` lines 508–530 do it correctly — mirror
them into both branches of the scheduler upsert. ~30 lines.

### B3 · Citation forms — FIXED

`rulegen/ecfr.py` now parses four citation shapes — section, appendix,
part, chapter — so `12 CFR 748 App A`, `12 CFR 748 App B`,
`31 CFR Chapter V`, and `31 CFR Part 510` all resolve. See
`rulegen/ecfr.py:25-29` for the regexes and `fetch_section()` for the
dispatch. A wrong citation raises `ValueError`/`LookupError` with the
input echoed, so it stands out in a batch of hundreds.

---

## Run order

```bash
# 0. verify offline
python tests/test_all.py                    # expect 53/53

# 1. schema (alembic — migration 048 owns the rulegen schema)
python -m alembic upgrade head
python -m alembic current                   # should show 048 (or later)
psql "$DATABASE_URL" -c "\dt rulegen.*"     # 6 tables expected

# 2. snapshot production before touching it
psql "$DATABASE_URL" -c "
  CREATE TABLE IF NOT EXISTS compliance_rules_backup_$(date +%Y%m%d)
  AS SELECT *, now() AS backed_up_at FROM public.compliance_rules;"

# 3. prompt config
python batch_run.py check                   # must print 'config ok'

# 4. golden batch — known answers
export ANTHROPIC_API_KEY=...
python batch_run.py run golden
python batch_run.py evaluate                # exits 1 on failure

# 5. ingest that batch and inspect (add --dry-run first if paranoid)
python ingest.py --batch golden --prompt-version bsa_aml.v1
psql "$DATABASE_URL" -c "SELECT * FROM rulegen.v_decomposition_health;"
psql "$DATABASE_URL" -c "
  SELECT batch_key, recall, verdict, sections, rules
    FROM rulegen.runs ORDER BY finished_at DESC LIMIT 5;"

# 6. only if step 4 and 5 both look right, continue
python batch_run.py run funds_transfer \
  && python ingest.py --batch funds_transfer --prompt-version bsa_aml.v1
```

**Do not run all batches in one go.** The gate between them is the point:
a prompt regression caught on three sections costs three sections, caught
on fifty it costs fifty, and SME review time is the expensive resource.

`schema/ingest.sql` is kept as the readable source-of-truth for the
staging schema; migration 048 (`alembic/versions/048_rulegen_schema.py`)
is the authoritative applier. Do not run the raw `.sql` against a
database that already has migration 048 applied — the migration is
idempotent, the raw SQL is not.

---

## What `ingest.py` does

Shipped in commit `8534f5a`. Source: `scrapping/rulegen/ingest.py`.

1. Reads extracted sections and rules from the SQLite queue for one
   batch (`rulegen/batches.py` for keys).
2. Upserts `rulegen.regulations`, then `rulegen.cfr_sections`. On a
   changed `content_hash`, inserts a new section row and sets
   `superseded_at` on the old one. **Never updates section text in
   place** — history is what makes the nightly diff meaningful.
3. Upserts `rulegen.obligations`, grouped per B1 (`obligation_id` from
   the queue is `obligation_key(citation, normalised_text)`).
4. Inserts `rulegen.rules` with `span_verified` **recomputed locally**
   via `mechanical.span_present()` against the stored `section_text` —
   never trusted from the model.
5. If a LAW rule's span is not verified at ingest, downgrades to
   `authority_type='GUIDANCE'`, `route='block'`, and appends a reason
   line — so `ck_law_span_verified` holds and the rest of the section
   still lands. The SME still sees it in `v_review_queue`.
6. Writes a `rulegen.runs` row with `prompt_version`, `sections`,
   `rules`, `model_calls`, `recall`, and `verdict`. Recall + verdict
   come from `evaluate.score_run` against the shipped prompt's
   `evidence_vocabulary`, but only when the batch overlaps
   `golden.SECTIONS`; otherwise the row still lands with
   `recall=NULL, verdict='promote'` and a note in the summary.
7. Prints `v_decomposition_health` for the batch's citations.

**Transaction boundary: one section, one transaction.** A section is
fully ingested or not at all, so a killed run resumes cleanly and a
poison section fails alone rather than tainting the batch.

`--dry-run` runs everything but ROLLBACKs each section — use it once
against prod before the first real load of a new batch.

---

## Review and promotion

```sql
-- the SME worklist: blocks first, then Critical reviews
SELECT rule_id, cfr_citation, route, criticality, rule_logic,
       evidence_source, source_span, reasons
  FROM rulegen.v_review_queue LIMIT 30;

-- after a human has checked them
UPDATE rulegen.rules
   SET reviewed = TRUE, reviewed_by = 'jamie', reviewed_at = now()
 WHERE rule_id IN ('R-1020.210-002-01', '...');

-- promote reviewed, non-blocked rules
SELECT * FROM rulegen.promote(50);
```

`promote()` sets `is_active = true` only for rules routed
`auto_promote`. Reviewed-but-flagged rules land inactive with
`needs_review = true`, so they are visible in context without reaching a
customer. Blocked rules never promote — enforced by both the function
and a trigger (`trg_no_blocked_promotion`).

---

## Checks after every ingest

```sql
-- 1. decomposition is not collapsing to 1:1
SELECT * FROM rulegen.v_decomposition_health;

-- 2. every LAW rule is grounded (should return zero rows)
SELECT rule_id FROM rulegen.rules
 WHERE authority_type = 'LAW' AND span_verified = FALSE;

-- 3. evidence is a bank artifact, never a citation (zero rows)
SELECT rule_id, evidence_source FROM rulegen.rules
 WHERE evidence_source ~ '\d{1,4}\.\d+|CFR';

-- 4. Chapter X rules must cover credit unions unless carved out
SELECT rule_id, cfr_citation FROM rulegen.rules
 WHERE cfr_citation LIKE '31 CFR 10%' AND applies_credit_union = FALSE;

-- 5. the four values that have been wrong in production
SELECT rule_id, threshold_value, threshold_unit, cfr_citation
  FROM rulegen.rules
 WHERE (cfr_citation LIKE '%1010.410%' AND threshold_unit = 'usd'
        AND threshold_value = 10000)
    OR (cfr_citation LIKE '%1010.230%' AND threshold_value = 0.20)
    OR (cfr_citation LIKE '%1020.320%' AND threshold_unit = 'calendar_days'
        AND threshold_value = 90)
    OR rule_logic ~* 'dual[- ](approval|authorization)';
```

Query 5 must return zero rows. Each of those four values has shipped
wrong before, once in code written to prevent it.

---

## Nightly, after the corpus is loaded

`pipeline.recheck()` has the logic; nothing schedules it. Build
`nightly.py`:

```
for each section where superseded_at IS NULL:
    fetch → compare content_hash
      same      → done. zero model calls.
      different → insert new section row, supersede the old
                  for each attached rule: is source_span still present?
                     present → surviving
                     absent  → broken
                  INSERT rulegen.change_records
                  STOP
```

**The nightly job never writes a rule.** A job that can rewrite a rule
from scraped text is how a fabricated wire dual-approval obligation
entered a production compliance corpus.

---

## Rollback

```sql
-- staging: throw the whole run away (safe — nothing customer-facing lives here)
DROP SCHEMA rulegen CASCADE;
-- then re-apply migration 048
--   python -m alembic upgrade head

-- production: flip generations, do not delete
UPDATE public.compliance_rules SET is_active = false
 WHERE corpus_generation = 'rulegen_2026_08';
UPDATE public.compliance_rules SET is_active = true
 WHERE corpus_generation = 'legacy_hardcoded';
```

Legacy rows are never deleted. Retire them per connector, and only after
that connector's scraped replacement is verified green — deleting in
bulk drops coverage to zero on a live system between two commits.

---

## Verified against scratch Postgres (2026-08-17)

- `v_decomposition_health` returns non-1:1 ratios (2.00 / 4.50 / 5.00
  across the golden batch) — B1 fix holds.
- `ck_law_span_verified` fires on a manual bad insert.
- Query 5 (never-again values) returns 0 rows.
- `rulegen.runs` row landed with `recall=0.824 verdict=hold` for the
  mechanical-only queue baseline — evaluate gate holds correctly at
  ~54% baseline recall.

Prod ingest still needs its own dry-run against Aurora before the first
real batch lands.
