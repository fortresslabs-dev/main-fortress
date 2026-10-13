"""OFAC SDN vector-cosine threshold calibration.

Run this BEFORE flipping OFAC_SCREENER_MODE=vector. Sweeps cosine cutoffs
against a labeled gold set and emits the F1-optimal threshold per
name-variant class (individual / entity / vessel / alias).

Why per-class: vector cosine over Titan v2 doesn't behave uniformly across
classes. Individuals (~2-token names) cluster tightly; entities (long,
boilerplate-laden corporate names) get artificially-high baselines from
words like "LIMITED" / "TRADING" / "SHIPPING" that the embedding model
treats as semantically loaded. One global threshold over-blocks entities
and under-blocks individuals — calibrate them separately.

Gold-set CSV columns:
    name              raw query name (NOT pre-normalized — script normalizes)
    expected_sdn      target SDN number, or empty string for known-clean
    variant_class     individual | entity | vessel | alias
    notes             optional, ignored

For each gold row we:
  1. Normalize via the loader's normalize_name (byte-identical with screener).
  2. Embed via Titan v2 (same model as the production embed job).
  3. Pull top-K nearest variants from ofac_sdn_vectors by cosine.
  4. Record top-1 SDN number + cosine score + blended trgm/fuzz/cosine score.

Then we sweep T ∈ [MIN_T, MAX_T] in STEP increments, compute
precision/recall/F1 per class on the cosine and blended scores, and pick
the F1-optimal cutoff per class.

Run:
  PIPELINE_MODE=pipeline1 python -m scripts.setup.calibrate_ofac_thresholds \\
      --gold scripts/setup/data/ofac_calibration_gold.csv \\
      --out  scripts/setup/data/ofac_thresholds.json

Env:
  AWS_REGION        (default us-east-1)
  CAL_TOP_K         (default 5)         top-K candidates to consider per name
  CAL_CONCURRENCY   (default 5)         in-flight Titan calls
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

import boto3
from sqlalchemy import text

from src.services.database import init_db, close_db, get_db
from src.services.ofac_sdn_loader import normalize_name


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_MODEL = "amazon.titan-embed-text-v2:0"
_TOP_K = int(os.getenv("CAL_TOP_K", "5"))
_CONCURRENCY = int(os.getenv("CAL_CONCURRENCY", "5"))

_VALID_CLASSES = {"individual", "entity", "vessel", "alias"}

# Sweep range. Below 0.70 we know we're in noise; above 0.95 we lose recall
# on any non-exact match. 0.01 step keeps the table digestible.
_MIN_T, _MAX_T, _STEP = 0.70, 0.95, 0.01


@dataclass
class GoldRow:
    name: str
    expected_sdn: str          # "" for known-clean
    variant_class: str
    normalized: str
    # Filled in after vector query:
    top_sdn: Optional[str] = None
    cosine: float = 0.0
    blended: float = 0.0       # 0.4*trgm + 0.4*fuzz + 0.2*cosine
    matched_correct: bool = False  # top_sdn == expected_sdn (when expected non-empty)


# ── Loading + embedding ──────────────────────────────────────────────────

def load_gold(path: str) -> list[GoldRow]:
    rows: list[GoldRow] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, raw in enumerate(reader, start=2):  # line 1 is header
            name = (raw.get("name") or "").strip()
            klass = (raw.get("variant_class") or "").strip().lower()
            if not name:
                log.warning("gold_row_skipped_blank_name line=%d", i)
                continue
            if klass not in _VALID_CLASSES:
                log.warning("gold_row_bad_class line=%d class=%r", i, klass)
                continue
            rows.append(GoldRow(
                name=name,
                expected_sdn=(raw.get("expected_sdn") or "").strip(),
                variant_class=klass,
                normalized=normalize_name(name),
            ))
    log.info("gold_loaded total=%d positives=%d clean=%d",
             len(rows),
             sum(1 for r in rows if r.expected_sdn),
             sum(1 for r in rows if not r.expected_sdn))
    return rows


def _embed_sync(client, name: str) -> list[float]:
    resp = client.invoke_model(
        modelId=_MODEL,
        body=json.dumps({"inputText": name, "dimensions": 1024, "normalize": True}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


async def _embed_one(client, sem, row: GoldRow) -> tuple[GoldRow, list[float] | None]:
    async with sem:
        try:
            vec = await asyncio.to_thread(_embed_sync, client, row.normalized)
            return row, vec
        except Exception as exc:
            log.error("embed_failed name=%r err=%s", row.name[:40], exc)
            return row, None


# ── Vector query ─────────────────────────────────────────────────────────

async def _score_row(session, row: GoldRow, embedding: list[float]) -> None:
    """Populate row.top_sdn / cosine / blended / matched_correct."""
    res = (await session.execute(text("""
        SELECT
            sdn_number,
            name_normalized,
            1 - (embedding <=> CAST(:vec AS vector))               AS cosine,
            similarity(name_normalized, :q)                        AS trgm
        FROM ofac_sdn_vectors
        WHERE is_active = TRUE
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :k
    """), {"vec": str(embedding), "q": row.normalized, "k": _TOP_K})).all()

    if not res:
        return

    try:
        from rapidfuzz import fuzz
        token_score = lambda a, b: fuzz.token_sort_ratio(a, b) / 100.0
    except ImportError:
        from difflib import SequenceMatcher
        token_score = lambda a, b: SequenceMatcher(None, a, b).ratio()

    # Pick the best blended candidate (mirrors prod _vector_rerank weights).
    best = None
    for cand in res:
        fuzz_score = token_score(row.normalized, cand.name_normalized)
        blended = 0.40 * float(cand.trgm) + 0.40 * fuzz_score + 0.20 * float(cand.cosine)
        if best is None or blended > best[2]:
            best = (cand.sdn_number, float(cand.cosine), blended)

    row.top_sdn = best[0]
    row.cosine = best[1]
    row.blended = best[2]
    if row.expected_sdn:
        row.matched_correct = (row.top_sdn == row.expected_sdn)


# ── Sweep + metrics ──────────────────────────────────────────────────────

@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _classify(row: GoldRow, score: float, threshold: float) -> str:
    """Return tp|fp|fn|tn for this row at this threshold.

    The "positive" prediction is: top_sdn matches expected AND score >= T.
    A high-score hit on the WRONG SDN is a false positive (mis-routing).
    """
    predicted_block = score >= threshold
    is_positive = bool(row.expected_sdn)

    if is_positive:
        if predicted_block and row.matched_correct:
            return "tp"
        # Either we missed it (score too low) or we hit the wrong SDN.
        # Both hurt the bank — the first is missed sanctions risk, the
        # second is wrong audit trail. Count both as FN for recall purity;
        # mis-routed hits also count as FP for precision.
        if predicted_block and not row.matched_correct:
            return "fp_and_fn"
        return "fn"
    # Clean party.
    return "fp" if predicted_block else "tn"


def sweep(rows: list[GoldRow], score_attr: str) -> dict[str, dict[float, Counts]]:
    """Returns {variant_class: {threshold: Counts}}."""
    out: dict[str, dict[float, Counts]] = defaultdict(lambda: defaultdict(Counts))
    thresholds = [round(_MIN_T + i * _STEP, 2) for i in range(int((_MAX_T - _MIN_T) / _STEP) + 1)]
    for row in rows:
        score = getattr(row, score_attr)
        for t in thresholds:
            verdict = _classify(row, score, t)
            c = out[row.variant_class][t]
            if verdict == "tp":
                c.tp += 1
            elif verdict == "fp":
                c.fp += 1
            elif verdict == "fn":
                c.fn += 1
            elif verdict == "tn":
                c.tn += 1
            elif verdict == "fp_and_fn":
                c.fp += 1
                c.fn += 1
    return out


def pick_optimal(sweeps: dict[str, dict[float, Counts]]) -> dict[str, dict]:
    """For each class, return the F1-optimal threshold + its metrics."""
    out = {}
    for klass, by_t in sweeps.items():
        best_t, best_c = max(by_t.items(), key=lambda kv: (kv[1].f1, -abs(kv[0] - 0.85)))
        out[klass] = {
            "threshold": best_t,
            "precision": round(best_c.precision, 4),
            "recall": round(best_c.recall, 4),
            "f1": round(best_c.f1, 4),
            "tp": best_c.tp, "fp": best_c.fp,
            "fn": best_c.fn, "tn": best_c.tn,
        }
    return out


def print_sweep_table(klass: str, by_t: dict[float, Counts]) -> None:
    print(f"\n── variant_class = {klass} ─────────────────────────────────")
    print(f"  {'T':>5}  {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}  "
          f"{'prec':>6} {'rec':>6} {'F1':>6}")
    for t in sorted(by_t):
        c = by_t[t]
        print(f"  {t:>5.2f}  {c.tp:>4} {c.fp:>4} {c.fn:>4} {c.tn:>4}  "
              f"{c.precision:>6.3f} {c.recall:>6.3f} {c.f1:>6.3f}")


# ── Main ─────────────────────────────────────────────────────────────────

async def run(gold_path: str, out_path: Optional[str], verbose: bool) -> int:
    rows = load_gold(gold_path)
    if not rows:
        log.error("gold set empty — nothing to calibrate")
        return 2

    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("bedrock-runtime", region_name=region)
    sem = asyncio.Semaphore(_CONCURRENCY)

    log.info("embedding gold rows count=%d concurrency=%d", len(rows), _CONCURRENCY)
    embed_results = await asyncio.gather(*[_embed_one(client, sem, r) for r in rows])

    embedded = [(r, v) for r, v in embed_results if v is not None]
    log.info("embed_done ok=%d failed=%d", len(embedded), len(rows) - len(embedded))

    async with get_db() as session:
        for r, vec in embedded:
            await _score_row(session, r, vec)

    cosine_sweep = sweep([r for r, _ in embedded], "cosine")
    blended_sweep = sweep([r for r, _ in embedded], "blended")

    print("\n" + "=" * 60)
    print("COSINE-ONLY sweep")
    print("=" * 60)
    for klass in sorted(cosine_sweep):
        if verbose:
            print_sweep_table(klass, cosine_sweep[klass])

    print("\n" + "=" * 60)
    print("BLENDED (0.4 trgm + 0.4 fuzz + 0.2 cosine) sweep")
    print("=" * 60)
    for klass in sorted(blended_sweep):
        if verbose:
            print_sweep_table(klass, blended_sweep[klass])

    cosine_optimal = pick_optimal(cosine_sweep)
    blended_optimal = pick_optimal(blended_sweep)

    print("\n" + "=" * 60)
    print("F1-OPTIMAL thresholds (suggested SSM values)")
    print("=" * 60)
    print(f"\n  {'class':<12} {'score':<8} {'T':>5} {'prec':>6} {'rec':>6} {'F1':>6}")
    for klass in sorted(_VALID_CLASSES):
        if klass in cosine_optimal:
            o = cosine_optimal[klass]
            print(f"  {klass:<12} {'cosine':<8} "
                  f"{o['threshold']:>5.2f} {o['precision']:>6.3f} "
                  f"{o['recall']:>6.3f} {o['f1']:>6.3f}")
        if klass in blended_optimal:
            o = blended_optimal[klass]
            print(f"  {klass:<12} {'blended':<8} "
                  f"{o['threshold']:>5.2f} {o['precision']:>6.3f} "
                  f"{o['recall']:>6.3f} {o['f1']:>6.3f}")

    payload = {
        "model": _MODEL,
        "top_k": _TOP_K,
        "gold_size": len(rows),
        "embedded": len(embedded),
        "cosine_optimal": cosine_optimal,
        "blended_optimal": blended_optimal,
        "ssm_suggestions": {
            f"/fortress/p2/ofac/threshold/{klass}": blended_optimal[klass]["threshold"]
            for klass in blended_optimal
        },
    }

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        print(f"\nwrote {out_path}")

    print("\nNext step:")
    print("  1. Review the table above with compliance ops.")
    print("  2. Load thresholds into SSM (see ssm_suggestions in JSON output).")
    print("  3. Run scripts/ops/ofac_ab_compare.py in shadow mode for 1 wk.")

    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", required=True, help="labeled gold-set CSV")
    parser.add_argument("--out", default=None, help="write JSON results here")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print full per-threshold tables")
    args = parser.parse_args()

    await init_db()
    try:
        return await run(args.gold, args.out, args.verbose)
    finally:
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
