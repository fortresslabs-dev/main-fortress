#!/usr/bin/env python3
"""
Validate OFAC SDN Scraper — run locally to verify data format before DB insert.

Usage:
    python scripts/connectors/validate_ofac_sdn.py

What it does:
    1. Downloads real SDN CSV from treasury.gov
    2. Parses entries
    3. Validates data structure matches DB schema
    4. Prints sample data + summary stats
    5. Exits 0 on success, 1 on failure
"""
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.connectors.regulatory.usa.ofac_sdn import OfacSdnConnector


def validate():
    connector = OfacSdnConnector()

    # Step 1: Test connection
    print("=" * 60)
    print("OFAC SDN Scraper Validation")
    print("=" * 60)

    print("\n[1/5] Testing connection to treasury.gov ...")
    if not connector.test_connection():
        print("FAIL: Cannot reach OFAC SDN URL")
        return False
    print("  OK — URL reachable")

    # Step 2: Connect + extract
    print("\n[2/5] Downloading & parsing SDN CSV ...")
    connector.connect()
    data = connector.extract_all_rules()
    connector.disconnect()

    # Step 3: Validate structure
    print("\n[3/5] Validating data structure ...")

    required_keys = ["sdn_entries", "regulations", "requirements", "kpis", "test_scenarios"]
    for k in required_keys:
        if k not in data:
            print(f"  FAIL: Missing key '{k}'")
            return False
        print(f"  OK — {k}: {len(data[k])} records")

    # Validate SDN entries schema
    sdn_entries = data["sdn_entries"]
    if len(sdn_entries) == 0:
        print("  FAIL: Zero SDN entries parsed")
        return False

    sdn_fields = {"sdn_id", "uid", "entity_type", "full_name", "sdn_type", "programs", "title", "remarks", "last_updated"}
    sample = sdn_entries[0]
    missing = sdn_fields - set(sample.keys())
    if missing:
        print(f"  FAIL: SDN entry missing fields: {missing}")
        return False
    print(f"  OK — SDN entry schema valid ({len(sdn_fields)} fields)")

    # Validate regulations
    reg = data["regulations"][0]
    reg_fields = {"regulation_id", "regulation_name", "category", "regulator", "geography", "source_url"}
    missing = reg_fields - set(reg.keys())
    if missing:
        print(f"  FAIL: Regulation record missing fields: {missing}")
        return False
    print(f"  OK — Regulation schema valid")

    # Validate requirements
    req = data["requirements"][0]
    req_fields = {"requirement_id", "regulation_id", "requirement_text", "rule_type", "parameters"}
    missing = req_fields - set(req.keys())
    if missing:
        print(f"  FAIL: Requirement missing fields: {missing}")
        return False
    print(f"  OK — Requirement schema valid")

    # Step 4: Print sample data
    print("\n[4/5] Sample SDN entries (first 5):")
    print("-" * 60)
    for entry in sdn_entries[:5]:
        print(f"  UID: {entry['sdn_id']:>8} | Type: {entry['entity_type']:<12} | Name: {entry['full_name'][:50]}")
        print(f"           Programs: {entry['programs'][:60]}")

    # Entity type distribution
    types = {}
    for e in sdn_entries:
        t = e["entity_type"]
        types[t] = types.get(t, 0) + 1

    print(f"\n  Entity type distribution:")
    for t, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {t:<15} {count:>6}")

    # Step 5: Validate test scenarios
    print("\n[5/5] Test scenarios:")
    print("-" * 60)
    for ts in data["test_scenarios"]:
        print(f"  {ts['scenario_id']}: {ts['scenario_name']}")
        print(f"    Given: {ts['given']}")
        print(f"    When:  {ts['when_step']}")
        print(f"    Then:  {ts['then_step']}")
        print()

    # Final summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  SDN entries:    {len(sdn_entries)}")
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
