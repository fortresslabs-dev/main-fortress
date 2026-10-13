"""
LangGraph Node Functions for the Autonomous Compliance Agent.

Each function is a graph node that receives AgentState and returns
a partial state update dict.  The graph wires them together with
conditional edges (verification → end | iteration → implementation).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict

import requests as http_requests
import structlog

from src.agents.langgraph.state import AgentState
from src.agents.langgraph.utils import (
    analyze_csv_structure,
    analyze_html_structure,
    analyze_pdf_structure,
    analyze_xml_structure,
    build_fix_prompt,
    build_scraper_prompt,
    choose_strategy,
    clean_generated_code,
    detect_format,
    save_scraper_code,
    validate_extracted_data,
)

logger = structlog.get_logger(__name__)

# ── LLM helpers — Bedrock or direct Anthropic API ───────────────────

def _invoke_bedrock(prompt: str, max_tokens: int = 8000, temperature: float = 0.2) -> str:
    """Synchronous Bedrock call."""
    import boto3

    from src.utils.config import settings
    region = os.getenv("AWS_REGION", settings.AWS_REGION or "us-east-1")
    # settings.BEDROCK_MODEL is overridable via env / SSM / Secrets Manager
    model_id = os.getenv("BEDROCK_MODEL") or settings.BEDROCK_MODEL
    client = boto3.client("bedrock-runtime", region_name=region)

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    response = client.invoke_model(modelId=model_id, body=body)
    response_body = json.loads(response["body"].read())
    text = response_body["content"][0]["text"]
    usage = response_body.get("usage", {})
    logger.info("bedrock_invoked", input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
    return text


def _invoke_anthropic(prompt: str, max_tokens: int = 8000, temperature: float = 0.2) -> str:
    """Synchronous direct Anthropic API call."""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    model_id = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    logger.info("anthropic_invoked", input_tokens=message.usage.input_tokens, output_tokens=message.usage.output_tokens)
    return text


def _invoke_llm(prompt: str, max_tokens: int = 8000, temperature: float = 0.2) -> str:
    """Dispatch to Bedrock or Anthropic API based on LLM_PROVIDER env var."""
    provider = os.getenv("LLM_PROVIDER", "bedrock")
    if provider == "anthropic":
        return _invoke_anthropic(prompt, max_tokens, temperature)
    return _invoke_bedrock(prompt, max_tokens, temperature)


# =====================================================================
# NODE 1 — ANALYSIS
# =====================================================================

def analysis_node(state: AgentState) -> Dict[str, Any]:
    """Download source document, detect format, analyse structure, pick strategy."""
    spec = state["source_spec"]
    logger.info("analysis_start", source=spec["name"], url=spec["url"])

    # Download
    resp = http_requests.get(spec["url"], timeout=120, headers={"User-Agent": "FortressLabsComplianceAgent/1.0"})
    resp.raise_for_status()
    content = resp.content
    content_type = resp.headers.get("Content-Type", "")

    # Detect format (prefer spec, fallback to detection)
    doc_format = spec.get("format") or detect_format(spec["url"], content_type)

    # Analyse
    analysers = {
        "pdf": analyze_pdf_structure,
        "csv": analyze_csv_structure,
        "html": analyze_html_structure,
        "xml": analyze_xml_structure,
    }
    analyse_fn = analysers.get(doc_format, analyze_html_structure)
    structure = analyse_fn(content)

    strategy = choose_strategy(doc_format, structure)

    logger.info(
        "analysis_complete",
        format=doc_format,
        strategy=strategy,
        estimated=structure.get("estimated_count"),
    )

    return {
        "document_content": content,
        "document_format": doc_format,
        "document_structure": structure,
        "extraction_strategy": strategy,
        "messages": [{"role": "system", "content": f"Analysed {spec['name']}: format={doc_format}, strategy={strategy}"}],
    }


# =====================================================================
# NODE 2 — IMPLEMENTATION (code generation via Bedrock)
# =====================================================================

def implementation_node(state: AgentState) -> Dict[str, Any]:
    """Generate a Python scraper script using Claude via AWS Bedrock."""
    spec = state["source_spec"]
    logger.info("implementation_start", source=spec["name"])

    prompt = build_scraper_prompt(
        source_spec=spec,
        doc_format=state["document_format"],
        strategy=state["extraction_strategy"],
        structure=state["document_structure"],
    )

    raw_code = _invoke_llm(prompt)
    scraper_code = clean_generated_code(raw_code)

    iteration = state.get("iteration_count", 0)
    suffix = f"_v{iteration}" if iteration > 0 else ""
    rel_path = f"src/scrapers/usa/generated_{spec['source_id']}{suffix}.py"
    abs_path = save_scraper_code(scraper_code, rel_path)

    logger.info("implementation_complete", path=abs_path, code_len=len(scraper_code))

    return {
        "scraper_code": scraper_code,
        "scraper_file_path": abs_path,
        "messages": [{"role": "assistant", "content": f"Generated scraper ({len(scraper_code)} chars) → {rel_path}"}],
    }


# =====================================================================
# NODE 3 — EXECUTION (run generated scraper in subprocess)
# =====================================================================

EXECUTION_TIMEOUT = int(os.getenv("AGENT_SCRAPER_TIMEOUT", "300"))

def execution_node(state: AgentState) -> Dict[str, Any]:
    """Execute the generated scraper in an isolated subprocess."""
    spec = state["source_spec"]
    logger.info("execution_start", source=spec["name"], script=state["scraper_file_path"])

    start = datetime.now(timezone.utc)

    try:
        result = subprocess.run(
            ["python", state["scraper_file_path"]],
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        if result.returncode == 0:
            stdout = result.stdout.strip()
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                # Try to find JSON in the output
                match = __import__("re").search(r"\{.*\}", stdout, __import__("re").DOTALL)
                data = json.loads(match.group()) if match else {"requirements": []}

            extracted = data.get("requirements", [])
            logger.info("execution_success", records=len(extracted), elapsed=f"{elapsed:.1f}s")

            return {
                "execution_result": {"success": True, "stdout": stdout[:5000], "stderr": result.stderr[:2000]},
                "extracted_data": extracted,
                "messages": [{"role": "system", "content": f"Scraper succeeded: {len(extracted)} records in {elapsed:.1f}s"}],
            }
        else:
            error_msg = result.stderr[:3000] or result.stdout[:3000]
            logger.warning("execution_failed", error=error_msg[:200])

            return {
                "execution_result": {"success": False, "stdout": result.stdout[:2000], "stderr": error_msg},
                "extracted_data": [],
                "errors": [f"Scraper exit code {result.returncode}: {error_msg[:500]}"],
                "messages": [{"role": "system", "content": f"Scraper failed (exit {result.returncode})"}],
            }

    except subprocess.TimeoutExpired:
        elapsed = EXECUTION_TIMEOUT
        logger.warning("execution_timeout", timeout=EXECUTION_TIMEOUT)
        return {
            "execution_result": {"success": False, "stderr": f"Timeout after {EXECUTION_TIMEOUT}s"},
            "extracted_data": [],
            "errors": [f"Execution timeout ({EXECUTION_TIMEOUT}s)"],
            "messages": [{"role": "system", "content": "Scraper timed out"}],
        }


# =====================================================================
# NODE 4 — VERIFICATION
# =====================================================================

def verification_node(state: AgentState) -> Dict[str, Any]:
    """Validate extracted data against expected schema and quality metrics."""
    spec = state["source_spec"]
    data = state.get("extracted_data") or []
    expected = spec.get("expected_count", 0)
    required_fields = spec.get("fields", ["requirement_id", "requirement_text"])

    logger.info("verification_start", source=spec["name"], records=len(data))

    # If execution itself failed, short-circuit
    exec_result = state.get("execution_result", {})
    if not exec_result.get("success"):
        return {
            "validation_result": {"success": False, "errors": state.get("errors", ["Execution failed"])},
            "quality_score": 0.0,
            "success": False,
            "final_count": 0,
            "messages": [{"role": "system", "content": "Verification skipped — execution failed"}],
        }

    report = validate_extracted_data(data, expected, required_fields)

    logger.info(
        "verification_complete",
        success=report["success"],
        quality=f"{report['quality_score']:.1f}%",
        count=report["actual_count"],
    )

    errors_to_add = report["errors"] if not report["success"] else []

    return {
        "validation_result": report,
        "quality_score": report["quality_score"],
        "success": report["success"],
        "final_count": report["actual_count"],
        "errors": errors_to_add,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Verification {'PASSED' if report['success'] else 'FAILED'}: "
                    f"{report['actual_count']} records, quality {report['quality_score']:.1f}%"
                ),
            }
        ],
    }


# =====================================================================
# NODE 5 — ITERATION (self-healing)
# =====================================================================

def iteration_node(state: AgentState) -> Dict[str, Any]:
    """Analyse errors with Claude and regenerate a fixed scraper."""
    iteration = state.get("iteration_count", 0) + 1
    spec = state["source_spec"]
    logger.info("iteration_start", iteration=iteration, source=spec["name"])

    prompt = build_fix_prompt(
        errors=state.get("errors", []),
        scraper_code=state.get("scraper_code", ""),
        source_spec=spec,
        doc_format=state.get("document_format", "html"),
        strategy=state.get("extraction_strategy", "section_based"),
    )

    raw_code = _invoke_llm(prompt)
    fixed_code = clean_generated_code(raw_code)

    rel_path = f"src/scrapers/usa/generated_{spec['source_id']}_v{iteration}.py"
    abs_path = save_scraper_code(fixed_code, rel_path)

    logger.info("iteration_complete", iteration=iteration, path=abs_path)

    return {
        "scraper_code": fixed_code,
        "scraper_file_path": abs_path,
        "iteration_count": iteration,
        "errors": [],  # clear for the retry cycle (Annotated list will reset in next add)
        "messages": [{"role": "assistant", "content": f"Iteration {iteration}: generated fixed scraper → {rel_path}"}],
    }


# =====================================================================
# ROUTING — conditional edge after verification
# =====================================================================

def should_continue(state: AgentState) -> str:
    """Return 'end' on success or max-iterations, otherwise 'iteration'."""
    if state.get("success"):
        logger.info("routing_decision", decision="end", reason="success")
        return "end"

    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", 5)
    if iteration >= max_iter:
        logger.warning("routing_decision", decision="end", reason="max_iterations_reached")
        return "end"

    logger.info("routing_decision", decision="iteration", current=iteration)
    return "iteration"
