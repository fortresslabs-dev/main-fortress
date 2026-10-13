# Source 6: Federal Reserve Regulation E — Electronic Fund Transfers

## What We Are Targeting

Extract **Reg E consumer protection rules** from the Federal Reserve covering:
- Error resolution timelines (10-day investigation, 45-day max)
- Provisional credit within 10 business days
- Unauthorized transaction liability limits ($50 / $500 / unlimited based on reporting time)
- Required disclosures for electronic transfers

**Test cases:** 2
**Priority:** P1 — Important

---

## Source of Information

| Source | URL | Format | AI Needed? |
|--------|-----|--------|------------|
| Regulation E Full Text | https://www.federalreserve.gov/supervisionreg/regecg.htm | HTML | No |
| Reg E Official Commentary | https://www.ecfr.gov/current/title-12/chapter-X/part-1005 | HTML | No |
| CFPB Reg E Resources | https://www.consumerfinance.gov/rules-policy/regulations/1005/ | HTML | No |

---

## Current Implementation Status: ❌ TODO

**Connector file:** Not yet created → `src/connectors/regulatory/usa/frb_reg_e.py`

---

## Implementation Plan

### Extraction Method: HTML scraping (no AI needed)

Regulation E text is published as structured HTML with section numbers. The key thresholds are well-defined constants:

| Rule | Value | Source Section |
|------|-------|---------------|
| Error investigation period | 10 business days | §1005.11(c)(1) |
| Maximum resolution period | 45 calendar days | §1005.11(c)(2) |
| Provisional credit deadline | 10 business days | §1005.11(c)(2)(i) |
| Liability if reported in 2 days | $50 max | §1005.6(b)(1) |
| Liability if reported 2-60 days | $500 max | §1005.6(b)(2) |
| Liability if reported after 60 days | Unlimited | §1005.6(b)(3) |

```
Tool: Hardcoded from regulation text (thresholds are constants)
Cost: $0
AI: Not needed
```

---

## AI / PDF Extraction: NOT NEEDED

Reg E thresholds are fixed regulatory constants. HTML scraping or hardcoding is sufficient.

---

## Expected Output

```
Regulations:     1 (FRB-REG-E-001)
Requirements:    ~6 (timelines + liability limits)
KPIs:            ~2 (resolution timeliness, provisional credit compliance)
Test Scenarios:  2
```

---

## Validation Checklist (for when implemented)

```
❌ 1 regulation (Reg E error resolution)
❌ Error investigation: 10 business days
❌ Max resolution: 45 calendar days
❌ Provisional credit: 10 business days
❌ Liability tiers: $50, $500, unlimited
❌ 2 test scenarios in BDD format
```
