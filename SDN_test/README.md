# SDN Test Framework

This workspace section is dedicated to OFAC/SDN accuracy testing using the Pipeline 2 six-agent flow.

## Created Assets
- Runner script: `scripts/phase2/run_sdn_pipeline2_test_framework.py`
- Action plan: `SDN_test/ACTION_ITEMS.md`
- Rule snapshot output: `SDN_test/rules_snapshot/`
- Workflow artifacts: `SDN_test/artifacts/workflow_results/`
- Reports: `SDN_test/artifacts/reports/`
- Generated tests (separate folder): `SDN_generated_tests/`

## Quick Start

```bash
python scripts/phase2/run_sdn_pipeline2_test_framework.py --limit 3
```

After run:
- Check summary: `SDN_test/artifacts/reports/run_summary.json`
- Check generated tests: `SDN_generated_tests/`
- Check workflow outputs: `SDN_test/artifacts/workflow_results/`
