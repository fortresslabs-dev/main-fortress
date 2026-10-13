# SDN Test Action Items (Pipeline 2, 6-Agent)

## Folder Plan
- `SDN_test/rules_snapshot/`: fetched OFAC/SDN rule snapshots (`.json`).
- `SDN_test/artifacts/workflow_results/`: full per-rule 6-agent workflow outputs.
- `SDN_test/artifacts/reports/`: run summary and pytest execution reports.
- `SDN_generated_tests/`: generated Python pytest files (separate folder).

## Execution Plan
1. Fetch all active OFAC/SDN rules from `compliance_rules` table.
2. Save rule snapshot to `SDN_test/rules_snapshot/ofac_sdn_rules.json`.
3. For each rule, build a compliance story prompt and run Pipeline 2 six-agent workflow:
   - Agent 1: Discovery
   - Agent 2: Understanding
   - Agent 3: Framework
   - Agent 4: Generation
   - Agent 5: Validation
   - Agent 6: Execution
4. Save generated Python tests in `SDN_generated_tests/`.
5. Save workflow JSON artifacts per rule in `SDN_test/artifacts/workflow_results/`.
6. Generate a run summary report in `SDN_test/artifacts/reports/run_summary.json`.
7. Optionally run pytest on generated tests and save output to `SDN_test/artifacts/reports/pytest_report.json`.

## Run Commands

```bash
# Process all active SDN rules and generate tests
python scripts/phase2/run_sdn_pipeline2_test_framework.py

# Dry batch for first 5 SDN rules
python scripts/phase2/run_sdn_pipeline2_test_framework.py --limit 5

# Generate tests and execute pytest on generated suite
python scripts/phase2/run_sdn_pipeline2_test_framework.py --run-pytest --pytest-args "-q --maxfail=1"
```

## Notes
- Ensure environment is configured for Pipeline 2 (`PIPELINE_MODE=pipeline2`).
- Agent 4 uses Bedrock generation; valid AWS/Bedrock credentials are required.
- Agent 6 may attempt GitHub/JIRA actions depending on configured tokens.
