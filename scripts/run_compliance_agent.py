#!/usr/bin/env python
"""
CLI runner for the LangGraph Autonomous Compliance Agent.

Examples
--------
    # Run a single source by ID
    python scripts/run_compliance_agent.py --source ofac_sdn

    # Run all priority-1 sources
    python scripts/run_compliance_agent.py --priority 1

    # Run every source in the config
    python scripts/run_compliance_agent.py --all

    # Dry-run: just list sources, don't execute
    python scripts/run_compliance_agent.py --dry-run

    # Override max retry iterations
    python scripts/run_compliance_agent.py --source occ_bsa --max-iterations 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.agents.langgraph.compliance_agent import (
    load_sources,
    run_all_sources,
    run_compliance_agent,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the Autonomous Compliance Agent (LangGraph)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", help="Source ID to process (e.g. ofac_sdn)")
    group.add_argument("--priority", type=int, help="Run all sources with this priority level")
    group.add_argument("--all", action="store_true", help="Process every source in config")
    group.add_argument("--dry-run", action="store_true", help="List sources without executing")

    p.add_argument(
        "--config",
        default="config/compliance_sources.yaml",
        help="Path to compliance_sources.yaml (default: %(default)s)",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="Max self-healing iterations per source (default: %(default)s)",
    )
    p.add_argument(
        "--output",
        help="Write JSON results to this file",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    # ── Dry run ─────────────────────────────────────────────────────
    if args.dry_run:
        sources = load_sources(args.config)
        print(f"\n{'='*60}")
        print(f"  {len(sources)} sources in {args.config}")
        print(f"{'='*60}")
        for s in sources:
            print(f"  [{s.get('priority', '?')}] {s['source_id']:<25} {s['name']}")
        print()
        return

    # ── Single source ───────────────────────────────────────────────
    if args.source:
        sources = load_sources(args.config)
        match = [s for s in sources if s["source_id"] == args.source]
        if not match:
            print(f"ERROR: source '{args.source}' not found in {args.config}")
            available = [s["source_id"] for s in sources]
            print(f"Available: {', '.join(available)}")
            sys.exit(1)

        result = run_compliance_agent(match[0], max_iterations=args.max_iterations)
        results = [result]

    # ── Priority filter ─────────────────────────────────────────────
    elif args.priority is not None:
        results = run_all_sources(
            config_path=args.config,
            max_iterations=args.max_iterations,
            priority_filter=args.priority,
        )

    # ── All sources ─────────────────────────────────────────────────
    else:
        results = run_all_sources(
            config_path=args.config,
            max_iterations=args.max_iterations,
        )

    # ── Optional JSON output ────────────────────────────────────────
    if args.output:
        # Serialise, stripping bytes fields
        serialisable = []
        for r in results:
            entry = {k: v for k, v in r.items() if k != "document_content"}
            serialisable.append(entry)
        Path(args.output).write_text(json.dumps(serialisable, indent=2, default=str))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
