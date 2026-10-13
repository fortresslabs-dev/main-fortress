# OFAC SDN Vector Screener — Calibration Gold-Set Runbook

**Owner:** Compliance Ops, with engineering support
**Engineering contact:** P2 sync team (agent-4)
**Status:** Pre-flip gate for `OFAC_SCREENER_MODE=vector`
**Last updated:** 2026-06-04

This runbook describes how to assemble a labeled gold set, run the calibration
script, and produce per-class cosine thresholds that compliance can sign off
on before we flip OFAC screening from the legacy rapidfuzz path to the
pgvector path.

---

## 1. Why this exists

The vector screener (migrations 021 / 022) ranks counterparty names against
`ofac_sdn_vectors` using a blend of trigram similarity, token-sort fuzz, and
cosine distance over Titan-v2 embeddings. Unlike the legacy screener — which
uses a single global `OFAC_FUZZY_THRESHOLD=85` — vector cosine does **not**
behave uniformly across name classes:

| Class | Why cosine misbehaves |
|---|---|
| **individual** | Names are short (≈2 tokens). Cosine clusters tightly; even unrelated names score 0.75+. Needs a **high** threshold. |
| **entity** | Long, boilerplate-heavy ("LIMITED", "TRADING CO", "HOLDINGS"). Boilerplate inflates cosine baselines. Needs a **moderate** threshold with strong fuzz weight. |
| **vessel** | Short + IMO numbers + flag suffixes. Cosine is unreliable; trigram dominates. Needs a **low** threshold but high blended weighting. |
| **alias** | Truncated or transliterated variants of a parent name. Cosine recall matters more than precision (we want to catch them). Needs a **low** threshold. |

A single global cutoff over-blocks entities and under-blocks individuals. We
calibrate per class.

---

## 2. Gold-set schema

CSV file: `scripts/setup/data/ofac_calibration_gold.csv`

| Column | Required | Meaning |
|---|---|---|
| `name` | yes | Raw counterparty name as it would appear in a wire (NOT pre-normalized). The calibration script normalizes via `ofac_sdn_loader.normalize_name`. |
| `expected_sdn` | yes (empty for clean rows) | SDN number this name should resolve to. Empty string means this is a known-clean counterparty (true negative). |
| `variant_class` | yes | One of `individual` / `entity` / `vessel` / `alias`. Drives per-class threshold output. |
| `notes` | no | Free text — provenance, source ticket, etc. Ignored by the script but helpful for review. |

**Empty `expected_sdn` = "this is a clean counterparty"** — used to measure
false-positive rate. A good gold set is **balanced**: roughly equal
positive/clean rows per class, so precision and recall both have signal.

---

## 3. What's checked in (the synthetic seed)

`scripts/setup/data/ofac_calibration_gold.csv` ships with **200 synthetic
rows**:

| Class | Positives | Clean | Total |
|---|---|---|---|
| individual | 25 | 25 | 50 |
| entity | 25 | 25 | 50 |
| vessel | 25 | 25 | 50 |
| alias | 25 | 25 | 50 |

**Every positive row has `expected_sdn = TBD_OPS_FILL_NNN` as a placeholder.**
The calibration script will report 0% recall against this file until ops
replaces the placeholders with real SDN numbers. This is intentional — it
prevents anyone from accidentally running calibration on synthetic data and
treating the output as production thresholds.

The synthetic names are culturally-plausible but do **not** correspond to any
specific real SDN entries or real persons. They exist purely to validate the
CSV pipeline (column order, normalization round-trip, variant class taxonomy)
end to end before ops sinks time into real data entry.

---

## 4. Ops workflow — what humans need to do

**☐ Step 1: Pull real SDN names from your screening history (HUMAN VERIFY)**

The synthetic seed is structurally correct but compliance-empty. Replace it
with real data:

1. Export the last 12 months of OFAC screening events from your case
   management system (Actimize / Verafin / in-house) — both confirmed-true
   matches AND confirmed false positives that were cleared.
2. Bucket each event into one of `individual / entity / vessel / alias`.
   For aliases, the parent SDN number is what matters.
3. Aim for **at least 100 positives per class** (so the per-threshold sweep
   has signal at the granularity of ±0.01 cosine).

**☐ Step 2: Add clean counterparties (HUMAN VERIFY)**

Pull a random sample of cleared wires from production where OFAC screening
returned no hit. Aim for ~100 clean rows per class. These drive the
false-positive rate — without enough clean rows, the calibrator will pick a
threshold that's too low and over-block in prod.

If you can't easily class clean counterparties as individual/entity/vessel,
use these heuristics:
- Has a corporate suffix (LLC, LTD, GMBH, AG, S.A.) → `entity`
- Starts with `M/V`, `M/T`, `S/S`, contains "FLAG", contains an IMO number → `vessel`
- Two-or-three-token person-style name → `individual`

**☐ Step 3: Verify SDN numbers against the live list (HUMAN VERIFY)**

For each positive row, confirm `expected_sdn` matches the **current** SDN
list. SDN numbers are stable but entries do get removed (delisting).
Cross-reference against:
- OFAC Sanctions List Search: https://sanctionssearch.ofac.treas.gov/
- Or your most recent successful `python -m src.jobs.embed_sdn_variants` run

Any row whose `expected_sdn` no longer exists in `ofac_sdn_vectors` should
either be removed from the gold set or moved to the "clean" cohort (since
delisted names should NOT block).

**☐ Step 4: Replace TBD placeholders in the CSV**

Find-and-replace every `TBD_OPS_FILL_NNN` with the verified SDN number, or
delete those rows entirely and substitute with your bank-specific data from
steps 1–3. Keep the synthetic clean rows or replace them — they're generic
enough to be useful as filler.

**☐ Step 5: Hand back to engineering for dry-run**

Once the CSV is ops-verified, ping the engineering channel. Engineering will
run the calibration script against P1 Aurora and circulate the per-class
threshold table for compliance sign-off.

---

## 5. Running calibration (engineering)

Prerequisites:
- Migrations 021 and 022 applied to P1 Aurora
- `python -m src.jobs.embed_sdn_variants` has run to completion (embeddings backfilled)
- `scripts/setup/verify_sdn_prereqs.py` returns green

Run:

```bash
PIPELINE_MODE=pipeline1 \
AWS_REGION=us-east-1 \
python -m scripts.setup.calibrate_ofac_thresholds \
    --gold scripts/setup/data/ofac_calibration_gold.csv \
    --out  scripts/setup/data/ofac_thresholds.json \
    --verbose
```

Tunable env:
- `CAL_TOP_K` (default 5) — candidates considered per gold row
- `CAL_CONCURRENCY` (default 5) — in-flight Titan embed calls

Runtime estimate: ~5 minutes for 1000 rows (Titan latency dominates).

---

## 6. Interpreting the output

The script prints two sweep tables per class — one for cosine-only, one for
the blended score (0.4 trgm + 0.4 fuzz + 0.2 cosine, mirroring prod
`_vector_rerank`) — then emits the F1-optimal threshold per class.

Final block:

```
F1-OPTIMAL thresholds (suggested SSM values)
  class        score      T   prec    rec     F1
  individual   cosine   0.86  0.952  0.880  0.915
  individual   blended  0.62  0.971  0.920  0.945
  entity       cosine   0.81  0.880  0.910  0.895
  entity       blended  0.58  0.910  0.940  0.925
  ...
```

JSON output (`ofac_thresholds.json`) contains an `ssm_suggestions` block
ready to feed into Parameter Store:

```json
{
  "ssm_suggestions": {
    "/fortress/p2/ofac/threshold/individual": 0.62,
    "/fortress/p2/ofac/threshold/entity": 0.58,
    ...
  }
}
```

---

## 7. Acceptance criteria (compliance sign-off before SSM rollout)

**☐ HUMAN VERIFY** — compliance ops must agree that per-class **F1 ≥ 0.90**
on the blended score, AND **recall ≥ 0.95** for the `individual` and `alias`
classes (we tolerate higher FP rate to never miss a real hit).

If those bars aren't met:
- F1 too low → gold set is too small or mislabeled. Add rows, re-verify.
- Recall too low → embedding quality is the bottleneck. Engineering checks
  whether `embed_sdn_variants` covered all variants for the missed SDNs.
- Precision too low → cosine cutoff is too generous. Compliance can manually
  bump the chosen threshold above F1-optimum if false positives are
  operationally expensive.

---

## 8. After thresholds are approved

1. Engineering loads the approved thresholds into SSM under
   `/fortress/p2/ofac/threshold/{class}`.
2. `validators.py:OFACScreener` reads the per-class threshold on init
   (engineering work item — currently uses a single `OFAC_FUZZY_THRESHOLD`).
3. Engineering runs `scripts/ops/ofac_ab_compare.py` in shadow mode for one
   week with `OFAC_SCREENER_MODE=legacy` still flipped.
4. Weekly disagreement log goes to compliance for review.
5. Once disagreement rate stabilizes and compliance signs off on the
   delta, flip `OFAC_SCREENER_MODE=vector` (still as a canary — see
   `project_ofac_sdn_vector_rollout`).

---

## 9. Where humans are on the hook

| Step | Owner |
|---|---|
| Build the gold set from real screening history | Compliance Ops |
| Verify SDN numbers against live list | Compliance Ops |
| Replace TBD placeholders | Compliance Ops |
| Run calibration script | Engineering |
| Sign off on per-class thresholds (F1 ≥ 0.90, recall ≥ 0.95 for individual/alias) | Compliance Ops |
| Load thresholds into SSM | Engineering |
| Approve A/B disagreement delta after shadow week | Compliance Ops |
| Flip `OFAC_SCREENER_MODE=vector` | Joint (engineering executes, compliance approves) |
