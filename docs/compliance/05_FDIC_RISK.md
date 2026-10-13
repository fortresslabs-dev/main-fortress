# Source 5: FDIC Risk Management Manual

## What We Are Targeting

Extract **risk management examination rules** from the FDIC covering:
- Third-party risk management (vendor due diligence)
- IT security controls for banking systems
- Loan portfolio management and underwriting standards
- BSA/AML overlap requirements

**Test cases:** 3
**Priority:** P1 — Important

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| FDIC Risk Management Manual | https://www.fdic.gov/regulations/safety/manual/ | HTML (sectioned) | No — structured HTML |
| Section 8: BSA | Above + /section8-1.html | HTML | No |
| Section 5: Loan Portfolio | Above + /section5-1.html | HTML | No |
| Section 11: IT | Above + /section11-1.html | HTML | No |

---

## Current Implementation Status: ❌ TODO

**Connector file:** Not yet created → `src/connectors/regulatory/usa/fdic_risk.py`

---

## Implementation Plan

### Extraction Method: HTML scraping (no AI needed)

The FDIC Risk Management Manual is published as structured HTML pages with clear sections. Each section has numbered subsections with requirements.

```
Tool: requests + BeautifulSoup
Input: FDIC manual HTML pages (sections 5, 8, 11)
Output: Structured requirements with section references
Cost: $0
AI: Not needed — HTML is well-structured
```

### Steps
1. Scrape Section 8 (BSA) — vendor monitoring requirements
2. Scrape Section 5 (Loans) — underwriting standards
3. Scrape Section 11 (IT) — security control requirements
4. Extract requirements into structured format
5. Build 3 test scenarios

---

## AI / PDF Extraction: NOT NEEDED

The FDIC manual is published as well-structured HTML. Standard BeautifulSoup parsing is sufficient.

---

## Expected Output

```
Regulations:     3 (Third-Party, Loan, IT Security)
Requirements:    ~10
KPIs:            ~3
Test Scenarios:  3
```

---

## Validation Checklist (for when implemented)

```
❌ 3 regulations extracted
❌ ~10 requirements with section references
❌ 3 KPIs
❌ 3 test scenarios in BDD format
❌ Source URLs point to valid FDIC pages
```
