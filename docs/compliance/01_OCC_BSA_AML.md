# Source 1: OCC BSA/AML — Wire Transfer & CDD Compliance

## What We Are Targeting

Extract **BSA/AML examination rules** from the Office of the Comptroller of the Currency covering:
- Wire transfer dual approval ($10K threshold)
- Currency Transaction Report (CTR) filing ($10K cash)
- Structuring detection (aggregated transactions)
- Customer Due Diligence (CDD) / Beneficial Ownership (25% threshold)
- Record retention (5-year requirement)
- Ongoing monitoring & risk rating

**Test cases:** 8 (TC-001 → TC-006, TC-012, TC-017) — 23% of all test cases

---

## Source of Information

| Source | URL | Format |
|--------|-----|--------|
| BSA/AML Examination Manual | https://www.occ.gov/publications-and-resources/publications/banker-education/files/pub-bsa-aml-examination-manual.pdf | PDF (418 pages) |
| OCC BSA Topic Page | https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html | HTML |
| OCC Bulletins | https://www.occ.gov/news-issuances/bulletins/index-bulletins.html | HTML + PDF |

---

## Current Implementation Status: ✅ DONE

**Connector file:** `src/connectors/regulatory/usa/occ_bsa.py`

### What's implemented
- 3 regulations (Wire-001, CTR-001, CDD-001)
- 8 requirements with exact thresholds ($10K wire, $10K CTR, 25% BO, etc.)
- 5 KPIs with formulas and alert thresholds
- 8 test scenarios in BDD format (given / when / then)
- Connection test to OCC website

### Data extraction method
**Structured rules hardcoded** — The key BSA/AML thresholds and rules are well-known regulatory constants (e.g., $10,000 CTR threshold hasn't changed since 1970). These are coded directly from the examination manual.

---

## Do We Need AI / PDF Extraction?

**Optional, not required for current scope.**

The 418-page BSA/AML Examination Manual contains ~45 additional requirements beyond our 8 core requirements. If we want to extract those:

| What | Tool | Effort |
|------|------|--------|
| Download PDF | `requests` | Trivial |
| Extract text from PDF | `pdfplumber` or `PyPDF2` | ~2 hours |
| Parse sections (TOC-based) | Python regex | ~2 hours |
| Extract structured rules from text | Claude Sonnet 4 via Bedrock | ~$0.50 in tokens |
| Store in DB | Existing migration | Already done |

**Recommendation:** Phase 2 enhancement. Current 8 test cases cover the exam-critical rules.

---

## Validation Checklist

```
✅ 3 regulations extracted (Wire, CTR, CDD)
✅ 8 requirements with thresholds
✅ 5 KPIs with formulas
✅ 8 test scenarios with BDD format
✅ All scenario_ids unique (TC-001 to TC-006, TC-012, TC-017)
✅ All requirement_ids unique
✅ Threshold amounts are numeric (10000.00, 25.0)
✅ test_connection() returns True (OCC website reachable)
```

---

## Test Scenarios Covered

| ID | Name | Priority | Regulation |
|----|------|----------|------------|
| TC-001 | Wire $10,001 requires dual approval | critical | OCC-BSA-WIRE-001 |
| TC-002 | Same person cannot approve wire twice | critical | OCC-BSA-WIRE-001 |
| TC-003 | Wire under $10,000 single approval OK | high | OCC-BSA-WIRE-001 |
| TC-004 | CTR auto-generated for $10K+ cash | critical | OCC-BSA-CTR-001 |
| TC-005 | Structuring detection — aggregated deposits | critical | OCC-BSA-CTR-001 |
| TC-006 | Beneficial ownership required for LLC | high | OCC-BSA-CDD-001 |
| TC-012 | Wire audit trail retained 5 years | high | OCC-BSA-WIRE-001 |
| TC-017 | Risk rating updated on negative news | medium | OCC-BSA-CDD-001 |
