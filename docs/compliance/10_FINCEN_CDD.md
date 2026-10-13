# Source 10: FinCEN Customer Due Diligence (CDD) Rule

## What We Are Targeting

Extract **CDD / KYC rules** from FinCEN covering:
- Customer identification program (CIP) requirements
- Beneficial ownership identification (25% threshold)
- Risk-based customer monitoring
- Enhanced Due Diligence (EDD) for high-risk customers

**Test cases:** 2
**Priority:** P1

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| FinCEN CDD Final Rule | https://www.fincen.gov/resources/statutes-and-regulations/cdd-final-rule | HTML + PDF | No — thresholds are constants |
| FinCEN CDD FAQs | https://www.fincen.gov/resources/statutes-and-regulations/guidance/frequently-asked-questions-regarding-customer-due | HTML | No |

---

## Current Implementation Status: ❌ TODO

**Note:** Some CDD rules are already covered in Source 1 (OCC BSA/AML, requirement OCC-BSA-CDD-001). This connector adds FinCEN-specific CDD requirements that supplement the OCC rules.

**Connector file:** Not yet created → `src/connectors/regulatory/usa/fincen_cdd.py`

---

## Implementation Plan

**Hardcoded rules — no AI needed.** CDD thresholds (25% beneficial ownership, CIP requirements) are well-established FinCEN constants.

---

## Expected Output

```
Regulations:     1
Requirements:    ~4 (CIP, BO, EDD, ongoing monitoring)
KPIs:            ~2
Test Scenarios:  2
```
