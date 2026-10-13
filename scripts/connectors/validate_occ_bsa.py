#!/usr/bin/env python3
"""
Validate OCC BSA/AML Connector — verify data format before DB insert.

Usage:
    python scripts/connectors/validate_occ_bsa.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.connectors.regulatory.usa.occ_bsa import OccBsaConnector


def validate():
    connector = OccBsaConnector()

    print("=" * 60)
    print("OCC BSA/AML Connector Validation")
    print("=" * 60)

    print("\n[1/4] Testing connection ...")
    reachable = connector.test_connection()
    print(f"  OCC website reachable: {reachable}")

    print("\n[2/4] Extracting rules ...")
    connector.connect()
    data = connector.extract_all_rules()
    connector.disconnect()

    required_keys = ["regulations", "requirements", "kpis", "test_scenarios"]
    for k in required_keys:
        if k not in data:
            print(f"  FAIL: Missing key '{k}'")
            return False
        print(f"  OK — {k}: {len(data[k])} records")

    # Validate regulation schema
    print("\n[3/4] Validating schemas ...")
    reg = data["regulations"][0]
    for field in ["regulation_id", "regulation_name", "category", "regulator", "source_url"]:
        if field not in reg:
            print(f"  FAIL: Regulation missing '{field}'")
            return False
    print(f"  OK — Regulation schema valid")

    req = data["requirements"][0]
    for field in ["requirement_id", "regulation_id", "requirement_text", "rule_type", "parameters"]:
        if field not in req:
            print(f"  FAIL: Requirement missing '{field}'")
            return False
    print(f"  OK — Requirement schema valid")

    kpi = data["kpis"][0]
    for field in ["kpi_id", "regulation_id", "kpi_name", "kpi_formula"]:
        if field not in kpi:
            print(f"  FAIL: KPI missing '{field}'")
            return False
    print(f"  OK — KPI schema valid")

    tc = data["test_scenarios"][0]
    for field in ["scenario_id", "regulation_id", "scenario_name", "given", "when_step", "then_step"]:
        if field not in tc:
            print(f"  FAIL: Test scenario missing '{field}'")
            return False
    print(f"  OK — Test scenario schema valid")

    # Print sample data
    print("\n[4/4] Data summary:")
    print("-" * 60)

    print("\n  Regulations:")
    for r in data["regulations"]:
        print(f"    {r['regulation_id']}: {r['regulation_name']}")

    print(f"\n  Requirements ({len(data['requirements'])}):")
    for r in data["requirements"]:
        threshold = f"${r['threshold_amount']:,.0f}" if r.get("threshold_amount") else "N/A"
        print(f"    {r['requirement_id']}: {r['rule_type']:<25} threshold={threshold}")

    print(f"\n  KPIs ({len(data['kpis'])}):")
    for k in data["kpis"]:
        print(f"    {k['kpi_id']}: {k['kpi_name']}")

    print(f"\n  Test Scenarios ({len(data['test_scenarios'])}):")
    for t in data["test_scenarios"]:
        print(f"    {t['scenario_id']}: {t['scenario_name']} [{t['priority']}]")

    print("\n" + "=" * 60)
    print(f"  Regulations:    {len(data['regulations'])}")
    print(f"  Requirements:   {len(data['requirements'])}")
    print(f"  KPIs:           {len(data['kpis'])}")
    print(f"  Test scenarios: {len(data['test_scenarios'])}")
    print(f"\n  ALL VALIDATIONS PASSED ✓")
    return True


if __name__ == "__main__":
    try:
        ok = validate()
        sys.exit(0 if ok else 1)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
