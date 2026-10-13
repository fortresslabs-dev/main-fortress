# Scraper extraction architecture — LLM + Pydantic + tool_use

Sibling doc to `scraper-architecture.md`. That doc covers **where data goes**
(Aurora, Bedrock embed, S3, MCP scrape monitor). This one covers **how
structured fields are extracted from unstructured prose** — the layer
between "we have HTML/PDF/XML" and "we have rows ready to upsert."

## Why this exists

A real P0 bug, 2026-06-02:

> Dashboard enforcement tab showed a CFPB action against Capital One with
> `penalty_amount_usd = $480,000,000,000`. The violation text said:
> *"a national bank with over $480 billion in assets"* — the bank's asset
> size, not the fine.

Root cause: `src/services/enforcement_actions.py::_parse_penalty_usd` was a
greedy regex (`\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?`) +
`max(finditer())`. It picked the largest dollar figure in the document,
no matter what it referred to. P2's `/sync/enforcement-actions` pulled it
verbatim and the dashboard rendered it as a fine.

That single failure mode generalised: an audit (see commit log on
`feature/p1-scrapping-enhancement`) found **10 ranked extraction paths**
that can corrupt the DB the same way — penalty parsing (P0), OFAC fuzzy
threshold, obligation type classification, dollar threshold extraction,
action-type inference, etc.

User constraint that drove the redesign: *"one wrong data can make this
product hell."*

## Architecture — two tiers

```
        ┌───────────────────────────────────────────────────────┐
        │ Raw doc (HTML / PDF / XML / XLSX, from connector)     │
        └────────────────────────┬──────────────────────────────┘
                                 │
        ┌────────────────────────▼──────────────────────────────┐
        │ Tier A — Deterministic                                │
        │   • OFAC XML structure walk                           │
        │   • CFR / USC citation regex                          │
        │   • PDF→text (pdfplumber → unstructured → OCR)        │
        │   • XLSX/CSV via pandas + Pydantic schema             │
        │   • Hardcoded fallbacks for CF-gated FFIEC hosts      │
        │                                                       │
        │ Outputs raw text + any structured fields it can       │
        │ guarantee correct (XML attributes, well-formed dates).│
        └────────────────────────┬──────────────────────────────┘
                                 │ raw text + Tier-A fields
        ┌────────────────────────▼──────────────────────────────┐
        │ Tier B — LLM + Pydantic (this doc)                    │
        │   src/extraction/bedrock_extractor.py                 │
        │   src/extraction/penalty.py (per-entity prompt)       │
        │                                                       │
        │   Bedrock Claude (Haiku 4.5 default) via tool_use:    │
        │   tool_choice forces ONE call to record_extraction    │
        │   with a JSON Schema derived from the Pydantic model. │
        │                                                       │
        │   Output validated through Pydantic; cap/range        │
        │   violations raise and the caller falls back to       │
        │   "no value" (NOT a regex guess) by default.          │
        └────────────────────────┬──────────────────────────────┘
                                 │ ExtractionResult
        ┌────────────────────────▼──────────────────────────────┐
        │ Pydantic validation gate (in the schema itself)       │
        │   • amount_usd ∈ [0, $50B]                            │
        │   • confidence ∈ [0, 1]                               │
        │   • is_penalty_not_asset enforced semantically        │
        │   • Caller drops result when confidence < 0.5         │
        └────────────────────────┬──────────────────────────────┘
                                 │
                          SQLAlchemy upsert
```

The principle: **keep what's already deterministic, replace what's not.**
LLM-on-everything is slow (~1–3 s/call), expensive, and undermines the
guarantees we already have for OFAC XML / CFR citation parsing.

## Pydantic ExtractionResult contract

`src/extraction/models.py`

Every Tier-B extraction returns an `ExtractionResult` subclass carrying:

| Field | Why |
|---|---|
| `confidence: float ∈ [0, 1]` | Caller can gate on confidence (< 0.5 → store None). |
| `extraction_method: enum {llm, regex, hardcoded, mock}` | Audits + eval splits scores by method. |
| `source_span: str` | Verbatim sentence the value came from — re-readable for review. |
| `reasoning: str` | One-line explanation; especially important for null cases. |

`PenaltyExtraction` adds:

| Field | Validator |
|---|---|
| `amount_usd: Optional[Decimal]` | `>= 0`; `<= MAX_PLAUSIBLE_PENALTY_USD ($50B)` |
| `is_penalty_not_asset: bool` | When `False`, `amount_usd` MUST be `None`. |

`MAX_PLAUSIBLE_PENALTY_USD = $50B` because the largest banking penalty
ever recorded is BNP Paribas ($8.97B, 2014). Anything above $50B is
prima-facie an asset/revenue figure misclassified as a fine.

## Bedrock extractor — tool_use, not JSON mode

`src/extraction/bedrock_extractor.py`

```python
result: PenaltyExtraction = await get_extractor().extract(
    schema_cls=PenaltyExtraction,
    text=violation_summary,
    instructions=PENALTY_INSTRUCTIONS,
    examples=PENALTY_FEW_SHOT,
)
```

Why tool_use over JSON mode:

| | JSON mode | Tool_use (chosen) |
|---|---|---|
| Schema enforcement | Soft (system prompt instruction) | Hard (Bedrock validates against `input_schema`) |
| Markdown fence leakage | ~5% of responses | 0% — tool calls have no prose |
| Multi-field type safety | Manual JSON parse + Pydantic | Pydantic-first by design |
| Cost | Same | Same |
| Latency | Same | Same |

Implementation detail: `extra="forbid"` on the Pydantic model emits
`additionalProperties: false` in the derived JSON Schema, so Claude
cannot smuggle in fields. The internal `extraction_method` is stripped
from the tool schema (the LLM should not pick its own provenance) and
set to `ExtractionMethod.LLM` server-side after validation.

### Model defaults

Resolution order (matches the existing `BedrockService` convention):

1. Explicit `model_id=` arg
2. `settings.P1_OBLIGATION_EXTRACTION_MODEL` (Haiku 4.5)
3. `settings.BEDROCK_MODEL` (Sonnet 4.5)
4. Boto built-in default

Haiku 4.5 is the right default for single-field extraction — ~3× cheaper
than Sonnet, ~2 s latency, accuracy within 1–2 pp on classification on
the golden set. Sonnet is reserved for multi-step reasoning over long
documents.

All model IDs must use the `us.anthropic.<model>-v1:0` inference-profile
prefix (see memory `feedback_bedrock_inference_profile_prefix.md`).

## Penalty extractor — prompt design

`src/extraction/penalty.py`

The instructions:

1. **Enumerate what counts** (CMP, fine, restitution, disgorgement,
   forfeiture ordered by a regulator).
2. **Enumerate what does NOT count** (total assets, deposits, revenue,
   market cap, holding-company size, transaction volume, consumer
   overcharge claims, etc.).
3. **Sum rule**: multiple penalty figures (`$1.5M CMP + $300K
   restitution`) → return total.
4. **Null rule**: action discussed but no monetary penalty (consent
   order without CMP, prohibition order, formal agreement) → return
   `amount_usd=null` with `is_penalty_not_asset=true`.
5. **Unit rule**: return plain USD (`$1.5 million` → `1500000`).
6. **Source span**: verbatim quote from the document — no paraphrase.
7. **Confidence rubric**: 0.95+ for explicit "ordered to pay $N",
   0.7–0.9 for implied, < 0.5 for ambiguous (caller drops at < 0.5).

Three few-shot examples are included:

| Example | Tests |
|---|---|
| Capital One filing | Asset-size distractor (`$480B in assets` → `null`) |
| OCC XYZ Bank | Clean CMP with `$12B in assets` distractor |
| Fed formal agreement | Explicit no-penalty case |

## Golden set + eval harness

```
tests/golden/extraction/enforcement_penalty.jsonl     # 12 cases
scripts/eval_extraction.py
```

JSONL schema per case:

```json
{
  "id": "capital_one_assets_trap_2025",
  "input": "...prose...",
  "expected_amount_usd": null,
  "expected_is_penalty_not_asset": false,
  "notes": "The original $480B bug."
}
```

The 12 cases cover: asset distractor, deposit distractor, revenue
distractor, market-cap distractor, restitution-sum, disgorgement-only,
formal agreement no-penalty, prohibition order, proposed settlement
with no final amount, lawsuit-only overcharge claim, etc.

### Make targets

```bash
make extraction-eval                  # run all suites, exit non-zero on regression
DRY_RUN=1 make extraction-backfill-penalties   # preview backfill
DRY_RUN=0 make extraction-backfill-penalties   # apply
```

`eval_extraction.py` forces `LLM_MOCK_MODE=false` internally — mock mode
returns `None` for everything and would give a misleading 50% pass rate.

CI wiring (not yet enabled in `.github/workflows/ci-cd.yml`): add a job
that runs `make extraction-eval` with Bedrock credentials provisioned
via OIDC role assumption. Default `--min-accuracy 0.80`; tighten after
the first baseline run lands.

## Backfill — fixing existing bad rows

`scripts/backfill_enforcement_penalties.py`

Selects rows where `penalty_amount_usd > $1B`. Justification: real
banking penalties above $1B are single digits per year; anything above
$1B in our table that isn't a known mega-fine (BNP, Wells Fargo, etc.)
is almost certainly the asset-confusion bug.

For each suspect row:

1. Read `violation_summary`.
2. Call `extract_penalty(text)` against live Bedrock.
3. If confidence ≥ 0.5 and `amount_usd` validates → write back.
4. Else (low confidence, null amount, validation error) → write `None`.
5. Log every change with old/new amount, confidence, source_span,
   reasoning.

`DRY_RUN=1` is the Makefile default — operators preview first.

ECS RunTask form (same shape as `backfill_ofac_sdn_vectors.py`):

```bash
aws ecs run-task \
  --cluster fortress-pipeline1-production \
  --task-definition fortress-pipeline1-production \
  --launch-type FARGATE --network-configuration ... \
  --overrides '{"containerOverrides":[{"name":"pipeline1",
      "command":["python","-m","scripts.backfill_enforcement_penalties"],
      "environment":[{"name":"DRY_RUN","value":"0"}]}]}'
```

## Operational runbook — when a regression hits

Symptom: an enforcement row has a wrong penalty (or an obligation has a
wrong classification, once that extractor lands).

1. Grep CloudWatch for `penalty_extract_ok` or `penalty_extract_no_penalty`
   with the offending `action_ref`. Pull `source_span`, `reasoning`,
   `confidence`. That tells you whether the LLM or the regex fallback
   produced the value.
2. If LLM produced it: copy the input prose into the JSONL golden set
   with the correct expected value. Run `make extraction-eval` — it will
   fail. Tune the prompt (add a clarifying instruction or another
   few-shot example) until it passes.
3. If regex fallback produced it (logged as
   `penalty_extract_llm_failed_fallback_regex`): investigate why the LLM
   path failed (validation cap? invoke error?). Bedrock quota or
   inference-profile drift are the usual suspects.
4. Re-run backfill on the affected `regulator` window to repair the row.

## What this branch does NOT yet cover

Tier-B extraction has been built for **one** entity (penalty amounts).
The other nine items in the audit are still on regex/heuristics:

| Audit # | Path | Status |
|---|---|---|
| 1 | `_parse_penalty_usd` | ✅ replaced (this branch) |
| 2 | OFAC fuzzy threshold (`0.85` hardcoded) | open |
| 3 | Obligation type classification (negation bug) | open |
| 4 | OFAC XML namespace brittleness | open |
| 5 | PDF OCR fallback threshold | open |
| 6 | Dollar threshold extraction in rule resolver | open |
| 7 | Enforcement action type inference | open |
| 8 | Institution type substring match | open |
| 9 | Sentence splitting regex | open |
| 10 | PDF table extraction (multi-column collapse) | open |

Each follows the same pattern: new `Foo Extraction(ExtractionResult)`
schema in `src/extraction/models.py`, prompt + few-shot in
`src/extraction/<entity>.py`, replace caller, add golden suite, wire
Make target.

Other roadmap items:

- **Redis caching** by `(content_hash, prompt_version, model_id)`.
  Today every backfill / re-run hits Bedrock. Redis is already in the
  stack; adding a cache layer to `BedrockExtractor.extract` is ~20 LOC.
- **MCP `extraction_validator` server** so both the scraper and the
  agent pipeline call the same validator (single source of truth) and
  can diff against the prior version of a doc.
- **Persist provenance.** Today `extraction_method` / `confidence` /
  `source_span` live in CloudWatch logs only. An `extraction_audit`
  table keyed by `(table_name, row_id, field_name)` would let the CCO
  dashboard surface "this $5M penalty was extracted by LLM at
  confidence 0.93 from this sentence."
- **CI gate.** `.github/workflows/ci-cd.yml` should run
  `make extraction-eval` against live Bedrock with a sentinel
  threshold. Currently dev-machine only.

## Code map

```
src/extraction/
├── __init__.py                  # exports ExtractionResult, PenaltyExtraction, ExtractionMethod
├── models.py                    # Pydantic schemas + $50B cap validator
├── bedrock_extractor.py         # generic extract(); tool_use; LLM_MOCK_MODE path
└── penalty.py                   # penalty prompt + 3 few-shot examples

src/services/enforcement_actions.py
  └── _extract_penalty()         # LLM-first, regex fallback, confidence gate

scripts/
├── eval_extraction.py           # golden-set runner, --min-accuracy gate
└── backfill_enforcement_penalties.py   # > $1B suspect re-extract, DRY_RUN default

tests/golden/extraction/
└── enforcement_penalty.jsonl    # 12 cases — asset/deposit/revenue distractors etc.

Makefile
  └── extraction-eval, extraction-backfill-penalties
```
