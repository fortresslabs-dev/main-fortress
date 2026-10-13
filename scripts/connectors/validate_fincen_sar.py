#!/usr/bin/env python3
"""
Validate FinCEN SAR Connector — verify data format before DB insert.

Usage:
    python scripts/connectors/validate_fincen_sar.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.connectors.regulatory.usa.fincen_sar import FincenSarConnector


def validate():
    connector = FincenSarConnector()

    print("=" * 60)
    print("FinCEN SAR Connector Validation")
    print("=" * 60)

    print("\n[1/4] Testing connection ...")
    reachable = connector.test_connection()
    print(f"  FinCEN website reachable: {reachable}")

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

    # Validate schemas
    print("\n[3/4] Validating schemas ...")

    reg = data["regulations"][0]
    for field in ["regulation_id", "regulation_name", "category", "regulator", "source_url"]:
        assert field in reg, f"Regulation missing '{field}'"
    print(f"  OK — Regulation schema valid")

    req = data["requirements"][0]
    for field in ["requirement_id", "regulation_id", "requirement_text", "rule_type", "parameters"]:
        assert field in req, f"Requirement missing '{field}'"
    print(f"  OK — Requirement schema valid")

    # Validate SAR triggers specifically
    triggers = [r for r in data["requirements"] if r["rule_type"] == "sar_trigger"]
    assert len(triggers) == 3, f"Expected 3 SAR triggers, got {len(triggers)}"
    amounts = sorted([t["threshold_amount"] for t in triggers])
    assert amounts == [0, 5000.00, 25000.00], f"SAR trigger amounts wrong: {amounts}"
    print(f"  OK — SAR trigger thresholds: $0 (insider), $5K (identified), $25K (unidentified)")

    tc = data["test_scenarios"][0]
    for field in ["scenario_id", "regulation_id", "scenario_name", "given", "when_step", "then_step"]:
        assert field in tc, f"Test scenario missing '{field}'"
    print(f"  OK — Test scenario schema valid")

    # Print summary
    print("\n[4/4] Data summary:")
    print("-" * 60)

    print("\n  Regulations:")
    for r in data["regulations"]:
        print(f"    {r['regulation_id']}: {r['regulation_name']}")

    print(f"\n  Requirements ({len(data['requirements'])}):")
    for r in data["requirements"]:
        threshold = f"${r['threshold_amount']:,.0f}" if r.get("threshold_amount") is not None else "N/A"
        print(f"    {r['requirement_id']}: {r['rule_type']:<22} threshold={threshold}")

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
