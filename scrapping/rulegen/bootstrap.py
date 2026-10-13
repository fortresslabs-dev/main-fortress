#!/usr/bin/env python3
"""Full BSA/AML bootstrap. Resumable — kill it and re-run.

    python bootstrap.py discover        # walk eCFR, populate the queue
    python bootstrap.py scope           # gate every section
    python bootstrap.py rejects         # READ THIS BEFORE EXTRACTING
    python bootstrap.py extract         # model calls, resumable
    python bootstrap.py stats
    python bootstrap.py review          # the SME worklist, prioritised

Extraction is the only step that costs money, and `rejects` is the only
step that needs your eyes before it. Sample 20 rejects by hand: a wrong
reject drops a section silently, which is worse than a wrong rule.
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from rulegen.discovery import discover, BSA_PARTS, coverage_gap
from rulegen.scope import assess, summarise
from rulegen.queue import Queue
from rulegen.pipeline import extract
from rulegen.mechanical import split

DB = Path("rulegen.db")
AS_OF = date.today()


def cmd_discover(q: Queue) -> None:
    total = 0
    for title, parts in BSA_PARTS.items():
        print(f"walking title {title}, parts {sorted(parts)}...")
        found = discover(title, parts, as_of=AS_OF)
        added = q.add_discovered(found)
        total += added
        print(f"  {len(found)} sections found, {added} new")
    print(f"\n{total} new sections queued")
    print("next: python bootstrap.py scope")


def cmd_scope(q: Queue, fetch) -> None:
    pending = q.pending_scope()
    print(f"gating {len(pending)} sections\n")
    verdicts = []
    for row in pending:
        try:
            section = fetch(row["citation"])
            text, heading = section.text, section.heading
        except Exception as exc:
            q.fail_section(row["citation"], f"fetch: {exc}")
            continue
        v = assess(row["citation"], heading or row["heading"] or "", text,
                   part=row["part"])
        q.set_scope(row["citation"], v)
        verdicts.append((row["citation"], v))
        mark = "keep" if v.in_scope else "drop"
        print(f"  {mark}  {row['citation']:<18} {v.signal}")
    s = summarise(verdicts)
    print(f"\nkept {s['kept']} / {s['total']}   dropped {s['dropped']}")
    print(f"dropped by signal: {s['dropped_by_signal']}")
    if s["borderline_review_these"]:
        print(f"borderline, check these first: {s['borderline_review_these']}")
    print("\nnext: python bootstrap.py rejects   <-- read before extracting")


def cmd_rejects(q: Queue, _fetch=None) -> None:
    rows = q.rejects()
    print(f"{len(rows)} sections dropped by the scope gate.\n")
    print("Sample at least 20 by hand. A wrong reject loses a section")
    print("silently; a wrong rule gets caught in review.\n")
    by_signal: dict[str, list] = {}
    for r in rows:
        by_signal.setdefault(r["scope_signal"], []).append(r)
    for signal, group in sorted(by_signal.items()):
        print(f"\n{signal}  ({len(group)})")
        for r in group[:8]:
            print(f"    {r['citation']:<18} {(r['heading'] or '')[:56]}")
        if len(group) > 8:
            print(f"    ... {len(group) - 8} more")
    print("\nIf any of these should have been kept, fix rulegen/scope.py,")
    print("then: UPDATE sections SET state='discovered' WHERE state='out_of_scope';")


def cmd_extract(q: Queue, fetch, classify, batch: int = 5) -> None:
    if classify is None:
        print("no ANTHROPIC_API_KEY set — extraction needs it")
        return
    done = calls = 0
    started = time.time()
    while True:
        rows = q.next_batch(batch)
        if not rows:
            break
        for row in rows:
            citation = row["citation"]
            try:
                section = fetch(citation)
                units = len(split(section.text))
                record = extract(citation, section.text,
                                 section.source_url, classify)
                q.commit_section(citation, record.content_hash,
                                 record.rules, units,
                                 section_text=section.text,
                                 source_url=section.source_url,
                                 obligations=record.obligations)
                done += 1
                calls += units
                routes = {}
                for r in record.rules:
                    routes[r.route] = routes.get(r.route, 0) + 1
                print(f"  {citation:<18} {units:>3} units  "
                      f"{len(record.rules):>2} rules  {routes}")
            except Exception as exc:
                q.fail_section(citation, str(exc))
                print(f"  {citation:<18} FAILED: {type(exc).__name__}: {exc}")
    mins = (time.time() - started) / 60
    print(f"\n{done} sections, {calls} model calls, {mins:.1f} min")
    print("next: python bootstrap.py review")


def cmd_stats(q: Queue, _fetch=None) -> None:
    s = q.stats()
    print(f"sections        : {s['sections']}")
    print(f"rules           : {s['rules_total']} "
          f"({s['rules_reviewed']} reviewed)")
    print(f"by route        : {s['rules_by_route']}")
    print(f"model calls     : {s['model_calls']}")
    print(f"dropped by gate : {s['dropped_by_signal']}")
    todo = s["rules_total"] - s["rules_reviewed"]
    blocks = s["rules_by_route"].get("block", 0)
    reviews = s["rules_by_route"].get("review", 0)
    print(f"\nreview load: {blocks} blocks + {reviews} reviews")
    print(f"  at ~2 min each that is ~{(blocks + reviews) * 2 / 60:.1f} hours")
    print(f"  auto-promoted: sample 10%, not all "
          f"({s['rules_by_route'].get('auto_promote', 0)} rules)")


def cmd_review(q: Queue, _fetch=None) -> None:
    rows = q.review_worklist(30)
    if not rows:
        print("nothing pending")
        return
    import json
    for r in rows:
        d = json.loads(r["payload"])
        tag = {"block": "BLOCK ", "review": "REVIEW", "auto_promote": "sample"}
        print(f"\n[{tag.get(r['route'], r['route'])}] {r['rule_id']}  "
              f"{d.get('criticality', '')}")
        print(f"  {d.get('rule_logic', '')[:100]}")
        print(f"  evidence: {d.get('evidence_source')}   "
              f"span: {d.get('source_span', '')[:60]!r}")
        for reason in d.get("reasons", []):
            print(f"  {reason}")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    q = Queue(DB)

    fetch = classify = None
    if cmd in ("scope", "extract"):
        from rulegen.ecfr import fetch_section          # thin eCFR wrapper
        fetch = fetch_section
    if cmd == "extract":
        import os
        if os.environ.get("ANTHROPIC_API_KEY"):
            from rulegen.judgement import anthropic_api
            classify = anthropic_api()

    handlers = {"discover": lambda: cmd_discover(q),
                "scope": lambda: cmd_scope(q, fetch),
                "rejects": lambda: cmd_rejects(q),
                "extract": lambda: cmd_extract(q, fetch, classify),
                "stats": lambda: cmd_stats(q),
                "review": lambda: cmd_review(q)}
    if cmd not in handlers:
        print(__doc__)
        return 1
    handlers[cmd]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
