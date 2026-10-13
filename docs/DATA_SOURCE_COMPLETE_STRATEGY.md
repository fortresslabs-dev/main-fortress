# COMPLETE DATA SOURCE STRATEGY - ALL 35 TEST CASES
## Fortress Labs Compliance Matrix Implementation Guide

**Based on:** fortress_labs_compliance_matrix.xlsx  
**Last Updated:** March 9, 2026

---

## 📊 **DATA SOURCE SUMMARY**

| Source Type | Count | % | Implementation Time |
|-------------|-------|---|---------------------|
| **Govt Web Scraping** | 18 | 51% | 1-2 weeks |
| **Bank Database** | 12 | 34% | 2-3 weeks |
| **Live API** | 5 | 14% | 1 week |

**Total:** 35 test cases across 8 regulatory domains

---

## 🔍 **DETAILED BREAKDOWN BY TEST CASE**

### **AUDIT AGENT (6 Test Cases)**

#### **TC-001: Community Bank BSA Minimum Exam**
```yaml
Data Sources:
  - Govt Web: ✅ OCC BSA Procedures (PDF scraping)
  - Bank Data: ✅ BSA policy document (upload)
  
Acquisition Method:
  - Scrape: https://occ.gov/topics/supervision-and-examination/bsa
  - Parse: PDF (100-200 pages), extract exam procedures
  - Store: Weaviate vector DB
  
Update Frequency: Annual + bulletins
Implementation Time: 1 day
Priority: P1 (Feb 2026 deadline)
```

#### **TC-002: FDIC 3-Tier Exam Cycle Classification**
```yaml
Data Sources:
  - Govt Web: ✅ FDIC Compliance Manual (HTML scraping)
  - Bank Data: ✅ Asset size, compliance rating, CRA rating
  
Acquisition Method:
  - Scrape: https://www.fdic.gov/regulations/compliance/manual/
  - Parse: HTML chapters, extract tier criteria
  - Bank: Call Report data (asset size, ratings)
  
Update Frequency: Annual
Implementation Time: 1 day
Priority: P2
```

#### **TC-003: CRA Performance Evaluation Audit**
```yaml
Data Sources:
  - Govt Web: ✅ FFIEC CRA benchmarks (API)
  - Bank Data: ✅ Community development lending data
  
Acquisition Method:
  - API: https://www.ffiec.gov/cra/ (county-level benchmarks)
  - Bank DB: Lending by census tract, loan types
  
Update Frequency: Annual (Nov release)
Implementation Time: 2 days
Priority: P2
```

#### **TC-004: FDIC Digital Sign Compliance Audit**
```yaml
Data Sources:
  - Govt Web: ✅ FDIC Part 328 Signage Rule (PDF)
  - Bank Data: ✅ Screenshots (web, mobile app, ATM)
  
Acquisition Method:
  - Scrape: https://www.fdic.gov/regulations/laws/rules/2000-4500.html
  - Bank: UI/UX audit (manual capture or automated testing)
  
Update Frequency: Static (rule finalized)
Implementation Time: 1 day
Priority: P3
```

#### **TC-005: OFAC SDN Screening Audit Trail**
```yaml
Data Sources:
  - Govt API: ✅ OFAC SDN List (real-time)
  - Bank Data: ✅ Screening logs (transaction + customer onboarding)
  
Acquisition Method:
  - API: https://ofac.treasury.gov/ (real-time SDN updates)
  - Bank DB: Query screening_logs table
  
Update Frequency: Real-time (OFAC), daily audit
Implementation Time: 2 days
Priority: P1
```

#### **TC-006: IT Controls Audit — Third-Party Vendors**
```yaml
Data Sources:
  - Govt Web: ✅ FFIEC IT Handbook (PDF scraping)
  - Bank Data: ✅ Vendor master, contracts, SOC 2 reports
  
Acquisition Method:
  - Scrape: https://ithandbook.ffiec.gov/ (IT controls framework)
  - Bank DB: Vendor_master, contracts, risk_assessments
  
Update Frequency: Handbook updated periodically
Implementation Time: 2 days
Priority: P2
```

---

### **RISK AGENT (6 Test Cases)**

#### **TC-007: Capital & Liquidity Risk Scoring**
```yaml
Data Sources:
  - Govt Web: ✅ FRB SR Letters (HTML/PDF scraping)
  - Bank Data: ✅ Call Report (capital ratios, liquidity)
  
Acquisition Method:
  - Scrape: https://www.federalreserve.gov/supervisionreg/srletters/
  - Bank: FFIEC 031/041 Call Report data
  
Update Frequency: Monthly (SR letters), Quarterly (Call Reports)
Implementation Time: 2 days
Priority: P1
```

#### **TC-008: Executive Compensation Risk Review**
```yaml
Data Sources:
  - Govt Web: ✅ Dodd-Frank Section 956 Rule (PDF)
  - Bank Data: ✅ Compensation plans, deferral schedules
  
Acquisition Method:
  - Scrape: https://www.fdic.gov/regulations/laws/rules/
  - Bank: HR database (comp plans, clawback provisions)
  
Update Frequency: Static rule (monitor for amendments)
Implementation Time: 1 day
Priority: P3
```

#### **TC-009: Third-Party Relationship Risk Assessment**
```yaml
Data Sources:
  - Govt Web: ✅ FFIEC TPRM Guidance May 2024 (PDF)
  - Bank Data: ✅ Vendor contracts, due diligence docs
  
Acquisition Method:
  - Scrape: https://www.ffiec.gov/ (TPRM guidance)
  - Bank DB: Same as TC-006 (vendor data)
  
Update Frequency: Static guidance
Implementation Time: 1 day (reuses TC-006 scraper)
Priority: P2
```

#### **TC-010: Vendor Breach SAR Risk Trigger**
```yaml
Data Sources:
  - Govt Web: ✅ FinCEN Ransomware Advisory (PDF/HTML)
  - Bank Data: ✅ Vendor breach notifications
  
Acquisition Method:
  - Scrape: https://www.fincen.gov/financial-institution-advisory
  - Bank: Incident management system (breach alerts)
  
Update Frequency: Advisories updated quarterly
Implementation Time: 1 day
Priority: P1
```

#### **TC-011: Stablecoin Issuance Risk Assessment**
```yaml
Data Sources:
  - Govt Web: ✅ FDIC GENIUS Act Proposed Rule (PDF)
  - Bank Data: ❌ Not applicable (new regulation)
  
Acquisition Method:
  - Scrape: https://www.fdic.gov/ (monitor rulemaking)
  - Bank: N/A (future use case)
  
Update Frequency: Monitor for final rule (2026)
Implementation Time: 1 day
Priority: P3
```

#### **TC-012: Concentration Risk — Consumer Lending**
```yaml
Data Sources:
  - Govt Web: ✅ FRB Compliance Outlook Q4 2024 (PDF/HTML)
  - Bank Data: ✅ Loan portfolio (by product, geography, segment)
  
Acquisition Method:
  - Scrape: https://www.consumercomplianceoutlook.org/
  - Bank DB: Loan_portfolio table (aggregations)
  
Update Frequency: Quarterly (Compliance Outlook)
Implementation Time: 2 days
Priority: P2
```

---

### **AML AGENT (6 Test Cases)**

#### **TC-013: SAR Quality Threshold Test**
```yaml
Data Sources:
  - Govt Web: ✅ FinCEN SAR FAQs Oct 2025 (PDF/HTML)
  - Bank Data: ✅ Suspicious transactions
  
Acquisition Method:
  - Scrape: https://www.fincen.gov/resources/advisories
  - Bank DB: Transaction_master (flagged suspicious)
  
Update Frequency: Quarterly (FAQs)
Implementation Time: 1 day
Priority: P1
```

#### **TC-014: AML Program — 4 Pillars Assessment**
```yaml
Data Sources:
  - Govt Web: ✅ FFIEC BSA/AML Manual (HTML scraping)
  - Bank Data: ✅ AML policy, training records, audit reports
  
Acquisition Method:
  - Scrape: https://bsaaml.ffiec.gov/manual
  - Bank: Policy PDFs, training_completion table, audit_findings
  
Update Frequency: Manual updated periodically
Implementation Time: 1 day
Priority: P1
```

#### **TC-015: BOI Reporting — Foreign Entity Screening**
```yaml
Data Sources:
  - Govt Web: ✅ FinCEN CTA Interim Rule (PDF)
  - Bank Data: ✅ Customer entities (registration country)
  
Acquisition Method:
  - Scrape: https://www.fincen.gov/boi
  - Bank DB: Entity_master (entity_type, registration_country)
  
Update Frequency: Rule static (March 2025)
Implementation Time: 1 day
Priority: P1
```

#### **TC-016: Third-Party TIN Collection Compliance**
```yaml
Data Sources:
  - Govt Web: ✅ FinCEN CIP Order July 2025 (Advisory)
  - Bank Data: ✅ Customer TIN records
  
Acquisition Method:
  - Scrape: https://www.fincen.gov/financial-institution-advisory
  - Bank DB: Customer_identity (tin, tin_source, verification_method)
  
Update Frequency: Static order
Implementation Time: 1 day
Priority: P2
```

#### **TC-017: Real-Time SDN Transaction Screening**
```yaml
Data Sources:
  - Govt API: ✅ OFAC SDN List Service (JSON API)
  - Bank Data: ✅ Wire transfers >$10K
  
Acquisition Method:
  - API: https://ofac.treasury.gov/sanctions-list-service
  - Bank DB: Wire_transfer table (real-time feed)
  
Update Frequency: Real-time (OFAC updates multiple times daily)
Implementation Time: 1 day
Priority: P1 (CRITICAL)
```

#### **TC-018: Elder Exploitation SAR Detection**
```yaml
Data Sources:
  - Govt Web: ✅ FinCEN Elder Exploitation Guidance 2024 (PDF)
  - Bank Data: ✅ At-risk customer segments, transaction patterns
  
Acquisition Method:
  - Scrape: https://www.fincen.gov/resources/advisories
  - Bank DB: Customer_demographics (age >65), transactions
  
Update Frequency: Static guidance
Implementation Time: 2 days (pattern detection engine)
Priority: P2
```

---

### **FAIR LENDING AGENT (5 Test Cases)**

#### **TC-019: Small Business Lending Data Validation**
```yaml
Data Sources:
  - Govt Web: ✅ CFPB Section 1071 Final Rule (PDF)
  - Bank Data: ✅ Small business loan applications
  
Acquisition Method:
  - Scrape: https://www.consumerfinance.gov/rules-policy/final-rules/small-business-lending...
  - Bank DB: Small_business_loans (20+ required fields)
  
Update Frequency: Static rule (Jul 2026 deadline)
Implementation Time: 1 day
Priority: P1 (DEADLINE)
```

#### **TC-020: HMDA Loan Application Register Validation**
```yaml
Data Sources:
  - Govt API: ✅ FFIEC HMDA Validator (REST API)
  - Bank Data: ✅ HMDA LAR (110 fields per loan)
  
Acquisition Method:
  - API: https://ffiec.cfpb.gov/filing/ (LAR validation)
  - Bank DB: HMDA_LAR table (110 fields per FFIEC spec)
  
Update Frequency: Annual (LAR filing season Jan-Mar)
Implementation Time: 2 days
Priority: P1 (ANNUAL FILING)
```

#### **TC-021: Disparate Impact Screening**
```yaml
Data Sources:
  - Govt Web: ✅ CFPB Fair Lending Report (PDF)
  - Govt Data: ✅ Census Bureau demographics
  - Bank Data: ✅ Loan applications (race, ethnicity, decisions)
  
Acquisition Method:
  - Scrape: https://www.consumerfinance.gov/data-research/research-reports/
  - API/Download: Census.gov (demographics by census tract)
  - Bank DB: Loan_application (demographic fields, decisions)
  
Update Frequency: Annual (CFPB report), Decennial (Census)
Implementation Time: 3 days (complex analysis)
Priority: P2
```

#### **TC-022: Reconsideration of Value Process Audit**
```yaml
Data Sources:
  - Govt Web: ✅ FFIEC ROV Guidance 2024 (PDF)
  - Bank Data: ✅ Appraisal data, ROV requests
  
Acquisition Method:
  - Scrape: https://www.ffiec.gov/
  - Bank DB: Appraisals, rov_requests, resolution_outcomes
  
Update Frequency: Static guidance
Implementation Time: 1 day
Priority: P3
```

#### **TC-023: Mortgage Servicing Junk Fee Detection**
```yaml
Data Sources:
  - Govt Web: ✅ CFPB Supervisory Highlights (PDF/HTML)
  - Bank Data: ✅ Servicing fee schedules
  
Acquisition Method:
  - Scrape: https://www.consumerfinance.gov/compliance/supervisory-highlights/
  - Bank DB: Fee_schedule, servicing_charges
  
Update Frequency: Quarterly (Supervisory Highlights)
Implementation Time: 1 day
Priority: P2
```

---

### **STRESS TEST AGENT (5 Test Cases)**

#### **TC-024: Baseline / Adverse / Severely Adverse Scenario Run**
```yaml
Data Sources:
  - Govt Web: ✅ OCC 2025 Stress Scenarios (Excel download)
  - Bank Data: ✅ Call Report, loan portfolio
  
Acquisition Method:
  - Download: https://www.occ.gov/topics/.../stress-testing/
  - Bank DB: FFIEC 031/041 (capital, loans, deposits)
  
Update Frequency: Annual (February release)
Implementation Time: 1 day (scraper) + 2-3 weeks (model engine)
Priority: P1
```

#### **TC-025: Fed Stress Test — G-SIB Compliance**
```yaml
Data Sources:
  - Govt Web: ✅ FRB DFAST 2025 Scenarios (Excel)
  - Bank Data: ✅ Call Report, trading portfolios
  
Acquisition Method:
  - Download: https://www.federalreserve.gov/supervisionreg/dfast-2025.htm
  - Bank DB: Same as TC-024 + trading_book
  
Update Frequency: Annual (February)
Implementation Time: Same as TC-024
Priority: P1
```

#### **TC-026: Higher-Priced Mortgage Loan Threshold Validation**
```yaml
Data Sources:
  - Govt Web: ✅ CFPB Reg Z HPML Threshold (HTML)
  - Bank Data: ✅ Mortgage pricing (rate, APOR)
  
Acquisition Method:
  - Scrape: https://www.consumerfinance.gov/rules-policy/regulations/1026/
  - Bank DB: Mortgage_pricing (rate, apor, hpml_flag)
  
Update Frequency: Annual (CPI-W adjustment Jan 1)
Implementation Time: 1 day
Priority: P2
```

#### **TC-027: Living Will Consistency Check**
```yaml
Data Sources:
  - Govt Web: ✅ FRB Resolution Plans Guidance (PDF)
  - Bank Data: ✅ Resolution plan document
  
Acquisition Method:
  - Scrape: https://www.federalreserve.gov/supervisionreg/resolution-plans.htm
  - Bank: Living will PDF (upload)
  
Update Frequency: Annual (G-SIBs only)
Implementation Time: 1 day
Priority: P3
```

#### **TC-028: Deposit Insurance Fund Adequacy Stress**
```yaml
Data Sources:
  - Govt Web: ✅ FDIC DIF Reserve Ratio (HTML)
  - Bank Data: ✅ Insured deposits
  
Acquisition Method:
  - Scrape: https://www.fdic.gov/ (DIF statistics)
  - Bank DB: Deposit_accounts (insured vs uninsured)
  
Update Frequency: Quarterly (FDIC reports)
Implementation Time: 1 day
Priority: P3
```

---

### **POLICY QA AGENT (7 Test Cases)**

#### **TC-029: BSA Policy Document Currency Check**
```yaml
Data Sources:
  - Govt Web: ✅ OCC Comptroller's Handbook BSA (PDF)
  - Bank Data: ✅ BSA policy document
  
Acquisition Method:
  - Scrape: https://occ.gov/publications-and-resources/publications/comptrollers-handbook/
  - Bank: Policy PDF upload
  - NLP: Compare policy vs current handbook
  
Update Frequency: Handbook updated periodically
Implementation Time: 1 day (scraper) + 1 week (NLP engine)
Priority: P2
```

#### **TC-030: UDAAP Policy Language Validation**
```yaml
Data Sources:
  - Govt Web: ✅ CFPB UDAAP Guidance (HTML/PDF)
  - Bank Data: ✅ Consumer protection policies
  
Acquisition Method:
  - Scrape: https://www.consumerfinance.gov/rules-policy/
  - Bank: Policy PDFs
  - NLP: Validate UDAAP 3-pillar coverage
  
Update Frequency: Monitor rulemaking agenda
Implementation Time: Same as TC-029
Priority: P2
```

#### **TC-031: AML Policy 4-Pillar Gap Analysis**
```yaml
Data Sources:
  - Govt Web: ✅ FFIEC BSA/AML Manual (HTML)
  - Bank Data: ✅ AML/BSA policy
  
Acquisition Method:
  - Scrape: https://bsaaml.ffiec.gov/manual
  - Bank: AML policy PDF
  - NLP: Check 4 pillars (policies, officer, training, testing)
  
Update Frequency: Manual updated periodically
Implementation Time: Same as TC-029
Priority: P1
```

#### **TC-032: Credit Union Policy Update Tracker**
```yaml
Data Sources:
  - Govt Web: ✅ NCUA Letters to Credit Unions (HTML)
  - Bank Data: ✅ Credit union policies
  
Acquisition Method:
  - Scrape: https://www.ncua.gov/regulation-supervision/letters-credit-unions...
  - Bank: Policy documents
  
Update Frequency: Monitor NCUA letters
Implementation Time: 1 day
Priority: P3
```

#### **TC-033: Open Banking Policy Readiness Review**
```yaml
Data Sources:
  - Govt Web: ✅ CFPB Section 1033 Rule (monitor rulemaking)
  - Bank Data: ✅ Data sharing policies
  
Acquisition Method:
  - Scrape: https://www.consumerfinance.gov/rules-policy/
  - Bank: Data governance policies
  
Update Frequency: Monitor for reproposal (Dec 2025 expected)
Implementation Time: 1 day
Priority: P3
```

#### **TC-034: FCPA Policy Monitoring Alert**
```yaml
Data Sources:
  - Govt Web: ✅ DOJ FCPA Guidance (PDF/HTML)
  - Bank Data: ✅ Anti-bribery policies
  
Acquisition Method:
  - Scrape: https://www.justice.gov/criminal-fraud/foreign-corrupt-practices-act
  - Bank: FCPA compliance policies
  
Update Frequency: Monitor for changes
Implementation Time: 1 day
Priority: P3
```

---

## 🎯 **IMPLEMENTATION PRIORITY MATRIX**

### **Phase 1: Critical Path (Week 1-2)**

| Test Case | Priority | Why | Time |
|-----------|----------|-----|------|
| TC-013 | P0 | SAR quality - FinCEN emphasis | 1 day |
| TC-017 | P0 | OFAC screening - criminal risk | 1 day |
| TC-001 | P1 | OCC BSA - Feb 2026 deadline | 1 day |
| TC-019 | P1 | Section 1071 - Jul 2026 deadline | 1 day |
| TC-020 | P1 | HMDA LAR - annual filing | 2 days |

**Total Phase 1:** 6 days (1-2 weeks with testing)

---

### **Phase 2: Bank Data Integration (Week 3-4)**

| Test Case | Priority | Needs | Time |
|-----------|----------|-------|------|
| TC-017 | P0 | FLEXCUBE wire transfer data | 2 days |
| TC-005 | P1 | OFAC screening logs | 1 day |
| TC-020 | P1 | HMDA LAR database | 2 days |
| TC-007 | P1 | Call Report data | 1 day |

**Total Phase 2:** 1 week

---

### **Phase 3: Advanced Features (Week 5-10)**

| Test Case | Priority | Complexity | Time |
|-----------|----------|------------|------|
| TC-024/TC-025 | P1 | DFAST stress model | 2-3 weeks |
| TC-021 | P2 | Disparate impact analysis | 1 week |
| TC-029-TC-031 | P2 | NLP policy scanner | 1 week |

**Total Phase 3:** 4-5 weeks

---

## ✅ **SUMMARY: DATA ACQUISITION STRATEGY**

### **Web Scraping (18 sources):**
```
Regulators: OCC, FDIC, FinCEN, CFPB, FRB, OFAC, FFIEC, NCUA
Format: PDF (exam manuals) + HTML (guidance)
Update: Weekly automated scraping
Storage: Weaviate vector database
Time: 1-2 weeks for all scrapers
```

### **Bank Database (12 sources):**
```
Systems: FLEXCUBE (USA), Finacle (India)
Tables: Transactions, loans, customers, vendors, policies
Access: Read-only UAT environment
Frequency: Real-time + batch queries
Time: 2-3 weeks for all connectors
```

### **Live APIs (5 sources):**
```
APIs: OFAC SDN, FFIEC HMDA Validator, FFIEC CRA Benchmarks
Format: JSON REST APIs
Rate Limits: 100 req/hour (FFIEC)
Frequency: Real-time (OFAC), on-demand (FFIEC)
Time: 1 week for all APIs
```

---

## 🚀 **NEXT STEPS**

1. **This Week:** Implement FinCEN + OFAC scrapers/APIs (TC-013, TC-017)
2. **Next Week:** Add OCC + CFPB (TC-001, TC-019)
3. **Week 3:** FLEXCUBE database connector
4. **Week 4:** HMDA LAR + Call Report data
5. **Month 2:** Advanced features (DFAST, disparate impact, NLP)

**You have complete coverage of all 35 test cases!** 🎯
