# Source 9: OCC Third-Party Risk Management

## What We Are Targeting

Extract **third-party vendor risk management rules** from OCC covering:
- Vendor due diligence before engagement
- Ongoing monitoring of critical vendors
- Business continuity requirements for third-party services
- Concentration risk assessment

**Test cases:** 2
**Priority:** P1

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| OCC Bulletin 2013-29 (updated 2023) | https://www.occ.gov/news-issuances/bulletins/2023/bulletin-2023-17.html | HTML | No |
| Third-Party Relationships: Risk Mgmt Guidance | https://www.occ.gov/topics/supervision-and-examination/bank-management/third-party-relationships/index-third-party-relationships.html | HTML | No |

---

## Current Implementation Status: ❌ TODO

**Connector file:** Not yet created → `src/connectors/regulatory/usa/occ_third_party.py`

---

## Implementation Plan

**HTML scraping — no AI needed.** OCC Bulletin 2023-17 is published as structured HTML with clear requirements sections.

---

## Expected Output

```
Regulations:     1
Requirements:    ~5 (due diligence, monitoring, BCP, concentration)
KPIs:            ~2
Test Scenarios:  2
```
