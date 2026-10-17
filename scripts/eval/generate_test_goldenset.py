"""Golden-set batch-run + scoring harness for the test-generation pipeline.

Runs agents 1-5 (Discovery -> Understanding -> Framework -> Generation ->
Validation) for each rule in a golden set and scores the generated pytest
against OBJECTIVE, auto-checkable grounding metrics. Agent 6 (GitHub PR / JIRA
comment) is intentionally skipped so the harness has no external side effects.

Run inside the P2 container (has Bedrock creds + DB + src):
    docker exec fortress-pipeline2 python /tmp/generate_test_goldenset.py \
        --rules FINCEN-1010-660-R3,OCC-BSA-CDD-001 --out /tmp/goldenset_report

Default golden set = BSA/AML rules that have obligations + a CFR citation.

SCORING (all automatable; no self-assessment in the grounding score):
  - syntax_valid        : ast.parse succeeds
  - cfr_cited           : the rule's own CFR citation appears in the test code
  - obligation_coverage : fraction of the rule's obligations evidenced in code
                          (obligation_id or its key terms appear)
  - threshold_hygiene   : no known-stale threshold value present (e.g. BO=25)
  grounding_score = mean(syntax_valid, cfr_cited, obligation_coverage)

Pipeline self-scores (quality/compliance) are recorded for reference but are
NOT part of grounding_score — they are the system grading itself.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

DEFAULT_GOLDEN = [
    "FINCEN-1010-660-R3", "FINCEN-SAR-001", "OCC-BSA-CDD-001", "OCC-BSA-CTR-001",
    "BSA-TRAVEL-001", "FFIEC-BSA-CIP-001", "FINCEN-1010-210-b-R1", "FINCEN-1010-205-R1",
]
# Values that must NOT appear (superseded / common LLM mistakes).
STALE_VALUES = {"beneficial_ownership=25", "25% of a legal entity"}


def _keyterms(s: str, n: int = 5) -> list[str]:
    toks = [t for t in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", s or "")
            if t.lower() not in {"must", "this", "that", "with", "shall", "which", "against"}]
    return toks[:n]


async def _load_rule(session, rule_id: str) -> dict | None:
    r = (await session.execute(text(
        "SELECT rule_id, rule_text, cfr_citation, category, regulator "
        "FROM compliance_rules WHERE rule_id=:r AND is_active=TRUE"
    ), {"r": rule_id})).first()
    if not r:
        return None
    obls = (await session.execute(text(
        "SELECT obligation_id, source_text FROM obligations "
        "WHERE regulation_id=:r AND is_active=TRUE ORDER BY obligation_id"
    ), {"r": rule_id})).fetchall()
    return {
        "rule_id": r.rule_id, "rule_text": r.rule_text or "",
        "cfr": r.cfr_citation or "", "category": r.category or "",
        "regulator": r.regulator or "",
        "obligations": [{"id": o.obligation_id, "text": o.source_text or ""} for o in obls],
    }


def _story(rule: dict) -> str:
    obl = "\n".join(f"- {o['text']}" for o in rule["obligations"])
    return (f"Compliance test required for rule {rule['rule_id']} "
            f"({rule['regulator']}, CFR {rule['cfr']}).\n\nRule: {rule['rule_text']}\n\n"
            f"Obligations:\n{obl}\n\nAcceptance: assertions must cite {rule['cfr']}; "
            f"cover boundary values; audit every compliance decision.")


def _score(rule: dict, test_code: str) -> dict:
    code = test_code or ""
    # syntax
    try:
        ast.parse(code)
        syntax_valid = True
    except Exception:
        syntax_valid = False
    # cfr grounding — tolerate spacing/section-symbol variants
    cfr = rule["cfr"]
    cfr_core = re.sub(r"\s+", "", cfr).replace("C.F.R.", "CFR") if cfr else ""
    code_norm = re.sub(r"\s+", "", code).replace("C.F.R.", "CFR")
    cfr_cited = bool(cfr_core) and cfr_core in code_norm
    cfr_hits = len(re.findall(re.escape(cfr_core), code_norm)) if cfr_core else 0
    # obligation coverage
    covered = 0
    for o in rule["obligations"]:
        if o["id"] in code:
            covered += 1
            continue
        terms = _keyterms(o["text"])
        if terms and sum(1 for t in terms if t.lower() in code.lower()) >= max(2, len(terms) // 2):
            covered += 1
    n = len(rule["obligations"]) or 1
    obligation_coverage = round(covered / n, 3)
    # threshold hygiene
    low = code.lower().replace(" ", "")
    threshold_hygiene = not any(sv.replace(" ", "").lower() in low for sv in STALE_VALUES)
    grounding = round((int(syntax_valid) + int(cfr_cited) + obligation_coverage) / 3, 3)
    return {
        "syntax_valid": syntax_valid, "cfr_cited": cfr_cited, "cfr_hits": cfr_hits,
        "obligations_total": len(rule["obligations"]), "obligations_covered": covered,
        "obligation_coverage": obligation_coverage, "threshold_hygiene": threshold_hygiene,
        "grounding_score": grounding,
    }


async def _run_one(rule_id: str) -> dict:
    from src.services.database import get_db
    from src.agents.discovery import DiscoveryAgent
    from src.agents.understanding import UnderstandingAgent
    from src.agents.framework import FrameworkAgent
    from src.agents.generation_bedrock import GenerationAgent
    from src.agents.validation import ValidationAgent

    async with get_db() as s:
        rule = await _load_rule(s, rule_id)
    if not rule:
        return {"rule_id": rule_id, "error": "not_found"}

    story = _story(rule)
    ctx = {"workflow_id": f"eval-{rule_id}", "jira_key": f"EVAL-{rule_id}"}
    try:
        disc = await DiscoveryAgent().execute(ctx, {**ctx, "jira_story": story})
        und = await UnderstandingAgent().execute(
            ctx, {**ctx, "jira_story": story, "discovery": disc, "rule_id": rule_id})
        fw = await FrameworkAgent().execute(ctx, {**ctx, "understanding": und})
        scenarios = fw.get("scenarios", [])
        gen = await GenerationAgent().execute(
            ctx, {**ctx, "scenarios": scenarios, "understanding": und, "rule_id": rule_id})
        test_code = gen.get("test_code", "")
        val = await ValidationAgent().execute(
            ctx, {**ctx, "test_code": test_code, "scenarios": scenarios})
    except Exception as e:
        return {"rule_id": rule_id, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    sc = _score(rule, test_code)
    return {
        "rule_id": rule_id, "cfr": rule["cfr"], "category": rule["category"],
        "scenarios": len(scenarios), "test_lines": len((test_code or "").splitlines()),
        **sc,
        "self_quality": val.get("quality_score"), "self_compliance": val.get("compliance_score"),
        "self_recommendation": val.get("recommendation"),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", help="comma-separated rule_ids (default: built-in BSA/AML set)")
    ap.add_argument("--out", default="/tmp/goldenset_report", help="output path prefix")
    args = ap.parse_args()
    rules = [r.strip() for r in args.rules.split(",")] if args.rules else DEFAULT_GOLDEN

    # Stand up the DB engine (and best-effort Redis) the way app startup does.
    from src.services.database import init_db, close_db
    await init_db()
    try:
        from src.services.redis_service import init_redis
        await init_redis()
    except Exception:
        pass

    print(f"golden-set run: {len(rules)} rules", flush=True)
    results = []
    for i, rid in enumerate(rules, 1):
        print(f"[{i}/{len(rules)}] {rid} ...", flush=True)
        res = await _run_one(rid)
        results.append(res)
        if "error" in res:
            print(f"    ERROR: {res['error']}", flush=True)
        else:
            print(f"    grounding={res['grounding_score']} cfr_cited={res['cfr_cited']} "
                  f"obl={res['obligations_covered']}/{res['obligations_total']} "
                  f"syntax={res['syntax_valid']} self_q={res['self_quality']}", flush=True)

    ok = [r for r in results if "error" not in r]
    agg = {
        "rules_run": len(results), "rules_ok": len(ok),
        "mean_grounding": round(sum(r["grounding_score"] for r in ok) / len(ok), 3) if ok else 0,
        "cfr_cited_rate": round(sum(r["cfr_cited"] for r in ok) / len(ok), 3) if ok else 0,
        "mean_obligation_coverage": round(sum(r["obligation_coverage"] for r in ok) / len(ok), 3) if ok else 0,
        "syntax_valid_rate": round(sum(r["syntax_valid"] for r in ok) / len(ok), 3) if ok else 0,
        "threshold_hygiene_rate": round(sum(r["threshold_hygiene"] for r in ok) / len(ok), 3) if ok else 0,
    }
    report = {"aggregate": agg, "results": results}

    with open(args.out + ".json", "w") as f:
        json.dump(report, f, indent=2)

    lines = ["# Golden-set test-generation report", "",
             "| rule | cfr_cited | obl covered | syntax | grounding | self_q | self_c |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['rule_id']} | ERROR: {r['error']} | | | | | |")
        else:
            lines.append(f"| {r['rule_id']} | {'✅' if r['cfr_cited'] else '❌'} "
                         f"| {r['obligations_covered']}/{r['obligations_total']} "
                         f"| {'✅' if r['syntax_valid'] else '❌'} | {r['grounding_score']} "
                         f"| {r['self_quality']} | {r['self_compliance']} |")
    lines += ["", "## Aggregate", "```", json.dumps(agg, indent=2), "```"]
    with open(args.out + ".md", "w") as f:
        f.write("\n".join(lines))

    print("\n=== AGGREGATE ===", flush=True)
    print(json.dumps(agg, indent=2), flush=True)
    print(f"\nwrote {args.out}.json and {args.out}.md", flush=True)
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
