"""
Agent State Schema for the LangGraph Compliance Agent.

Defines the TypedDict that flows through every node in the graph.
Uses Annotated + operator.add so list fields accumulate across iterations.
"""
from __future__ import annotations

import operator
from typing import Any, Dict, List, Optional, Annotated, TypedDict


class AgentState(TypedDict, total=False):
    """State that flows through the LangGraph compliance agent."""

    # ── Input ───────────────────────────────────────────────────────
    source_spec: Dict[str, Any]

    # ── Phase 1: Analysis ───────────────────────────────────────────
    document_content: Optional[bytes]
    document_format: Optional[str]
    document_structure: Optional[Dict[str, Any]]
    extraction_strategy: Optional[str]

    # ── Phase 2: Implementation ─────────────────────────────────────
    scraper_code: Optional[str]
    scraper_file_path: Optional[str]

    # ── Phase 3: Execution ──────────────────────────────────────────
    execution_result: Optional[Dict[str, Any]]
    extracted_data: Optional[List[Dict[str, Any]]]

    # ── Phase 4: Verification ───────────────────────────────────────
    validation_result: Optional[Dict[str, Any]]
    quality_score: Optional[float]

    # ── Phase 5: Iteration ──────────────────────────────────────────
    errors: Annotated[List[str], operator.add]
    iteration_count: int
    max_iterations: int

    # ── Output ──────────────────────────────────────────────────────
    success: bool
    final_count: Optional[int]
    messages: Annotated[List[Dict[str, str]], operator.add]
