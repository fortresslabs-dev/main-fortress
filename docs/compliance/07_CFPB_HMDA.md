# Source 7: CFPB HMDA — Home Mortgage Disclosure Act Reporting

## What We Are Targeting

Extract **HMDA reporting requirements** from the CFPB covering:
- Annual Loan Application Register (LAR) submission
- 25+ required data points per covered mortgage
- March 1 annual filing deadline
- Threshold: 25+ closed-end mortgage loans or 200+ open-end lines of credit

**Test cases:** 3
**Priority:** P1 — Important

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| HMDA Data Portal | https://www.consumerfinance.gov/data-research/hmda/ | HTML | No |
| HMDA Filing Specs | https://ffiec.cfpb.gov/documentation/publications/loan-level-datasets/lar-data-fields | HTML | No |
| HMDA Filing Guide | https://s3.amazonaws.com/cfpb-hmda-public/prod/help/hmda-fig.pdf | PDF | **Maybe** — field definitions |
| CFPB Reg C | https://www.consumerfinance.gov/rules-policy/regulations/1003/ | HTML | No |

---

## Current Implementation Status: ❌ TODO

**Connector file:** Not yet created → `src/connectors/regulatory/usa/cfpb_hmda.py`

---

## Implementation Plan

### Extraction Method: HTML scraping + optional PDF

The HMDA LAR data fields are published on the FFIEC website as structured HTML. The filing guide (PDF) has more detail.

```
Primary: HTML scrape of LAR data fields page (no AI)
Optional: PDF extraction of filing guide for field validation rules (AI)
Cost: $0 for HTML, ~$0.30 for PDF if needed
```

### Steps
1. Scrape LAR data fields from FFIEC website
2. Extract all 25+ required fields with types and options
3. Code filing deadline rule (March 1)
4. Code reporting thresholds (25 closed-end / 200 open-end)
5. Build 3 test scenarios

---

## AI / PDF Extraction

**Optional.** The HTML data fields page covers the core fields. The PDF filing guide has additional validation rules and edge cases.

| What | HTML | PDF + AI |
|------|------|----------|
| Field names | ✅ | ✅ |
| Field types | ✅ | ✅ |
| Validation rules | Partial | Full |
| Code values / enums | ✅ | ✅ |
| Edge case handling | ❌ | ✅ |

**Recommendation:** Start with HTML scrape. Add PDF extraction in Phase 2 if needed.

---

## Expected Output

```
Regulations:     1 (CFPB-HMDA-001)
Requirements:    ~8 (reporting threshold + deadline + key fields)
Data Points:     25+ (LAR required fields)
KPIs:            ~2 (filing timeliness, data completeness)
Test Scenarios:  3
```

---

## Validation Checklist (for when implemented)

```
❌ 1 regulation (HMDA reporting)
❌ Filing deadline: March 1 annually
❌ Thresholds: 25 closed-end / 200 open-end
❌ 25+ data fields extracted
❌ 3 test scenarios in BDD format
```
