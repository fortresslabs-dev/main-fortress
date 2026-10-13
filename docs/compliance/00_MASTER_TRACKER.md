# USA Banking Compliance — Master Implementation Tracker

**Total Sources:** 18 | **Total Test Cases:** 35 | **Geography:** USA

---

## Status Summary

| # | Source | Regulator | Status | Test Cases | Connector File | Task Doc |
|---|--------|-----------|--------|------------|----------------|----------|
| 1 | OCC BSA/AML | OCC | ✅ DONE | 8 (TC-001→TC-006, TC-012, TC-017) | `usa/occ_bsa.py` | [01_OCC_BSA_AML.md](01_OCC_BSA_AML.md) |
| 2 | FinCEN SAR | FinCEN | ✅ DONE | 6 (TC-013→TC-016, TC-018, TC-024) | `usa/fincen_sar.py` | [02_FINCEN_SAR.md](02_FINCEN_SAR.md) |
| 3 | OFAC SDN | OFAC | ✅ DONE | 5 (TC-007→TC-011) | `usa/ofac_sdn.py` | [03_OFAC_SDN.md](03_OFAC_SDN.md) |
| 4 | CFPB Section 1071 | CFPB | ❌ TODO | 4 (TC-019→TC-022) | — | [04_CFPB_1071.md](04_CFPB_1071.md) |
| 5 | FDIC Risk Mgmt | FDIC | ❌ TODO | 3 | — | [05_FDIC_RISK.md](05_FDIC_RISK.md) |
| 6 | FRB Reg E | FRB | ❌ TODO | 2 | — | [06_FRB_REG_E.md](06_FRB_REG_E.md) |
| 7 | CFPB HMDA | CFPB | ❌ TODO | 3 | — | [07_CFPB_HMDA.md](07_CFPB_HMDA.md) |
| 8 | FFIEC BSA | FFIEC | ❌ TODO | 2 | — | [08_FFIEC_BSA.md](08_FFIEC_BSA.md) |
| 9 | OCC Third-Party | OCC | ❌ TODO | 2 | — | [09_OCC_THIRD_PARTY.md](09_OCC_THIRD_PARTY.md) |
| 10 | FinCEN CDD | FinCEN | ❌ TODO | 2 | — | [10_FINCEN_CDD.md](10_FINCEN_CDD.md) |
| 11 | CFPB Reg B | CFPB | ❌ TODO | 1 | — | [11_CFPB_REG_B.md](11_CFPB_REG_B.md) |
| 12 | SEC | SEC | ❌ TODO | 0 | — | — |
| 13 | FRB SR Letters | FRB | ❌ TODO | 0 | — | — |
| 14 | DFAST Stress Test | FRB | ❌ TODO | 0 | — | — |
| 15 | OCC Circulars | OCC | ❌ TODO | 0 | — | — |
| 16 | FinCEN CTR | FinCEN | ❌ TODO | 0 | — | — |
| 17 | GLBA Privacy | FTC/FRB | ❌ TODO | 2 | — | [17_GLBA_PRIVACY.md](17_GLBA_PRIVACY.md) |
| 18 | State Regulations | State | ❌ TODO | 0 | — | — |

---

## Phase Plan

```
Phase 1 (Week 1-2) — CRITICAL:       19 test cases, 54%
  ✅ Source 1: OCC BSA/AML            8 TC   — DONE (structured rules, no PDF AI yet)
  ✅ Source 2: FinCEN SAR             6 TC   — DONE (structured rules, no PDF AI yet)
  ✅ Source 3: OFAC SDN               5 TC   — DONE (live CSV download + rules)

Phase 2 (Week 2-3) — URGENT:          7 test cases, 20%
  ❌ Source 4: CFPB Section 1071      4 TC   — TODO (deadline Jul 1 2026!)
  ❌ Source 7: CFPB HMDA              3 TC   — TODO

Phase 3 (Week 3-4) — IMPORTANT:       4 test cases, 11%
  ❌ Source 5: FDIC Risk Mgmt         3 TC   — TODO
  ❌ Source 6: FRB Reg E              2 TC   — TODO (error resolution timelines)

Phase 4 (Week 4-5) — REMAINING:       5 test cases, 14%
  ❌ Source 8-11, 17                   varies — TODO
```

---

## Validation Strategy

Each connector is validated by running `extract_all_rules()` and checking:

1. **Regulation count** — matches expected count from source doc
2. **Requirement count** — all rules extracted with thresholds
3. **KPI count** — formulas, targets, alert thresholds present
4. **Test scenario count** — BDD given/when/then complete
5. **Data types** — amounts are numeric, dates are ISO format, IDs are unique

```bash
# Run validation for a single connector
python scripts/validate_connector.py --connector occ_bsa

# Run all connectors
python scripts/validate_connector.py --all
```

---

## What Still Needs AI / PDF Extraction?

| Source | Current State | Needs AI? | Why |
|--------|--------------|-----------|-----|
| OCC BSA/AML | Structured rules hardcoded | **Optional** — PDF has 418 pages; could extract 30+ more rules | Full manual is at `pub-bsa-aml-examination-manual.pdf` |
| FinCEN SAR | Structured rules hardcoded | **Optional** — PDF has SAR form instructions | Could extract all 50+ SAR fields |
| OFAC SDN | Live CSV download | **No** | Machine-readable CSV already |
| CFPB 1071 | Not started | **Yes** — Federal Register PDF has 88 data points | HTML guide exists too |
| FDIC Manual | Not started | **Yes** — full manual is HTML sections | Can scrape HTML directly |
| FRB Reg E | Not started | **No** — regulation text is on HTML page | Structured scraping |
| CFPB HMDA | Not started | **No** — HMDA filing guide is downloadable | CSV/Excel specs |
| GLBA | Not started | **Maybe** — privacy notice templates are PDF | Short PDF |
