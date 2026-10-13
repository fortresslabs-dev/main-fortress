# CLAUDE.md — rulegen

Guidance for Claude Code working in this repo.

## What this is

Turns CFR sections into testable compliance rules for Fortress Pipeline 1.
Discovery walks eCFR's own table of contents; a scope gate filters out what
isn't a bank duty; a model decomposes what's left; deterministic checks
overrule the model.

**The domain matters.** These rules drive compliance testing at banks and
credit unions. A wrong threshold produces a green dashboard over a real
violation nobody sees until an examination. Prefer failing loudly.

## Run

### Progressive batch extraction (the normal path)

```bash
python batch_run.py plan                     # 10 batches, 50 citations
python batch_run.py check                    # validate the prompt config
python batch_run.py run golden               # batch 1: known answers
python batch_run.py evaluate                 # score; exits 1 if it fails
python batch_run.py run funds_transfer       # only after evaluate passes
python batch_run.py status
python batch_run.py rerun "31 CFR 1020.210"  # after a prompt change
```

**The gate between batches is the point.** A prompt regression caught on
three sections costs three sections; caught on fifty it costs fifty, and SME
review time is the expensive resource. `evaluate` exits non-zero on failure so
it can gate a script.

### Prompts are config, not code

`prompts/bsa_aml.v1.yaml` holds the system prompt, few-shot examples, evidence
vocabulary and model settings. To change a prompt: copy to `v2`, edit, run the
golden batch under both, keep the winner.

`PromptConfig.validate()` rejects three changes that silently degrade
extraction — `units_per_call > 1`, `temperature > 0`, and a missing
`noun_phrase_duty` example. `batch_run.py check` runs it.

### Everything else

```bash
python tests/test_all.py            # 53 tests, no network, no key
python bootstrap.py discover        # walk eCFR into the queue
python bootstrap.py scope           # gate every section
python bootstrap.py rejects         # <-- HUMAN READS THIS BEFORE EXTRACTING
export ANTHROPIC_API_KEY=sk-ant-...
python bootstrap.py extract         # resumable; kill and re-run freely
python bootstrap.py stats
python bootstrap.py review          # prioritised SME worklist
```

## Invariants — do not break

Each corresponds to a defect that has actually shipped here.

1. **`span_present` is exact, not fuzzy.** Whitespace and case normalised,
   nothing else. It is the only hard grounding guarantee. A similarity score
   destroys it.
2. **Numbers never go to the model.** `find_numbers` is regex. The $3,000
   Travel Rule threshold has been recorded wrong four times, once by code
   written to prevent it.
3. **The scope gate leans toward keeping.** A wrongly dropped section is
   invisible; a wrongly kept one costs a few model calls.
4. **`NOUN_DUTY_STEM` runs before any language-based reject.** 1020.210's
   five pillars carry no modal verb. Reject on "no modal" and the most
   examined obligations in US banking disappear silently.
5. **One unit per model call.** Batching units degrades quality — the model
   merges adjacent units into one duty, which is the defect the pipeline
   exists to prevent.
6. **Sections commit all-or-nothing.** Half-extracted state breaks resume.
7. **The nightly job never writes a rule.** Hash drift opens a change record
   and stops.

## Two tests assert FAILURE on purpose

`test_regex_alone_underfinds_duties` and
`test_gate_would_drop_1020_210_without_the_rescue` document why the model
call and the noun-phrase rescue exist. **If either starts passing, the CFR
text changed or someone loosened the splitter** — investigate, don't "fix"
the test.

## Changes that look right and are wrong

| Tempting | Why not |
|---|---|
| "Batch units to cut model cost" | Quality drops; adjacent duties merge |
| "`span_present` is too strict, use fuzzy" | Grounding guarantee gone, fabrication undetectable |
| "Derive thresholds from rule text" | 1010.410 opens with $10,000 before reaching $3,000 |
| "Drop sections with no modal verb" | See invariant 4 |
| "Reset the queue to re-run cleanly" | Destroys `reviewed` flags — SME work lost |
| "Review every rule" | ~700 rules, ~23 hours. Blocks first, sample auto-promoted |
| "Edit the prompt in place" | Loses the ability to A/B. Copy to a new version |
| "Skip the golden batch, start with real work" | The gate only works if it runs first |
| "Add a citation to two batches" | Extracted twice, duplicate rules. One test guards this |

## Expected shape of a full run

| | Estimate |
|---|---|
| Sections discovered | ~180 |
| Survive the scope gate | ~90 |
| Model calls | ~900 |
| Rules produced | ~600–800 |
| Nightly cost after bootstrap | **0 model calls** |

If the gate keeps far more than ~90, it is too loose and the model budget
inflates. Far fewer, and it is dropping real duties — read `rejects` again.

## Evaluation gates

`evaluate.verdict()` holds a run back if any of these fire:

| Gate | Threshold | What it catches |
|---|---|---|
| recall | < 0.85 | duties being missed — usually the noun-phrase example |
| charter errors | any | Title 12 / Chapter X defaults not landing |
| over-extraction | > 20% | scaffolding promoted into rules |
| evidence in vocabulary | < 0.9 | model inventing artifacts |

All four are tested with synthetic runs, including one that performs at bare
regex level and must be held.

## Schema + writer status

- **Schema.** Owned by alembic migration 048
  (`alembic/versions/048_rulegen_schema.py`). `schema/ingest.sql` is kept
  as the readable source-of-truth but is not the applier — run
  `python -m alembic upgrade head` on any target DB.
- **Writer.** `ingest.py` moves an approved batch from the SQLite queue
  into the `rulegen` schema. One transaction per section, `span_verified`
  recomputed locally, ungrounded LAW rules downgraded to
  `GUIDANCE`/`block` so `ck_law_span_verified` holds. Writes a
  `rulegen.runs` row per batch with recall + verdict from
  `evaluate.score_run` when the batch overlaps `golden.SECTIONS`.

## Not built yet

- **Guidance layer.** ~15 BSA/AML duties are not in CFR text at all — the
  120-day continuing-activity SAR deadline, the 14-day 314(a) window, the
  OFAC 50 percent rule. No discovery finds them. Needs a hand-maintained
  file with `authority_type='GUIDANCE'`.
- **Provenance in the main scheduler (B2).** Nothing on the `rulegen`
  side, but `src/services/scraper_scheduler.py` still writes 20 of 47
  `ComplianceRule` columns and skips `authority_type` +
  `citation_verified`. Anything reaching `public.compliance_rules`
  through the normal scraper path lands advisory until that upsert is
  mirrored from `src/services/update_service.py:508-530`.
- **Nightly hash diff.** `pipeline.recheck` implements the logic; nothing
  schedules it yet. Build `nightly.py` per `INGEST.md`.

## Style

Module docstrings explain *why*. Several encode a defect history that would
otherwise be reintroduced. Prefer deterministic code over a model call
wherever a right answer exists — every model call in this pipeline is a place
a wrong answer can look right.
