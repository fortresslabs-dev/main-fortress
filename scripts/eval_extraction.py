"""Extraction accuracy harness.

Runs every case in tests/golden/extraction/*.jsonl through the live
PenaltyExtraction path (LLM_MOCK_MODE is force-disabled inside this
script — mock mode would defeat the point of the eval).

Usage:
    python -m scripts.eval_extraction                    # all suites
    python -m scripts.eval_extraction --suite enforcement_penalty
    python -m scripts.eval_extraction --min-accuracy 0.85 # fail under threshold

Exit code: 0 if accuracy >= --min-accuracy, 1 otherwise. Wired into
Makefile via `make extraction-eval` for use in CI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "golden" / "extraction"

# Amounts within $1 are considered equal — covers Decimal rounding of
# "$1.5 million" → 1_500_000 vs 1_500_000.00.
AMOUNT_TOLERANCE = Decimal("1")


def _load_suite(name: str) -> List[Dict[str, Any]]:
    path = GOLDEN_DIR / f"{name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"golden suite not found: {path}")
    cases = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    return cases


def _amounts_match(expected: Any, actual: Any) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    return abs(Decimal(str(expected)) - Decimal(str(actual))) <= AMOUNT_TOLERANCE


async def _run_penalty_suite(cases: List[Dict[str, Any]]) -> Tuple[int, int, List[Dict[str, Any]]]:
    from src.extraction.penalty import extract_penalty
    passed = 0
    misses: List[Dict[str, Any]] = []
    for case in cases:
        cid = case["id"]
        expected_amount = case.get("expected_amount_usd")
        expected_flag = case.get("expected_is_penalty_not_asset")
        try:
            result = await extract_penalty(case["input"])
        except Exception as exc:
            misses.append({"id": cid, "verdict": "ERROR", "error": str(exc)})
            continue

        actual_amount = (
            None if result.amount_usd is None else str(result.amount_usd)
        )
        ok_amount = _amounts_match(expected_amount, actual_amount)
        ok_flag = (
            expected_flag is None
            or result.is_penalty_not_asset == expected_flag
        )
        if ok_amount and ok_flag:
            passed += 1
            continue
        misses.append({
            "id": cid,
            "verdict": "FAIL",
            "expected_amount_usd": expected_amount,
            "actual_amount_usd": actual_amount,
            "expected_is_penalty_not_asset": expected_flag,
            "actual_is_penalty_not_asset": result.is_penalty_not_asset,
            "confidence": result.confidence,
            "source_span": result.source_span,
            "reasoning": result.reasoning,
        })
    return passed, len(cases), misses


def _primary_cite_match(expected: Any, expected_choices: Any, actual: Any) -> bool:
    """Allow exact match against expected or membership in expected_choices.

    A case that lists expected_cfr_citation_primary_choices passes when the
    LLM picks ANY of those (or null, if null is in the list) — handles cases
    where multiple cites are plausibly 'primary'.
    """
    if expected_choices is not None:
        return actual in expected_choices
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    return expected.strip() == actual.strip()


def _threshold_amounts_satisfied(expected: List[str], actual_decimals: List[Any]) -> bool:
    """Every expected amount must appear in actual; extras are tolerated."""
    actual_strs = {str(Decimal(str(a))) for a in actual_decimals}
    for amt in expected:
        if str(Decimal(amt)) not in actual_strs:
            return False
    return True


async def _run_rule_suite(cases: List[Dict[str, Any]]) -> Tuple[int, int, List[Dict[str, Any]]]:
    from src.extraction.rule import extract_rule
    passed = 0
    misses: List[Dict[str, Any]] = []
    for case in cases:
        cid = case["id"]
        try:
            result = await extract_rule(case["rule_text"], case.get("regulator"))
        except Exception as exc:
            misses.append({"id": cid, "verdict": "ERROR", "error": str(exc)})
            continue

        checks: List[Tuple[str, bool, Any, Any]] = []

        # cfr_citation_primary — exact or one-of
        ok_primary = _primary_cite_match(
            case.get("expected_cfr_citation_primary"),
            case.get("expected_cfr_citation_primary_choices"),
            result.cfr_citation_primary,
        )
        checks.append(("cfr_citation_primary", ok_primary,
                       case.get("expected_cfr_citation_primary") or
                       case.get("expected_cfr_citation_primary_choices"),
                       result.cfr_citation_primary))

        # cfr_citations_all must contain expected list (if asserted)
        all_must = case.get("expected_cfr_citations_all_must_include")
        if all_must is not None:
            ok_all = all(c in result.cfr_citations_all for c in all_must)
            checks.append(("cfr_citations_all", ok_all, all_must,
                           result.cfr_citations_all))

        # usc_citations must contain expected list (if asserted)
        usc_must = case.get("expected_usc_citations_must_include")
        if usc_must is not None:
            ok_usc = all(c in result.usc_citations for c in usc_must)
            checks.append(("usc_citations", ok_usc, usc_must,
                           result.usc_citations))

        # jurisdiction_level — exact
        exp_juris = case.get("expected_jurisdiction_level")
        if exp_juris is not None:
            ok_juris = result.jurisdiction_level == exp_juris
            checks.append(("jurisdiction_level", ok_juris, exp_juris,
                           result.jurisdiction_level))

        # state_code — exact (None must match None)
        if "expected_state_code" in case:
            exp_sc = case["expected_state_code"]
            ok_sc = result.state_code == exp_sc
            checks.append(("state_code", ok_sc, exp_sc, result.state_code))

        # obligation count within range
        obl_min = case.get("expected_min_obligations")
        obl_max = case.get("expected_max_obligations")
        if obl_min is not None or obl_max is not None:
            n = len(result.obligations)
            ok_obl = ((obl_min is None or n >= obl_min) and
                      (obl_max is None or n <= obl_max))
            checks.append(("obligation_count", ok_obl,
                           f"{obl_min}..{obl_max}", n))

        # obligation_types must include (e.g. ['prohibited'])
        types_must = case.get("expected_obligation_types_must_include")
        if types_must is not None:
            actual_types = {o.obligation_type for o in result.obligations}
            ok_types = all(t in actual_types for t in types_must)
            checks.append(("obligation_types", ok_types, types_must,
                           sorted(actual_types)))

        # threshold count within range
        thr_min = case.get("expected_min_thresholds")
        thr_max = case.get("expected_max_thresholds")
        if thr_min is not None or thr_max is not None:
            n = len(result.monetary_thresholds)
            ok_thr = ((thr_min is None or n >= thr_min) and
                      (thr_max is None or n <= thr_max))
            checks.append(("threshold_count", ok_thr,
                           f"{thr_min}..{thr_max}", n))

        # threshold amounts must include
        amts = case.get("expected_threshold_amounts")
        if amts is not None:
            ok_amts = _threshold_amounts_satisfied(
                amts, [t.amount_usd for t in result.monetary_thresholds]
            )
            checks.append(("threshold_amounts", ok_amts, amts,
                           [str(t.amount_usd) for t in result.monetary_thresholds]))

        # confidence ceiling (for boundary cases)
        conf_max = case.get("expected_confidence_max")
        if conf_max is not None:
            ok_conf = result.confidence <= conf_max
            checks.append(("confidence_max", ok_conf, conf_max,
                           result.confidence))

        all_pass = all(c[1] for c in checks)
        if all_pass:
            passed += 1
            continue
        misses.append({
            "id": cid,
            "verdict": "FAIL",
            "confidence": result.confidence,
            "failed_checks": [
                {"field": f, "expected": exp, "actual": act}
                for f, ok, exp, act in checks if not ok
            ],
            "reasoning": result.reasoning,
        })
    return passed, len(cases), misses


SUITES = {
    "enforcement_penalty": _run_penalty_suite,
    "rule_extraction": _run_rule_suite,
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=list(SUITES.keys()), default=None,
                    help="Run a single suite (default: all)")
    ap.add_argument("--min-accuracy", type=float, default=0.80,
                    help="Fail when accuracy falls below this fraction.")
    args = ap.parse_args()

    # LLM_MOCK_MODE returns null + 0.0 confidence for every case, which
    # would give 100% on the no-penalty cases and 0% on the rest. That's
    # noise — force it off for the duration of this script.
    os.environ["LLM_MOCK_MODE"] = "false"

    suites = [args.suite] if args.suite else list(SUITES.keys())
    total_passed = 0
    total_count = 0
    for suite in suites:
        cases = _load_suite(suite)
        passed, count, misses = await SUITES[suite](cases)
        total_passed += passed
        total_count += count
        acc = passed / count if count else 0.0
        print(f"\n=== {suite}: {passed}/{count} ({acc:.0%}) ===")
        for m in misses:
            print(json.dumps(m, indent=2))

    overall = total_passed / total_count if total_count else 0.0
    print(f"\nOVERALL: {total_passed}/{total_count} ({overall:.0%}) "
          f"[threshold {args.min_accuracy:.0%}]")
    return 0 if overall >= args.min_accuracy else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
