#!/usr/bin/env python3
"""Run Pipeline 2 six-agent generation for all active OFAC/SDN rules.

Outputs:
- Rule snapshot JSON in SDN_test/rules_snapshot/
- Per-rule workflow artifacts in SDN_test/artifacts/workflow_results/
- Generated pytest files in a separate folder (default: SDN_generated_tests/)
- Summary report JSON in SDN_test/artifacts/reports/
"""

from __future__ import annotations

import argparse
import asyncio
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

# Ensure `src` imports resolve when script is run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.orchestration.workflow_engine import WorkflowOrchestrator
from src.services.database import close_db, get_db, init_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _build_story_text(rule: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Generate pytest coverage for OFAC/SDN compliance rule.",
            f"Rule ID: {rule['rule_id']}",
            f"Regulator: {rule.get('regulator') or 'unknown'}",
            f"CFR: {rule.get('cfr_citation') or 'N/A'}",
            f"Category: {rule.get('category') or 'N/A'}",
            "",
            "Requirement:",
            (rule.get("rule_text") or "")[:1200],
            "",
            "Acceptance:",
            "1. Include exact-match SDN block coverage.",
            "2. Include fuzzy-match review coverage (>=85 score).",
            "3. Include false-positive release scenario.",
            "4. Include audit trail assertions for OFAC decision path.",
        ]
    )


async def fetch_sdn_rules(limit: int | None) -> list[dict[str, Any]]:
    base_sql = """
        SELECT
            rule_id,
            regulator,
            cfr_citation,
            category,
            domain,
            geography,
            enforcement_risk_score,
            rule_text
        FROM compliance_rules
        WHERE is_active = TRUE
          AND geography = 'usa'
          AND (
                lower(coalesce(regulator, '')) LIKE '%ofac%'
             OR lower(coalesce(rule_text, '')) LIKE '%sdn%'
             OR lower(coalesce(rule_text, '')) LIKE '%sanction%'
             OR lower(coalesce(category, '')) LIKE '%ofac%'
             OR lower(coalesce(domain, '')) LIKE '%sanction%'
          )
        ORDER BY enforcement_risk_score DESC NULLS LAST, rule_id ASC
    """
    sql = base_sql if limit is None else f"{base_sql}\nLIMIT {int(limit)}"

    async with get_db() as session:
        rows = (await session.execute(text(sql))).mappings().all()

    return [dict(row) for row in rows]


@dataclass
class RunnerConfig:
    rules_output: Path
    workflow_output_dir: Path
    generated_tests_dir: Path
    summary_output: Path
    pytest_output: Path
    limit: int | None
    stop_on_error: bool
    run_pytest: bool
    pytest_args: list[str]
    max_generation_retries: int
    save_raw_failed: bool
    raw_failed_dir: Path
    run_accuracy_eval: bool
    accuracy_output: Path


def _try_ast_parse(code: str) -> tuple[bool, str | None]:
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as exc:
        line = exc.lineno or 0
        return False, f"line {line}: {exc.msg}"


def _truncate_to_valid_python(code: str, min_lines: int = 40) -> tuple[str, dict[str, Any]]:
    """Best-effort salvage: trim trailing lines until code parses."""
    original = code.rstrip() + "\n"
    ok, err = _try_ast_parse(original)
    if ok:
        return original, {
            "strategy": "none",
            "repaired": False,
            "removed_lines": 0,
            "reason": None,
        }

    lines = original.splitlines()
    for end in range(len(lines) - 1, min_lines - 1, -1):
        candidate = "\n".join(lines[:end]).rstrip() + "\n"
        ok, _ = _try_ast_parse(candidate)
        if ok:
            return candidate, {
                "strategy": "truncate_to_parse",
                "repaired": True,
                "removed_lines": len(lines) - end,
                "reason": err,
            }

    return original, {
        "strategy": "failed",
        "repaired": False,
        "removed_lines": 0,
        "reason": err,
    }


def _count_scenario_coverage(test_code: str, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    scenario_names = [str(s.get("name") or "").strip() for s in scenarios if s.get("name")]
    if not scenario_names:
        return {
            "scenarios_total": 0,
            "scenarios_covered": 0,
            "coverage_pct": 0.0,
        }

    code_lower = test_code.lower()
    covered = 0
    for name in scenario_names:
        if name.lower() in code_lower:
            covered += 1

    pct = round((covered / len(scenario_names)) * 100.0, 2)
    return {
        "scenarios_total": len(scenario_names),
        "scenarios_covered": covered,
        "coverage_pct": pct,
    }


def _compliance_keyword_score(test_code: str) -> dict[str, Any]:
    keywords = [
        "ofac",
        "sdn",
        "sanction",
        "audit",
        "blocked",
        "review",
        "fuzzy",
        "false_positive",
    ]
    code_lower = test_code.lower()
    hit = [k for k in keywords if k in code_lower]
    score = round((len(hit) / len(keywords)) * 100.0, 2)
    return {
        "keyword_hits": hit,
        "keyword_total": len(keywords),
        "score_pct": score,
    }


def _run_pytest_for_file(path: Path) -> dict[str, Any]:
    command = ["pytest", str(path), "-q", "--maxfail=1"]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "command": " ".join(shlex.quote(x) for x in command),
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _evaluate_accuracy(
    run_results: list[dict[str, Any]],
    accuracy_output: Path,
) -> dict[str, Any]:
    """Build a compact accuracy report per generated test file."""
    rows: list[dict[str, Any]] = []
    syntax_pass_count = 0
    pytest_pass_count = 0

    for item in run_results:
        test_path_raw = item.get("generated_test")
        if not test_path_raw:
            continue

        test_path = Path(test_path_raw)
        if not test_path.exists():
            continue

        code = test_path.read_text(encoding="utf-8")
        syntax_ok, syntax_error = _try_ast_parse(code)
        if syntax_ok:
            syntax_pass_count += 1

        scenarios = item.get("scenarios") or []
        scenario_cov = _count_scenario_coverage(code, scenarios)
        compliance = _compliance_keyword_score(code)
        pytest_result = _run_pytest_for_file(test_path)
        if pytest_result["passed"]:
            pytest_pass_count += 1

        row = {
            "rule_id": item.get("rule_id"),
            "generated_test": str(test_path),
            "syntax_pass": syntax_ok,
            "syntax_error": syntax_error,
            "scenario_coverage": scenario_cov,
            "compliance_keyword_score": compliance,
            "pytest": {
                "passed": pytest_result["passed"],
                "exit_code": pytest_result["exit_code"],
            },
            "composite_score": round(
                (
                    (100.0 if syntax_ok else 0.0) * 0.35
                    + scenario_cov["coverage_pct"] * 0.25
                    + compliance["score_pct"] * 0.2
                    + (100.0 if pytest_result["passed"] else 0.0) * 0.2
                ),
                2,
            ),
        }
        rows.append(row)

    rows.sort(key=lambda x: x["composite_score"], reverse=True)
    total = len(rows)
    report = {
        "generated_at": _utc_now(),
        "total_tests": total,
        "syntax_pass_rate": round((syntax_pass_count / total) * 100.0, 2) if total else 0.0,
        "pytest_pass_rate": round((pytest_pass_count / total) * 100.0, 2) if total else 0.0,
        "leaderboard": rows,
    }
    _write_json(accuracy_output, report)
    return report


async def run(config: RunnerConfig) -> int:
    os.environ.setdefault("PIPELINE_MODE", "pipeline2")
    started_at = _utc_now()

    await init_db()
    try:
        rules = await fetch_sdn_rules(config.limit)
        _write_json(config.rules_output, rules)

        if not rules:
            print("No active OFAC/SDN rules found.")
            summary = {
                "started_at": started_at,
                "finished_at": _utc_now(),
                "rule_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "results": [],
                "notes": "Query returned no SDN rules."
            }
            _write_json(config.summary_output, summary)
            return 0

        orchestrator = WorkflowOrchestrator()
        results: list[dict[str, Any]] = []

        for i, rule in enumerate(rules, start=1):
            workflow_id = str(uuid.uuid4())
            rule_id = str(rule.get("rule_id") or f"unknown_{i}")
            jira_key = f"SDNTEST-{i:04d}"
            safe_rule = _safe_name(rule_id)

            try:
                payload = {
                    "workflow_id": workflow_id,
                    "key": jira_key,
                    "description": _build_story_text(rule),
                }
                workflow_result = await orchestrator.execute_workflow(payload)

                test_code = workflow_result.get("test_code") or ""
                scenarios = workflow_result.get("scenarios") or []
                understanding = workflow_result.get("understanding") or {}

                repaired_code, repair_meta = _truncate_to_valid_python(test_code)
                syntax_ok, syntax_error = _try_ast_parse(repaired_code)

                retries_used = 0
                retry_errors: list[str] = []
                while (not syntax_ok) and retries_used < config.max_generation_retries:
                    retries_used += 1
                    try:
                        regen = await orchestrator.generation.execute(
                            {"workflow_id": workflow_id, "jira_key": jira_key},
                            {
                                "workflow_id": workflow_id,
                                "jira_key": jira_key,
                                "scenarios": scenarios,
                                "understanding": understanding,
                            },
                        )
                        regen_code = regen.get("test_code") or ""
                        repaired_code, repair_meta = _truncate_to_valid_python(regen_code)
                        syntax_ok, syntax_error = _try_ast_parse(repaired_code)
                    except Exception as retry_exc:
                        retry_errors.append(str(retry_exc))

                validation = workflow_result.get("validation") or {}
                if syntax_ok and repaired_code.strip():
                    validation = await orchestrator.validation.execute(
                        {"workflow_id": workflow_id},
                        {
                            "workflow_id": workflow_id,
                            "test_code": repaired_code,
                            "scenarios": scenarios,
                        },
                    )

                test_path = config.generated_tests_dir / f"test_sdn_{safe_rule}.py"
                if syntax_ok and repaired_code.strip():
                    test_path.parent.mkdir(parents=True, exist_ok=True)
                    test_path.write_text(repaired_code, encoding="utf-8")
                elif config.save_raw_failed and test_code:
                    raw_path = config.raw_failed_dir / f"test_sdn_{safe_rule}.py"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_text(test_code, encoding="utf-8")

                artifact_path = config.workflow_output_dir / f"{safe_rule}.json"
                workflow_result["repair"] = {
                    "syntax_ok": syntax_ok,
                    "syntax_error": syntax_error,
                    "meta": repair_meta,
                    "generation_retries_used": retries_used,
                    "generation_retry_errors": retry_errors,
                }
                workflow_result["validation"] = validation
                _write_json(artifact_path, workflow_result)

                results.append(
                    {
                        "rule_id": rule_id,
                        "workflow_id": workflow_id,
                        "jira_key": jira_key,
                        "status": "success" if syntax_ok else "invalid_generated_code",
                        "quality_score": validation.get("quality_score"),
                        "is_valid": validation.get("is_valid"),
                        "generated_test": str(test_path) if syntax_ok else None,
                        "raw_generated_test": str(config.raw_failed_dir / f"test_sdn_{safe_rule}.py") if (not syntax_ok and config.save_raw_failed and test_code) else None,
                        "artifact": str(artifact_path),
                        "repair": workflow_result["repair"],
                        "scenarios": scenarios,
                    }
                )
                if syntax_ok:
                    print(f"[{i}/{len(rules)}] SUCCESS {rule_id}")
                else:
                    print(f"[{i}/{len(rules)}] INVALID_CODE {rule_id} ({syntax_error})")
            except Exception as exc:
                results.append(
                    {
                        "rule_id": rule_id,
                        "workflow_id": workflow_id,
                        "jira_key": jira_key,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                print(f"[{i}/{len(rules)}] FAILED  {rule_id}: {exc}")
                if config.stop_on_error:
                    break

        pytest_report: dict[str, Any] | None = None
        if config.run_pytest:
            command = ["pytest", str(config.generated_tests_dir), *config.pytest_args]
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
            pytest_report = {
                "command": " ".join(shlex.quote(x) for x in command),
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
            _write_json(config.pytest_output, pytest_report)

        accuracy_report = None
        if config.run_accuracy_eval:
            accuracy_report = _evaluate_accuracy(results, config.accuracy_output)

        success_count = sum(1 for item in results if item["status"] == "success")
        summary = {
            "started_at": started_at,
            "finished_at": _utc_now(),
            "rule_count": len(rules),
            "success_count": success_count,
            "failure_count": len(results) - success_count,
            "rules_snapshot": str(config.rules_output),
            "generated_tests_dir": str(config.generated_tests_dir),
            "workflow_output_dir": str(config.workflow_output_dir),
            "raw_failed_dir": str(config.raw_failed_dir) if config.save_raw_failed else None,
            "pytest_report": str(config.pytest_output) if pytest_report is not None else None,
            "accuracy_report": str(config.accuracy_output) if accuracy_report is not None else None,
            "results": results,
        }
        _write_json(config.summary_output, summary)

        print(f"Completed: {success_count}/{len(results)} succeeded")
        print(f"Summary: {config.summary_output}")
        return 0 if (len(results) - success_count) == 0 else 1
    finally:
        await close_db()


def parse_args() -> RunnerConfig:
    parser = argparse.ArgumentParser(
        description="Generate SDN pytest suite via Pipeline 2 six-agent workflow"
    )
    parser.add_argument(
        "--rules-output",
        default="SDN_test/rules_snapshot/ofac_sdn_rules.json",
        help="JSON output path for fetched SDN rules",
    )
    parser.add_argument(
        "--workflow-output-dir",
        default="SDN_test/artifacts/workflow_results",
        help="Directory to store per-rule workflow artifacts",
    )
    parser.add_argument(
        "--generated-tests-dir",
        default="SDN_generated_tests",
        help="Directory for generated python pytest files",
    )
    parser.add_argument(
        "--summary-output",
        default="SDN_test/artifacts/reports/run_summary.json",
        help="JSON output path for execution summary",
    )
    parser.add_argument(
        "--pytest-output",
        default="SDN_test/artifacts/reports/pytest_report.json",
        help="JSON output path for pytest stdout/stderr report",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process only first N SDN rules")
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after first failed rule instead of continuing",
    )
    parser.add_argument(
        "--run-pytest",
        action="store_true",
        help="Run pytest on generated tests after workflow execution",
    )
    parser.add_argument(
        "--pytest-args",
        default="-q",
        help="Extra args for pytest, e.g. '-q --maxfail=1'",
    )
    parser.add_argument(
        "--max-generation-retries",
        type=int,
        default=1,
        help="Retry generation N times if produced test code is not syntactically valid",
    )
    parser.add_argument(
        "--save-raw-failed",
        action="store_true",
        help="Store raw invalid generated tests under raw-failed directory",
    )
    parser.add_argument(
        "--raw-failed-dir",
        default="SDN_test/artifacts/raw_generated_tests",
        help="Directory for raw invalid generated test files",
    )
    parser.add_argument(
        "--run-accuracy-eval",
        action="store_true",
        help="Compute syntax/coverage/compliance/pytest leaderboard report",
    )
    parser.add_argument(
        "--accuracy-output",
        default="SDN_test/artifacts/reports/accuracy_report.json",
        help="JSON output path for accuracy leaderboard report",
    )

    args = parser.parse_args()
    return RunnerConfig(
        rules_output=Path(args.rules_output),
        workflow_output_dir=Path(args.workflow_output_dir),
        generated_tests_dir=Path(args.generated_tests_dir),
        summary_output=Path(args.summary_output),
        pytest_output=Path(args.pytest_output),
        limit=args.limit,
        stop_on_error=args.stop_on_error,
        run_pytest=args.run_pytest,
        pytest_args=shlex.split(args.pytest_args),
        max_generation_retries=max(0, args.max_generation_retries),
        save_raw_failed=args.save_raw_failed,
        raw_failed_dir=Path(args.raw_failed_dir),
        run_accuracy_eval=args.run_accuracy_eval,
        accuracy_output=Path(args.accuracy_output),
    )


def main() -> int:
    cfg = parse_args()
    return asyncio.run(run(cfg))


if __name__ == "__main__":
    sys.exit(main())
