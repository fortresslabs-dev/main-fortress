# Source 8: FFIEC BSA/AML Manual

## What We Are Targeting

Extract **interagency BSA/AML examination procedures** from the Federal Financial Institutions Examination Council. This supplements the OCC manual (Source 1) with interagency guidance.

**Test cases:** 2
**Priority:** P1

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| FFIEC BSA/AML InfoBase | https://bsaaml.ffiec.gov/manual | HTML (structured) | No |
| Examination Manual sections | https://bsaaml.ffiec.gov/manual/Introduction/01 | HTML | No |

---

## Current Implementation Status: ❌ TODO

**Connector file:** Not yet created → `src/connectors/regulatory/usa/ffiec_bsa.py`

---

## Implementation Plan

**HTML scraping — no AI needed.** The FFIEC InfoBase is a well-structured HTML site with section navigation. Key examination procedures can be scraped directly.

---

## Expected Output

```
Regulations:     1
Requirements:    ~5
KPIs:            ~2
Test Scenarios:  2
```
