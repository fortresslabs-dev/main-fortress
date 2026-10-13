# Source 3: OFAC SDN — Sanctions Screening

## What We Are Targeting

Extract **OFAC SDN list** and screening rules from the Office of Foreign Assets Control covering:
- Real-time SDN list screening for all wire transfers
- Customer onboarding screening against SDN
- Fuzzy name matching (90% Levenshtein threshold)
- 50% ownership rule (entities owned by SDN-listed persons)
- SDN list freshness (must update within 24 hours of publication)

**Test cases:** 5 (TC-007 → TC-011) — 14% of all test cases

---

## Source of Information

| Source | URL | Format |
|--------|-----|--------|
| SDN CSV (machine-readable) | https://www.treasury.gov/ofac/downloads/sdn.csv | CSV (~18K entries) |
| SDN XML | https://www.treasury.gov/ofac/downloads/sdn.xml | XML |
| SDN Human-Readable | https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists | HTML/PDF |
| 50% Rule FAQ | https://ofac.treasury.gov/faqs/topic/1501 | HTML |
| Sanctions Programs | https://ofac.treasury.gov/sanctions-programs-and-country-information | HTML |

---

## Current Implementation Status: ✅ DONE

**Connector file:** `src/connectors/regulatory/usa/ofac_sdn.py`

### What's implemented
- 1 regulation (SDN List Screening Requirement)
- 4 requirements (wire screening, onboarding screening, 50% rule, list freshness)
- 3 KPIs (screening coverage, false positive rate, list freshness)
- 5 test scenarios in BDD format
- **Live SDN CSV download** — parses ~18,714 entries from treasury.gov
- Entity type normalization (individual / entity / vessel / aircraft)
- OFAC `-0-` placeholder handling

### Data extraction method
**Live CSV download + structured rules** — The SDN list is downloaded directly from treasury.gov (no scraping needed). Screening rules and thresholds are coded from OFAC guidance.

---

## Do We Need AI / PDF Extraction?

**No.** OFAC provides machine-readable data (CSV, XML). No AI needed.

| Data | Method | Notes |
|------|--------|-------|
| SDN entries | HTTP GET → CSV parse | ~18K entries, updates daily |
| Screening rules | Hardcoded | Well-known OFAC guidance |
| 50% rule | Hardcoded | From OFAC FAQ Topic 1501 |

---

## Validation Checklist

```
✅ 1 regulation (OFAC-SDN-001)
✅ 4 requirements (SCREEN-001, SCREEN-002, 50PERCENT-001, DAILY-001)
✅ 3 KPIs with formulas and alert thresholds
✅ 5 test scenarios (TC-007 to TC-011)
✅ SDN CSV downloads successfully (~18,714 entries)
✅ Entity types normalized (individual, entity, vessel, aircraft)
✅ OFAC '-0-' placeholders cleaned
✅ test_connection() returns True (treasury.gov reachable)
```

---

## Important Note on SDN Data

The full 18K SDN download is for **production screening systems**. For our test automation platform, the important deliverables are:
- The **4 screening requirements** (rules the bank's system must enforce)
- The **3 KPIs** (metrics to monitor)
- The **5 test scenarios** (BDD tests to generate via Agent 4)
- A **small sample** of SDN entries (for fuzzy matching tests)

---

## Test Scenarios Covered

| ID | Name | Priority | Regulation |
|----|------|----------|------------|
| TC-007 | Wire to SDN-listed individual blocked | critical | OFAC-SDN-001 |
| TC-008 | Fuzzy name match (92%) triggers review | high | OFAC-SDN-001 |
| TC-009 | Non-SDN name passes screening | high | OFAC-SDN-001 |
| TC-010 | 50% ownership rule applied | high | OFAC-SDN-001 |
| TC-011 | SDN list staleness alert (>24h) | medium | OFAC-SDN-001 |
