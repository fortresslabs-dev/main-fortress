# Source 2: FinCEN SAR — Suspicious Activity Report Filing

## What We Are Targeting

Extract **SAR filing rules** from the Financial Crimes Enforcement Network covering:
- Filing triggers: insider abuse ($0), identified suspect ($5K), unidentified ($25K)
- 30-day filing deadline from detection
- Required SAR form fields (name, TIN/SSN, date, amount, narrative)
- 90-day continuing activity SAR review
- Escalation and early warning alerts

**Test cases:** 6 (TC-013 → TC-016, TC-018, TC-024) — 17% of all test cases

---

## Source of Information

| Source | URL | Format |
|--------|-----|--------|
| FinCEN Filing Information | https://www.fincen.gov/resources/filing-information | HTML |
| SAR Electronic Filing Requirements | https://www.fincen.gov/sites/default/files/shared/FinCEN_SAR_ElectronicFilingRequirements.pdf | PDF |
| SAR Instructions | https://www.fincen.gov/sites/default/files/shared/SAR_instructions.pdf | PDF |
| FinCEN Guidance | https://www.fincen.gov/resources/statutes-and-regulations/fincen-guidance | HTML |
| BSA E-Filing System | https://bsaefiling.fincen.treas.gov/main.html | Web App (no API) |

---

## Current Implementation Status: ✅ DONE

**Connector file:** `src/connectors/regulatory/usa/fincen_sar.py`

### What's implemented
- 2 regulations (SAR Thresholds, Continuing Activity)
- 6 requirements (3 triggers + deadline + continuing activity + required fields)
- 3 KPIs (filing timeliness, quality, continuing activity)
- 6 test scenarios in BDD format

### Data extraction method
**Structured rules hardcoded** — SAR thresholds ($0, $5K, $25K) and the 30-day deadline are fixed FinCEN rules that don't change. Key required fields extracted from the SAR instructions PDF by hand.

---

## Do We Need AI / PDF Extraction?

**Optional — to extract the full 50+ SAR form fields.**

| What | Current | With AI |
|------|---------|---------|
| Filing triggers | ✅ 3 triggers hardcoded | Same — these don't change |
| Required fields | 5 key fields | Could extract all 50+ fields from SAR instructions PDF |
| Deadlines | ✅ 30-day + 90-day coded | Same |
| Narrative guidance | Not extracted | Could extract FinCEN's narrative writing guidelines |

**If we want to expand:**

| Step | Tool | Cost |
|------|------|------|
| Download SAR instructions PDF | `requests` | Free |
| Extract text | `pdfplumber` | Free |
| Parse form field definitions | Claude Sonnet 4 | ~$0.30 |
| Store all 50+ fields in DB | Migration | Already done |

**Recommendation:** Current 6 requirements cover the exam-critical items. Expanding to all 50+ fields is Phase 2.

---

## Validation Checklist

```
✅ 2 regulations extracted (SAR-001, SAR-002)
✅ 6 requirements with thresholds/parameters
✅ 3 KPIs with formulas
✅ 6 test scenarios with BDD format
✅ All scenario_ids unique (TC-013 to TC-016, TC-018, TC-024)
✅ Filing triggers: $0 (insider), $5K (identified), $25K (unidentified)
✅ Deadline: 30 days from detection
✅ Continuing activity: 90-day review period
✅ test_connection() reaches FinCEN website
```

---

## Test Scenarios Covered

| ID | Name | Priority | Regulation |
|----|------|----------|------------|
| TC-013 | SAR triggered for $5,500 suspicious wire | high | FINCEN-SAR-001 |
| TC-014 | SAR 30-day deadline enforcement | high | FINCEN-SAR-001 |
| TC-015 | SAR rejected for missing required fields | high | FINCEN-SAR-001 |
| TC-016 | Insider abuse triggers SAR ($0 threshold) | critical | FINCEN-SAR-001 |
| TC-018 | $25K unidentified suspect SAR trigger | high | FINCEN-SAR-001 |
| TC-024 | 90-day continuing activity review | medium | FINCEN-SAR-002 |
