# Source 17: GLBA Privacy — Gramm-Leach-Bliley Act

## What We Are Targeting

Extract **financial privacy requirements** from GLBA covering:
- Annual privacy notice requirements
- Opt-out rights for information sharing
- Safeguards Rule (information security program)

**Test cases:** 2
**Priority:** P2

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| FTC GLBA Safeguards Rule | https://www.ftc.gov/legal-library/browse/rules/financial-privacy-rule | HTML | No |
| FRB Privacy Resources | https://www.federalreserve.gov/supervisionreg/regulations.htm | HTML | No |
| Model Privacy Notices | https://www.federalreserve.gov/bankinforeg/model-privacy-form.htm | HTML + PDF | **Maybe** — template parsing |

---

## Current Implementation Status: ❌ TODO

**Connector file:** Not yet created → `src/connectors/regulatory/usa/glba_privacy.py`

---

## Implementation Plan

**Primarily hardcoded, optional PDF for privacy notice templates.**

| Component | Method | AI? |
|-----------|--------|-----|
| Annual notice requirement | Hardcode | No |
| Opt-out rights | Hardcode | No |
| Safeguards Rule requirements | HTML scrape from FTC | No |
| Model privacy notice template | PDF extraction | Maybe — short doc |

---

## Expected Output

```
Regulations:     1
Requirements:    ~4
KPIs:            ~2
Test Scenarios:  2
```
