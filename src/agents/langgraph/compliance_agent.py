"""
LangGraph Autonomous Compliance Agent — graph builder & runner.

Builds a StateGraph with 5 phases:
  analysis → implementation → execution → verification ─┬→ END
                                                         └→ iteration → implementation (loop)

Usage:
    from src.agents.langgraph.compliance_agent import run_compliance_agent

    result = run_compliance_agent({
        "source_id": "ofac_sdn",
        "name": "OFAC SDN List",
        "url": "https://www.treasury.gov/ofac/downloads/sdn.csv",
        "format": "csv",
        "expected_count": 8000,
        "fields": ["entity_name", "entity_type", "program"],
    })
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
import yaml
from langgraph.graph import END, StateGraph

from src.agents.langgraph.nodes import (
    analysis_node,
    execution_node,
    implementation_node,
    iteration_node,
    should_continue,
    verification_node,
)
from src.agents.langgraph.state import AgentState

logger = structlog.get_logger(__name__)

# Optional: LangSmith tracing (set env vars to enable)
# LANGCHAIN_TRACING_V2=true  LANGCHAIN_API_KEY=...  LANGCHAIN_PROJECT=compliance-agent


# ── Graph Builder ───────────────────────────────────────────────────

def build_compliance_agent() -> Any:
    """Compile and return the LangGraph application."""
    workflow = StateGraph(AgentState)

    # Register nodes
    workflow.add_node("analysis", analysis_node)
    workflow.add_node("implementation", implementation_node)
    workflow.add_node("execution", execution_node)
    workflow.add_node("verification", verification_node)
    workflow.add_node("iteration", iteration_node)

    # Linear edges
    workflow.set_entry_point("analysis")
    workflow.add_edge("analysis", "implementation")
    workflow.add_edge("implementation", "execution")
    workflow.add_edge("execution", "verification")

    # Conditional: verification → END or iteration
    workflow.add_conditional_edges(
        "verification",
        should_continue,
        {"end": END, "iteration": "iteration"},
    )

    # Iteration loops back to implementation
    workflow.add_edge("iteration", "implementation")

    # Compile (add checkpointer in production for resume support)
    app = workflow.compile()
    logger.info("compliance_agent_graph_compiled")
    return app


# ── Single-source runner ────────────────────────────────────────────

def run_compliance_agent(
    source_spec: Dict[str, Any],
    max_iterations: int = 5,
) -> Dict[str, Any]:
    """
    Run the autonomous agent for one compliance source.

    Returns the final AgentState dict with keys:
        success, final_count, quality_score, errors, messages, ...
    """
    agent = build_compliance_agent()

    initial_state: AgentState = {
        "source_spec": source_spec,
        "iteration_count": 0,
        "max_iterations": max_iterations,
        "errors": [],
        "messages": [],
        "success": False,
    }

    source_name = source_spec.get("name", source_spec.get("source_id", "unknown"))
    logger.info("agent_run_start", source=source_name)
    start = datetime.now(timezone.utc)

    final_state = agent.invoke(initial_state)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info(
        "agent_run_complete",
        source=source_name,
        success=final_state.get("success"),
        records=final_state.get("final_count"),
        quality=final_state.get("quality_score"),
        iterations=final_state.get("iteration_count"),
        elapsed=f"{elapsed:.1f}s",
    )

    return dict(final_state)


# ── Batch runner (all sources from YAML) ────────────────────────────

def load_sources(config_path: str = "config/compliance_sources.yaml") -> List[Dict[str, Any]]:
    """Load source specifications from the YAML config file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path.resolve()}")
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("sources", [])


def run_all_sources(
    config_path: str = "config/compliance_sources.yaml",
    max_iterations: int = 5,
    priority_filter: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Process every source in the config (optionally filtered by priority).

    Returns a list of result dicts, one per source.
    """
    sources = load_sources(config_path)
    if priority_filter is not None:
        sources = [s for s in sources if s.get("priority") == priority_filter]

    results: List[Dict[str, Any]] = []
    total = len(sources)

    for idx, spec in enumerate(sources, 1):
        name = spec.get("name", spec["source_id"])
        logger.info("batch_progress", current=idx, total=total, source=name)
        try:
            result = run_compliance_agent(spec, max_iterations=max_iterations)
        except Exception as exc:
            logger.error("batch_source_failed", source=name, error=str(exc))
            result = {
                "source_spec": spec,
                "success": False,
                "errors": [str(exc)],
                "final_count": 0,
                "quality_score": 0,
                "iteration_count": 0,
            }
        results.append(result)

    # Print summary
    _print_summary(results)
    return results


# ── Dashboard / summary ─────────────────────────────────────────────

def _print_summary(results: List[Dict[str, Any]]) -> None:
    total = len(results)
    successful = sum(1 for r in results if r.get("success"))
    failed = total - successful
    total_records = sum(r.get("final_count", 0) for r in results)
    avg_quality = (
        sum(r.get("quality_score", 0) for r in results) / total if total else 0
    )

    border = "=" * 62
    print(f"\n{border}")
    print("  AUTONOMOUS COMPLIANCE AGENT — EXECUTION SUMMARY")
    print(border)
    print(f"  Sources processed : {total}")
    print(f"  Successful        : {successful}")
    print(f"  Failed            : {failed}")
    print(f"  Total records     : {total_records:,}")
    print(f"  Avg quality       : {avg_quality:.1f}%")
    print(border)

    for r in results:
        spec = r.get("source_spec", {})
        name = spec.get("name", spec.get("source_id", "?"))
        ok = r.get("success")
        icon = "OK" if ok else "FAIL"
        count = r.get("final_count", 0)
        quality = r.get("quality_score", 0)
        iters = r.get("iteration_count", 0)
        print(f"  [{icon:>4}] {name:<42} {count:>6} records  {quality:>5.1f}%  iters={iters}")

    print(f"{border}\n")
