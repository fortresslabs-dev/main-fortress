#!/usr/bin/env python3
"""Run the extractor. With a key it calls the model; without, it shows
you the mechanical layer and where regex stops.

    python run.py                      # all five golden sections
    python run.py "31 CFR 1020.210"    # one section
    python run.py --recheck            # simulate tomorrow night
"""
import os
import re
import sys
from pathlib import Path

from rulegen.golden import SECTIONS
from rulegen.mechanical import split, stem, find_numbers, span_present
from rulegen.pipeline import extract, recheck, save, load

STORE = Path("store")
MODAL = re.compile(r"\b(shall|must|is required to|are required to|may not)\b", re.I)


def classifier():
    if os.environ.get("ANTHROPIC_API_KEY"):
        from rulegen.judgement import anthropic_api
        print("using: Anthropic API\n")
        return anthropic_api()
    print("no ANTHROPIC_API_KEY — mechanical layer only\n")
    return None


def report_mechanical(citation, spec):
    units = split(spec["text"])
    numbers = find_numbers(spec["text"])
    modal = [u for u in units if MODAL.search(u.text)]
    print(f"{citation}")
    print(f"  units split (regex)      : {len(units)}")
    print(f"  numbers typed (regex)    : {len(numbers)}  "
          f"{[(n['value'], n['unit']) for n in numbers]}")
    print(f"  duties by modal verb     : {len(modal)}")
    print(f"  duties I found by reading: {spec['expected_duties']}")
    gap = spec["expected_duties"] - len(modal)
    if gap > 0:
        print(f"  >>> regex misses {gap} — this is what the model is for")
    if spec.get("trap"):
        print(f"  trap: {spec['trap']}")
    print()
    return len(units), len(modal), spec["expected_duties"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_recheck = "--recheck" in sys.argv
    targets = args or list(SECTIONS)

    if do_recheck:
        for citation in targets:
            path = STORE / (citation.replace(" ", "-") + ".json")
            if not path.exists():
                print(f"{citation}: nothing stored — run extraction first")
                continue
            stored = load(path)
            same = recheck(stored, SECTIONS[citation]["text"])
            edited = SECTIONS[citation]["text"].replace(
                "5 business days", "10 business days")
            moved = recheck(stored, edited)
            print(f"{citation}")
            print(f"  unchanged text : changed={same.changed}  "
                  f"model calls={same.model_calls}  {same.note}")
            print(f"  edited text    : changed={moved.changed}  "
                  f"surviving={len(moved.surviving)} broken={len(moved.broken)}")
        return

    classify = classifier()
    tot_u = tot_m = tot_e = 0
    for citation in targets:
        spec = SECTIONS[citation]
        u, m, e = report_mechanical(citation, spec)
        tot_u += u; tot_m += m; tot_e += e

        if classify:
            record = extract(citation, spec["text"], spec["url"], classify)
            found = len(record.rules)
            routes = {}
            for r in record.rules:
                routes[r.route] = routes.get(r.route, 0) + 1
            print(f"  MODEL found              : {found} duties  {routes}")
            for r in record.rules:
                mark = {"auto_promote": "OK ", "review": "?? ", "block": "XX "}[r.route]
                print(f"    {mark}{r.path:<16} {r.rule_logic[:58]}")
                for reason in r.reasons:
                    print(f"        {reason}")
            save(record, STORE / (citation.replace(" ", "-") + ".json"))
            print()

    print("=" * 62)
    print(f"units {tot_u} | regex modal-verb {tot_m} | duties by reading {tot_e}")
    print(f"regex recall: {tot_m/tot_e:.0%}")
    if not classify:
        print("\nSet ANTHROPIC_API_KEY to close the gap.")


if __name__ == "__main__":
    main()
