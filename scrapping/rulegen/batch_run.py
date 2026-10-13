#!/usr/bin/env python3
"""Progressive batch extraction. One batch, validate, then the next.

    python batch_run.py plan                          # what will run
    python batch_run.py check --prompt prompts/bsa_aml.v1.yaml
    python batch_run.py run golden                    # first batch
    python batch_run.py evaluate                      # score against golden
    python batch_run.py run funds_transfer            # next, only if scored ok
    python batch_run.py status
    python batch_run.py rerun 31\\ CFR\\ 1020.210      # after a prompt change

The gate between batches is deliberate. A prompt regression caught on
three sections costs three sections; caught on fifty it costs fifty, and
the SME review time is the expensive part.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from rulegen.batches import BATCHES, BY_KEY, total_citations
from rulegen.prompts import load as load_prompt, classifier
from rulegen.queue import Queue
from rulegen.pipeline import extract
from rulegen.mechanical import split
from rulegen.evaluate import score_run, verdict
from rulegen.golden import SECTIONS

DB = Path("rulegen.db")
DEFAULT_PROMPT = Path("prompts/bsa_aml.v1.yaml")


def _prompt_path(argv) -> Path:
    if "--prompt" in argv:
        return Path(argv[argv.index("--prompt") + 1])
    return DEFAULT_PROMPT


def _fetch(citation: str):
    """Golden sections come from the fixture; everything else from eCFR."""
    if citation in SECTIONS:
        spec = SECTIONS[citation]
        class S:
            text = spec["text"]
            heading = ""
            source_url = spec["url"]
        return S()
    from rulegen.ecfr import fetch_section
    return fetch_section(citation)


# ── commands ──────────────────────────────────────────────────────────
def cmd_plan(argv) -> None:
    print(f"{len(BATCHES)} batches, {total_citations()} citations\n")
    for i, b in enumerate(BATCHES, 1):
        print(f"{i}. {b.key}  ({len(b.citations)})  {b.label}")
        print(f"   {b.why_this_order}")
        print()
    print("Run in order. Score after each; do not start the next batch on a")
    print("prompt that has not passed the golden gate.")


def cmd_check(argv) -> None:
    cfg = load_prompt(_prompt_path(argv))
    print(f"{cfg.path}  version {cfg.version}")
    print(f"  model            {cfg.model}")
    print(f"  temperature      {cfg.temperature}")
    print(f"  units per call   {cfg.units_per_call}")
    print(f"  few-shot         {len(cfg.few_shot)}: "
          f"{', '.join(f['name'] for f in cfg.few_shot)}")
    print(f"  vocabulary       {len(cfg.evidence_vocabulary)} artifacts")
    issues = cfg.validate()
    if issues:
        print("\nPROBLEMS:")
        for i in issues:
            print(f"  - {i}")
        sys.exit(1)
    print("\nconfig ok")


def cmd_run(argv) -> None:
    if len(argv) < 2:
        print("which batch? try: python batch_run.py plan")
        return
    key = argv[1]
    if key not in BY_KEY:
        print(f"unknown batch {key!r}. options: {', '.join(BY_KEY)}")
        return
    batch = BY_KEY[key]
    cfg = load_prompt(_prompt_path(argv))
    issues = cfg.validate()
    if issues:
        print("prompt config has problems — fix before spending:")
        for i in issues:
            print(f"  - {i}")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY set")
        return

    classify = classifier(cfg)
    q = Queue(DB)
    print(f"batch {batch.key}: {len(batch.citations)} citations, "
          f"prompt {cfg.version}\n")

    started, calls = time.time(), 0
    for citation in batch.citations:
        try:
            section = _fetch(citation)
            units = len(split(section.text))
            record = extract(citation, section.text, section.source_url,
                             classify)
            q.add_discovered([_stub(citation)])
            q.commit_section(citation, record.content_hash, record.rules, units,
                             section_text=section.text,
                             source_url=section.source_url,
                             obligations=record.obligations)
            calls += units
            routes: dict[str, int] = {}
            for r in record.rules:
                routes[r.route] = routes.get(r.route, 0) + 1
            print(f"  {citation:<20} {units:>3} units  "
                  f"{len(record.rules):>2} rules  {routes}")
        except Exception as exc:
            q.add_discovered([_stub(citation)])
            q.fail_section(citation, str(exc))
            print(f"  {citation:<20} FAILED  {type(exc).__name__}: {exc}")

    mins = (time.time() - started) / 60
    print(f"\n{calls} model calls, {mins:.1f} min")
    if key == "golden":
        print("\nnext: python batch_run.py evaluate")
    else:
        print("\nnext: python batch_run.py status")


def _stub(citation: str):
    """Build a Discovered row from a citation string. Handles the extended
    citation forms (App, Part, Chapter) so a batch containing them doesn't
    silently default to title=31 part=1010."""
    from rulegen.discovery import Discovered
    from rulegen.ecfr import parse_citation
    try:
        spec = parse_citation(citation)
    except ValueError:
        return Discovered(31, "1010", citation, "", citation, False)
    section = spec.section or (f"{spec.part} App {spec.appendix}"
                               if spec.appendix else spec.part or spec.chapter or citation)
    return Discovered(spec.title, spec.part or "", section, "", citation, False)


def cmd_evaluate(argv) -> None:
    cfg = load_prompt(_prompt_path(argv))
    q = Queue(DB)
    results: dict[str, list] = {}
    with q._conn() as c:
        for row in c.execute("SELECT citation, payload FROM rules"):
            results.setdefault(row["citation"], []).append(
                json.loads(row["payload"]))

    run = score_run(results, cfg.evidence_vocabulary)
    if run.get("scored", 0) == 0:
        print("no golden sections extracted yet — run: batch_run.py run golden")
        return

    print(f"prompt {cfg.version}   scored {run['scored']} golden sections\n")
    for s in run["per_section"]:
        flag = "" if s["found_duties"] >= s["expected_duties"] else "  <<<"
        print(f"  {s['citation']:<20} expected {s['expected_duties']:>2}  "
              f"found {s['found_duties']:>2}  recall {s['recall']:.2f}{flag}")
        for n in s["notes"]:
            print(f"      {n}")
    print(f"\n  recall            {run['recall']}")
    print(f"  over-extraction   {run['over_extraction']}")
    print(f"  evidence in vocab {run['evidence_in_vocab']}")
    if run["charter_errors"]:
        print(f"  charter errors    {len(run['charter_errors'])}")
        for e in run["charter_errors"][:5]:
            print(f"      {e}")

    ok, why = verdict(run)
    print(f"\n  VERDICT: {'PROMOTE' if ok else 'HOLD'} — {why}")
    if not ok:
        print("\n  Copy the prompt to a new version, adjust, re-run the golden")
        print("  batch, and evaluate again. Do not advance to the next batch.")
    sys.exit(0 if ok else 1)


def cmd_status(argv) -> None:
    q = Queue(DB)
    s = q.stats()
    done = s["sections"].get("extracted", 0)
    print(f"sections extracted : {done} / {total_citations()}")
    print(f"rules              : {s['rules_total']} "
          f"({s['rules_reviewed']} reviewed)")
    print(f"by route           : {s['rules_by_route']}")
    print(f"model calls        : {s['model_calls']}")

    with q._conn() as c:
        seen = {r["citation"] for r in
                c.execute("SELECT citation FROM sections WHERE state='extracted'")}
    print("\nbatch progress:")
    for b in BATCHES:
        n = sum(1 for cit in b.citations if cit in seen)
        bar = "#" * n + "." * (len(b.citations) - n)
        mark = "done" if n == len(b.citations) else ""
        print(f"  {b.key:<18} [{bar}] {n}/{len(b.citations)} {mark}")

    blocks = s["rules_by_route"].get("block", 0)
    reviews = s["rules_by_route"].get("review", 0)
    print(f"\nreview load: {blocks} blocks + {reviews} reviews "
          f"~{(blocks + reviews) * 2 / 60:.1f} h")


def cmd_rerun(argv) -> None:
    """Re-extract specific citations after a prompt change.

    Existing rules are replaced; the reviewed flag survives, so an SME's
    completed work is not thrown away by a re-run.
    """
    citations = [a for a in argv[1:] if not a.startswith("--")]
    if not citations:
        print("usage: batch_run.py rerun '31 CFR 1020.210' [...]")
        return
    cfg = load_prompt(_prompt_path(argv))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY set")
        return
    classify = classifier(cfg)
    q = Queue(DB)
    for citation in citations:
        section = _fetch(citation)
        record = extract(citation, section.text, section.source_url, classify)
        q.commit_section(citation, record.content_hash, record.rules,
                         len(split(section.text)),
                         section_text=section.text,
                         source_url=section.source_url,
                         obligations=record.obligations)
        print(f"  {citation:<20} {len(record.rules)} rules (prompt {cfg.version})")


COMMANDS = {"plan": cmd_plan, "check": cmd_check, "run": cmd_run,
            "evaluate": cmd_evaluate, "status": cmd_status, "rerun": cmd_rerun}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    COMMANDS[argv[0]](argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
