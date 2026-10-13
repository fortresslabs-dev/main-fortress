# USA BANKING COMPLIANCE - COMPLETE SOURCE DOCUMENT
## Regulations, Compliance Rules, and KPIs for Mid-Size Banks

**Target Market:** Mid-size banks ($1B-$10B assets), NBFCs, FinTech  
**Geography:** United States  
**Total Sources:** 18 regulatory sources  
**Total Test Cases:** 35 compliance scenarios  
**Last Updated:** March 15, 2026

---

## 📊 **EXECUTIVE SUMMARY**

### **Coverage Statistics:**

| Category | Sources | Test Cases | Priority | Implementation Time |
|----------|---------|------------|----------|-------------------|
| **BSA/AML** | 4 | 14 (40%) | P0 - Critical | Week 1-2 |
| **Fair Lending** | 3 | 7 (20%) | P0 - Critical | Week 2-3 |
| **Sanctions** | 2 | 5 (14%) | P0 - Critical | Week 1 |
| **Consumer Protection** | 3 | 4 (11%) | P1 - High | Week 3-4 |
| **Risk Management** | 3 | 3 (9%) | P1 - High | Week 4 |
| **Data Privacy** | 2 | 2 (6%) | P2 - Medium | Week 5 |
| **Fraud Controls** | 1 | 0 | P2 - Medium | Week 5 |

### **Implementation Phases:**

```
Phase 1 (Week 1-2): BSA/AML + Sanctions (19 test cases, 54%)
Phase 2 (Week 2-3): Fair Lending (7 test cases, 20%)
Phase 3 (Week 3-4): Consumer Protection (4 test cases, 11%)
Phase 4 (Week 4-5): Risk + Privacy (5 test cases, 14%)

Total: 35 test cases covering 100% of mid-size bank needs
```

---

## 🎯 **PRIORITY 1: CRITICAL (IMPLEMENT WEEK 1-2)**

### **SOURCE 1: OCC BSA/AML PROCEDURES** ⚠️ HIGHEST PRIORITY

**Regulator:** Office of the Comptroller of the Currency (OCC)  
**Category:** Bank Secrecy Act / Anti-Money Laundering  
**Test Cases:** 8 (23% of total)  
**Customer Impact:** Every mid-size bank, quarterly exams  
**Implementation Priority:** P0 - Start here

#### **Official Sources:**

```yaml
Primary Document:
  title: "BSA/AML Examination Manual"
  url: "https://www.occ.gov/publications-and-resources/publications/banker-education/files/pub-bsa-aml-examination-manual.pdf"
  format: PDF
  size: 418 pages
  last_updated: "November 2024"
  
Website:
  url: "https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html"
  format: HTML
  update_frequency: Monthly
  
Bulletins:
  url: "https://www.occ.gov/news-issuances/bulletins/index-bulletins.html"
  filter_by: "BSA"
  format: PDF
  update_frequency: As needed
```

#### **What to Extract:**

```json
{
  "regulations": [
    {
      "regulation_id": "OCC-BSA-WIRE-001",
      "regulation_name": "Wire Transfer Dual Approval Requirement",
      "category": "BSA/AML",
      "subcategory": "Wire Transfers",
      
      "requirements": [
        {
          "requirement_id": "OCC-BSA-WIRE-001-R1",
          "requirement_text": "Wire transfers exceeding $10,000 require dual approval from two separate authorized employees",
          "rule_type": "dual_approval_limit",
          "threshold_amount": 10000.00,
          "threshold_currency": "USD",
          "threshold_type": "per_transaction",
          "approval_count_required": 2,
          "separation_of_duties": true,
          "test_requirement": "Verify system prevents single-person approval for wire transfers >= $10,000"
        },
        {
          "requirement_id": "OCC-BSA-WIRE-001-R2",
          "requirement_text": "Approvers must be different individuals (cannot be same person)",
          "rule_type": "separation_of_duties",
          "test_requirement": "Verify system blocks same user ID from approving twice"
        },
        {
          "requirement_id": "OCC-BSA-WIRE-001-R3",
          "requirement_text": "Wire transfer records must be retained for 5 years",
          "rule_type": "record_retention",
          "retention_period_years": 5,
          "test_requirement": "Verify wire transfer audit trail stored for 5+ years"
        }
      ],
      
      "kpis": [
        {
          "kpi_id": "OCC-BSA-KPI-001",
          "kpi_name": "Wire Transfer Dual Approval Compliance Rate",
          "kpi_description": "Percentage of wire transfers >= $10K that received proper dual approval",
          "kpi_formula": "(Count of wires >= $10K with 2 approvals / Total wires >= $10K) * 100",
          "target_value": 100.0,
          "target_unit": "percent",
          "monitoring_frequency": "daily",
          "alert_threshold": 99.0,
          "criticality": "high"
        },
        {
          "kpi_id": "OCC-BSA-KPI-002",
          "kpi_name": "Same-Person Approval Violations",
          "kpi_description": "Count of wire transfers where same person approved twice",
          "kpi_formula": "Count of wires with approver1_id = approver2_id",
          "target_value": 0,
          "target_unit": "count",
          "monitoring_frequency": "real-time",
          "alert_threshold": 1,
          "criticality": "critical"
        }
      ],
      
      "test_scenarios": [
        {
          "scenario_id": "TC-001",
          "scenario_name": "Wire transfer $10,001 requires dual approval",
          "priority": "critical",
          "bdd_format": {
            "given": "User is authorized wire transfer initiator",
            "and": "Wire amount is $10,001 USD",
            "when": "User attempts to approve with single signature",
            "then": "System blocks transaction and displays error: 'Dual approval required for amounts >= $10,000'",
            "and": "Transaction status remains 'Pending Second Approval'"
          },
          "pytest_template": "test_wire_transfer_dual_approval_required.py"
        },
        {
          "scenario_id": "TC-002",
          "scenario_name": "Same person cannot approve wire twice",
          "priority": "critical",
          "bdd_format": {
            "given": "Wire transfer $15,000 initiated by User A",
            "and": "User A is first approver",
            "when": "User A attempts to be second approver",
            "then": "System rejects with error: 'Approver must be different from initiator and first approver'",
            "and": "Transaction remains in 'Pending' status"
          }
        }
      ],
      
      "source_metadata": {
        "source_url": "https://www.occ.gov/publications-and-resources/publications/banker-education/files/pub-bsa-aml-examination-manual.pdf",
        "document_title": "BSA/AML Examination Manual",
        "section": "Wire Transfer Procedures",
        "page_number": 234,
        "effective_date": "2023-01-01",
        "last_verified": "2024-11-15",
        "exam_frequency": "quarterly",
        "penalty_range": "$10,000 - $1,000,000 per violation"
      }
    }
  ]
}
```

#### **Scraping Plan:**

```yaml
scraper_name: "occ_bsa_scraper"
scraper_file: "src/scrapers/usa/occ_bsa_scraper.py"
implementation_priority: 1

approach:
  method: "PDF text extraction + LLM parsing"
  tools:
    - pdfplumber (PDF extraction)
    - Claude Sonnet 4 (structured extraction)
    - BeautifulSoup (HTML parsing for bulletins)
  
steps:
  1_fetch_documents:
    - "Download BSA/AML Examination Manual PDF"
    - "Scrape bulletin index page for recent BSA bulletins"
    - "Download new/updated bulletin PDFs"
  
  2_extract_sections:
    - "Parse PDF table of contents"
    - "Extract sections: Wire Transfers, Customer Due Diligence, SAR Filing, etc."
    - "Identify page ranges for each section"
  
  3_extract_requirements:
    - "Send each section to Claude with extraction prompt"
    - "Parse LLM JSON response"
    - "Extract: requirement_text, thresholds, rule_type, test_requirement"
  
  4_extract_kpis:
    - "Identify monitoring/measurement language"
    - "Extract formulas, targets, frequencies"
  
  5_store_database:
    - "Insert into 'regulations' table"
    - "Insert into 'compliance_requirements' table"
    - "Insert into 'compliance_kpis' table"
    - "Insert into 'test_scenarios' table"

schedule:
  initial_scrape: "manual_trigger"
  update_frequency: "monthly"
  bulletin_check: "weekly"

output:
  estimated_regulations: 12
  estimated_requirements: 45
  estimated_kpis: 18
  estimated_test_scenarios: 8
```

#### **Action Items:**

```markdown
Week 1 - Day 1-2:
- [ ] Implement `src/scrapers/usa/occ_bsa_scraper.py`
- [ ] Write PDF extraction logic (pdfplumber)
- [ ] Write section detection logic
- [ ] Test on BSA/AML Manual (pages 1-50 first)

Week 1 - Day 3-4:
- [ ] Implement LLM extraction prompt
- [ ] Test Claude Sonnet 4 extraction on 5 sample sections
- [ ] Validate JSON output format
- [ ] Store in database (test with 5 requirements)

Week 1 - Day 5:
- [ ] Run full scraper on entire BSA/AML Manual
- [ ] Verify 45+ requirements extracted
- [ ] Verify 8 test scenarios generated
- [ ] Run data quality checks

Success Criteria:
✓ 45+ requirements in database
✓ All 8 test cases (TC-001 to TC-008) extracted
✓ KPIs with formulas extracted
✓ Source metadata tracked
```

---

### **SOURCE 2: FinCEN SAR FILING REQUIREMENTS** ⚠️ CRITICAL

**Regulator:** Financial Crimes Enforcement Network (FinCEN)  
**Category:** Anti-Money Laundering  
**Test Cases:** 6 (17% of total)  
**Customer Impact:** Monthly SAR filings required  
**Implementation Priority:** P0

#### **Official Sources:**

```yaml
Primary Resource:
  title: "BSA Electronic Filing Requirements for Suspicious Activity Reports"
  url: "https://www.fincen.gov/resources/filing-information"
  format: HTML + PDF
  
SAR Forms:
  form_url: "https://www.fincen.gov/sites/default/files/shared/FinCEN_SAR_ElectronicFilingRequirements.pdf"
  instructions_url: "https://www.fincen.gov/sites/default/files/shared/SAR_instructions.pdf"
  
FAQs:
  url: "https://www.fincen.gov/resources/statutes-and-regulations/fincen-guidance"
  filter: "SAR"
  
API:
  filing_system: "BSA E-Filing System"
  url: "https://bsaefiling.fincen.treas.gov/main.html"
  note: "No public API, manual filing only"
```

#### **What to Extract:**

```json
{
  "regulations": [
    {
      "regulation_id": "FINCEN-SAR-001",
      "regulation_name": "Suspicious Activity Report Filing Thresholds and Deadlines",
      "category": "AML",
      "subcategory": "SAR Filing",
      
      "filing_triggers": [
        {
          "trigger_id": "SAR-TRIGGER-001",
          "trigger_description": "Insider abuse involving any amount",
          "minimum_amount": 0,
          "maximum_amount": null,
          "currency": "USD",
          "filing_mandatory": true,
          "filing_deadline_days": 30,
          "deadline_from": "date_of_detection"
        },
        {
          "trigger_id": "SAR-TRIGGER-002",
          "trigger_description": "Violations aggregating $5,000 or more where suspect can be identified",
          "minimum_amount": 5000.00,
          "currency": "USD",
          "filing_mandatory": true,
          "filing_deadline_days": 30,
          "deadline_from": "date_of_initial_detection"
        },
        {
          "trigger_id": "SAR-TRIGGER-003",
          "trigger_description": "Violations aggregating $25,000 or more regardless of suspect identification",
          "minimum_amount": 25000.00,
          "currency": "USD",
          "filing_mandatory": true,
          "filing_deadline_days": 30
        }
      ],
      
      "required_fields": [
        {
          "field_id": "SAR-FIELD-001",
          "field_name": "subject_information_name",
          "field_description": "Full name of subject individual or entity",
          "field_type": "text",
          "max_length": 150,
          "required": true,
          "validation_rule": "must_not_be_empty"
        },
        {
          "field_id": "SAR-FIELD-002",
          "field_name": "subject_tin_ssn",
          "field_description": "Subject TIN, EIN, or SSN",
          "field_type": "text",
          "format": "XXX-XX-XXXX or XX-XXXXXXX",
          "required": true,
          "validation_rule": "valid_tin_format"
        },
        {
          "field_id": "SAR-FIELD-003",
          "field_name": "suspicious_activity_date",
          "field_description": "Date or date range when suspicious activity occurred",
          "field_type": "date",
          "required": true
        },
        {
          "field_id": "SAR-FIELD-004",
          "field_name": "total_dollar_amount",
          "field_description": "Total dollar amount involved in suspicious activity",
          "field_type": "numeric",
          "min_value": 0.00,
          "max_value": 999999999.99,
          "currency": "USD",
          "required": true
        }
      ],
      
      "test_scenarios": [
        {
          "scenario_id": "TC-013",
          "scenario_name": "SAR filing triggered for $5,500 suspicious wire transfer",
          "priority": "high",
          "bdd_format": {
            "given": "Customer sends wire transfer $5,500 to high-risk jurisdiction",
            "and": "Transaction flagged as suspicious by monitoring system",
            "when": "Compliance officer reviews alert",
            "then": "System generates SAR filing task with 30-day deadline",
            "and": "SAR form pre-populated with transaction details"
          }
        },
        {
          "scenario_id": "TC-014",
          "scenario_name": "SAR filing deadline enforcement - 30 days",
          "priority": "high",
          "bdd_format": {
            "given": "Suspicious activity detected on Jan 1, 2026",
            "and": "SAR filing task created with due date Jan 31, 2026",
            "when": "Time reaches Jan 31, 2026 EOD",
            "and": "SAR not yet filed",
            "then": "System sends escalation alert to compliance manager",
            "and": "Incident logged for regulatory exam"
          }
        }
      ]
    }
  ]
}
```

#### **Scraping Plan:**

```yaml
scraper_name: "fincen_sar_scraper"
implementation_priority: 2

approach:
  method: "HTML parsing + PDF extraction"
  
steps:
  1: "Scrape https://www.fincen.gov/resources/filing-information"
  2: "Download SAR instructions PDF"
  3: "Extract filing thresholds ($5K, $25K, insider abuse)"
  4: "Extract required fields from form instructions"
  5: "Extract deadlines (30 days from detection)"
  6: "Store in database"

schedule:
  update_frequency: "quarterly"
  
output:
  estimated_regulations: 3
  estimated_requirements: 12
  estimated_test_scenarios: 6
```

#### **Action Items:**

```markdown
Week 1 - Day 3-4:
- [ ] Implement FinCEN SAR scraper
- [ ] Extract filing thresholds ($0, $5K, $25K)
- [ ] Extract 30-day deadline rule
- [ ] Extract required SAR form fields (50+ fields)
- [ ] Store in database

Success Criteria:
✓ 3 filing triggers stored
✓ 50+ required fields extracted
✓ 6 test scenarios generated
```

---

### **SOURCE 3: OFAC SDN LIST** ⚠️ CRITICAL (HAS API!)

**Regulator:** Office of Foreign Assets Control (OFAC)  
**Category:** Sanctions Screening  
**Test Cases:** 5 (14% of total)  
**Customer Impact:** Real-time screening required  
**Implementation Priority:** P0 - **EASIEST (Use API)**

#### **Official Sources:**

```yaml
SDN List (Human Readable):
  url: "https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists"
  format: HTML + PDF
  
SDN List (Machine Readable) ⚡ USE THIS:
  url: "https://ofac.treasury.gov/specially-designated-nationals-list-data-formats-data-schemas"
  formats:
    - XML: "https://www.treasury.gov/ofac/downloads/sdn.xml"
    - CSV: "https://www.treasury.gov/ofac/downloads/sdn.csv"
    - TXT (pipe-delimited): "https://www.treasury.gov/ofac/downloads/sdn.txt"
  update_frequency: "Daily (typically 2-4 PM ET)"
  
Sanctions Programs:
  url: "https://ofac.treasury.gov/sanctions-programs-and-country-information"
  
50% Rule:
  url: "https://ofac.treasury.gov/faqs/topic/1501"
  description: "Entities 50%+ owned by SDN are also sanctioned"
```

#### **What to Extract:**

```json
{
  "regulations": [
    {
      "regulation_id": "OFAC-SDN-001",
      "regulation_name": "SDN List Screening Requirement",
      "category": "Sanctions",
      
      "screening_rules": [
        {
          "rule_id": "OFAC-SCREEN-001",
          "rule_text": "All wire transfer beneficiaries must be screened against SDN list before processing",
          "screening_timing": "real-time_before_execution",
          "screening_method": "fuzzy_name_matching",
          "match_threshold": 90,
          "match_algorithm": "levenshtein_distance",
          "action_on_match": "block_transaction_pending_manual_review"
        },
        {
          "rule_id": "OFAC-50PERCENT-001",
          "rule_text": "Entities 50% or more owned by SDN are subject to same restrictions",
          "ownership_threshold": 50.0,
          "ownership_calculation": "aggregate_all_sdn_owners",
          "action": "apply_same_blocking_restrictions"
        }
      ],
      
      "sdn_entries": [
        {
          "sdn_id": "12345",
          "uid": "12345",
          "entity_type": "individual",
          "first_name": "John",
          "last_name": "Doe",
          "full_name": "John Doe",
          "title": null,
          "programs": ["UKRAINE-EO13662", "RUSSIA-EO14024"],
          "addresses": [
            {
              "address1": "123 Main St",
              "city": "Moscow",
              "country": "Russia"
            }
          ],
          "dates_of_birth": ["1975-03-15"],
          "nationalities": ["Russian"],
          "remarks": "Additional identifying information",
          "effective_date": "2022-03-01",
          "source_list_url": "https://www.treasury.gov/ofac/downloads/sdn.xml",
          "last_updated": "2024-11-20"
        }
      ],
      
      "test_scenarios": [
        {
          "scenario_id": "TC-007",
          "scenario_name": "Wire transfer to SDN-listed individual blocked",
          "priority": "critical",
          "bdd_format": {
            "given": "John Doe is on OFAC SDN list (UID 12345)",
            "and": "User initiates wire transfer to John Doe, Moscow, Russia",
            "when": "System performs OFAC screening",
            "then": "Transaction blocked immediately with status 'OFAC_MATCH'",
            "and": "Alert sent to compliance officer with match details",
            "and": "Transaction audit log records blocking reason"
          }
        },
        {
          "scenario_id": "TC-008",
          "scenario_name": "Fuzzy name matching - 92% similarity triggers review",
          "priority": "high",
          "bdd_format": {
            "given": "SDN list contains 'John Doe'",
            "and": "User initiates wire to 'Jon Doe' (92% name match)",
            "when": "System performs fuzzy matching",
            "then": "Transaction held for manual review",
            "and": "System displays: 'Potential OFAC match detected - Review required'",
            "and": "Compliance officer receives alert with similarity score"
          }
        }
      ],
      
      "kpis": [
        {
          "kpi_id": "OFAC-KPI-001",
          "kpi_name": "OFAC Screening Coverage",
          "kpi_formula": "(Wires screened against SDN / Total wires) * 100",
          "target_value": 100.0,
          "monitoring_frequency": "real-time",
          "criticality": "critical"
        },
        {
          "kpi_id": "OFAC-KPI-002",
          "kpi_name": "False Positive Rate",
          "kpi_formula": "(False positive matches / Total matches) * 100",
          "target_value": "<5.0",
          "monitoring_frequency": "weekly"
        }
      ]
    }
  ]
}
```

#### **Scraping Plan (EASIEST - Use CSV/XML API):**

```yaml
scraper_name: "ofac_sdn_scraper"
implementation_priority: 1  # EASIEST - start here for quick win!

approach:
  method: "Direct CSV/XML download (NO web scraping needed!)"
  api_url: "https://www.treasury.gov/ofac/downloads/sdn.csv"
  
steps:
  1_download:
    - "HTTP GET sdn.csv"
    - "Parse CSV with pandas"
  
  2_parse:
    - "Extract: uid, entity_type, name, programs, addresses, dob"
    - "Normalize data structure"
  
  3_store:
    - "Upsert into 'ofac_sdn_entries' table"
    - "Track last_updated timestamp"
  
  4_incremental:
    - "Compare with previous version"
    - "Identify new/removed/updated entries"
    - "Generate change alerts"

schedule:
  update_frequency: "daily"
  update_time: "16:00 ET"  # After OFAC daily update
  
output:
  estimated_sdn_entries: ~8000
  estimated_requirements: 4
  estimated_test_scenarios: 5

implementation_time: "4 hours"  # FAST!
```

#### **Action Items:**

```markdown
Week 1 - Day 1:
- [ ] Implement OFAC SDN scraper (EASIEST - just download CSV!)
- [ ] Download https://www.treasury.gov/ofac/downloads/sdn.csv
- [ ] Parse CSV with pandas
- [ ] Store ~8000 SDN entries in database
- [ ] Implement daily update schedule

Success Criteria:
✓ 8000+ SDN entries in database
✓ Daily update cron job configured
✓ 5 test scenarios generated
✓ Change detection working (new/removed/updated)

Estimated Time: 4 hours total ⚡ QUICK WIN!
```

---

### **SOURCE 4: CFPB SECTION 1071** ⚠️ CRITICAL (NEW REGULATION)

**Regulator:** Consumer Financial Protection Bureau (CFPB)  
**Category:** Fair Lending - Small Business Data Collection  
**Test Cases:** 4 (11% of total)  
**Customer Impact:** Deadline July 1, 2026 (Tier 1 banks)  
**Implementation Priority:** P0 - **URGENT**

#### **Official Sources:**

```yaml
Final Rule:
  title: "Small Business Lending Data Collection (Regulation B)"
  url: "https://www.consumerfinance.gov/rules-policy/final-rules/small-business-lending-data-collection-rule-1071/"
  federal_register_url: "https://www.federalregister.gov/documents/2023/03/31/2023-05680/small-business-lending-data-collection-rule"
  effective_date: "2024-05-20"
  
Compliance Deadlines:
  tier_1: "2026-07-01"  # ≥2,500 originations
  tier_2: "2027-07-01"  # ≥500 originations
  tier_3: "2028-07-01"  # ≥100 originations
  
Implementation Guide:
  url: "https://www.consumerfinance.gov/compliance/compliance-resources/small-business-lending-resources/small-business-lending-rule-implementation-guide/"
  
FAQs:
  url: "https://www.consumerfinance.gov/compliance/compliance-resources/small-business-lending-resources/small-business-lending-faqs/"
```

#### **What to Extract:**

```json
{
  "regulations": [
    {
      "regulation_id": "CFPB-1071-001",
      "regulation_name": "Section 1071 Small Business Lending Data Collection",
      "category": "Fair Lending",
      "subcategory": "Data Collection",
      
      "applicability": {
        "loan_types": [
          "small_business_loan",
          "small_business_line_of_credit"
        ],
        "loan_amount_threshold": 5000000.00,
        "loan_amount_threshold_currency": "USD",
        "geographic_scope": "USA"
      },
      
      "compliance_tiers": [
        {
          "tier": 1,
          "definition": "≥2,500 small business loans originated in each of two preceding years",
          "compliance_date": "2026-07-01",
          "origination_threshold": 2500,
          "time_period": "two_preceding_calendar_years"
        },
        {
          "tier": 2,
          "definition": "≥500 and <2,500 small business loans",
          "compliance_date": "2027-07-01",
          "origination_threshold": 500
        },
        {
          "tier": 3,
          "definition": "≥100 and <500 small business loans",
          "compliance_date": "2028-07-01",
          "origination_threshold": 100
        }
      ],
      
      "required_data_points": [
        {
          "data_point_id": "1071-DP-001",
          "data_point_name": "principal_owners_race",
          "description": "Race of principal owners (up to 4)",
          "field_type": "multi-select",
          "collection_method": "applicant_self_report",
          "options": [
            "American Indian or Alaska Native",
            "Asian",
            "Black or African American",
            "Native Hawaiian or Other Pacific Islander",
            "White",
            "I do not wish to provide this information"
          ],
          "max_selections": 5,
          "required": true,
          "reporting_required": true
        },
        {
          "data_point_id": "1071-DP-002",
          "data_point_name": "principal_owners_sex",
          "description": "Sex of principal owners",
          "field_type": "single-select",
          "collection_method": "applicant_self_report",
          "options": [
            "Male",
            "Female",
            "I do not wish to provide this information"
          ],
          "required": true
        },
        {
          "data_point_id": "1071-DP-003",
          "data_point_name": "principal_owners_ethnicity",
          "description": "Ethnicity of principal owners",
          "field_type": "single-select",
          "collection_method": "applicant_self_report",
          "options": [
            "Hispanic or Latino",
            "Not Hispanic or Latino",
            "I do not wish to provide this information"
          ],
          "required": true
        },
        {
          "data_point_id": "1071-DP-004",
          "data_point_name": "number_of_principal_owners",
          "description": "Total number of principal owners",
          "field_type": "numeric",
          "min_value": 0,
          "max_value": 4,
          "required": true,
          "note": "Collect demographic data for up to 4 principal owners"
        }
      ],
      
      "test_scenarios": [
        {
          "scenario_id": "TC-019",
          "scenario_name": "Section 1071 data collection for small business loan application",
          "priority": "critical",
          "bdd_format": {
            "given": "Applicant applies for small business loan < $5M",
            "and": "Financial institution is Tier 1 (≥2,500 originations)",
            "and": "Current date >= 2026-07-01",
            "when": "System processes loan application",
            "then": "System prompts for race, sex, ethnicity of up to 4 principal owners",
            "and": "Each demographic field includes 'I do not wish to provide' option",
            "and": "System allows application to proceed regardless of response"
          }
        },
        {
          "scenario_id": "TC-020",
          "scenario_name": "Section 1071 - Applicant declines to provide demographic data",
          "priority": "high",
          "bdd_format": {
            "given": "Small business loan application in progress",
            "when": "Applicant selects 'I do not wish to provide' for all demographic fields",
            "then": "System accepts application without demographic data",
            "and": "System records response as 'applicant_declined'",
            "and": "Loan underwriting proceeds normally"
          }
        }
      ]
    }
  ]
}
```

#### **Scraping Plan:**

```yaml
scraper_name: "cfpb_1071_scraper"
implementation_priority: 3

approach:
  method: "HTML scraping + PDF extraction"
  
steps:
  1: "Scrape implementation guide page"
  2: "Download final rule PDF from Federal Register"
  3: "Extract required data points (88 total)"
  4: "Extract compliance tiers and deadlines"
  5: "Extract exemptions and special rules"
  6: "Store in database"

schedule:
  update_frequency: "quarterly"  # Stable regulation, infrequent updates
  
output:
  estimated_data_points: 88
  estimated_requirements: 15
  estimated_test_scenarios: 4
```

#### **Action Items:**

```markdown
Week 2 - Day 1-2:
- [ ] Implement CFPB 1071 scraper
- [ ] Extract 88 required data points
- [ ] Extract tier definitions and deadlines
- [ ] Store in database
- [ ] Generate 4 test scenarios

Success Criteria:
✓ 88 data points extracted
✓ 3 compliance tiers stored with deadlines
✓ 4 test scenarios generated
✓ Tier 1 deadline (Jul 1, 2026) flagged as urgent
```

---

## 🎯 **PRIORITY 2: IMPORTANT (IMPLEMENT WEEK 3-4)**

### **SOURCE 5: FDIC RISK MANAGEMENT MANUAL**

**Regulator:** Federal Deposit Insurance Corporation  
**Category:** Risk Management, BSA/AML  
**Test Cases:** 3  
**Implementation Priority:** P1

```yaml
source:
  url: "https://www.fdic.gov/regulations/safety/manual/"
  sections:
    - "Section 8: Bank Secrecy Act (BSA)"
    - "Section 5: Loan Portfolio Management"
    - "Section 11: Information Technology"
  
scraping_plan:
  extract: "Third-party risk management requirements"
  extract: "IT security controls"
  extract: "Loan underwriting standards"
```

---

### **SOURCE 6: FEDERAL RESERVE REGULATION E**

**Regulator:** Federal Reserve Board  
**Category:** Consumer Protection  
**Test Cases:** 2  
**Implementation Priority:** P1

```yaml
source:
  url: "https://www.federalreserve.gov/supervisionreg/regecg.htm"
  focus: "Electronic Fund Transfer error resolution"
  
key_requirements:
  - "10-day error investigation timeline"
  - "45-day maximum resolution period"
  - "Provisional credit within 10 business days"
```

---

### **SOURCE 7: CFPB HMDA REPORTING**

**Regulator:** Consumer Financial Protection Bureau  
**Category:** Fair Lending  
**Test Cases:** 3  
**Implementation Priority:** P1

```yaml
source:
  url: "https://www.consumerfinance.gov/data-research/hmda/"
  
key_requirements:
  - "Annual HMDA LAR (Loan Application Register) submission"
  - "Data submission by March 1 each year"
  - "25+ data points per covered loan"
```

---

## 📋 **COMPLETE SOURCE LIST (All 18 Sources)**

### **Summary Table:**

| # | Source | Regulator | Category | Priority | Test Cases | Deadline | Implementation Week |
|---|--------|-----------|----------|----------|------------|----------|-------------------|
| 1 | **OCC BSA/AML** | OCC | BSA/AML | P0 | 8 | Ongoing | Week 1 |
| 2 | **FinCEN SAR** | FinCEN | AML | P0 | 6 | Monthly | Week 1 |
| 3 | **OFAC SDN** | OFAC | Sanctions | P0 | 5 | Daily | Week 1 |
| 4 | **CFPB 1071** | CFPB | Fair Lending | P0 | 4 | Jul 1, 2026 | Week 2 |
| 5 | **FDIC Manual** | FDIC | Risk Mgmt | P1 | 3 | Quarterly | Week 3 |
| 6 | **FRB Reg E** | FRB | Consumer | P1 | 2 | Ongoing | Week 3 |
| 7 | **CFPB HMDA** | CFPB | Fair Lending | P1 | 3 | Annual | Week 3 |
| 8 | **FFIEC BSA** | FFIEC | BSA/AML | P1 | 2 | Ongoing | Week 3 |
| 9 | **OCC Third-Party** | OCC | Risk Mgmt | P1 | 2 | Semi-annual | Week 4 |
| 10 | **FinCEN CDD** | FinCEN | KYC | P1 | 2 | Ongoing | Week 4 |
| 11 | **CFPB Reg B** | CFPB | Fair Lending | P2 | 1 | Ongoing | Week 4 |
| 12 | **SEC** | SEC | Securities | P2 | 0 | N/A | Week 5 |
| 13 | **FRB SR Letters** | FRB | Various | P2 | 0 | As issued | Week 5 |
| 14 | **DFAST** | FRB | Stress Test | P2 | 0 | Annual | Week 5 |
| 15 | **OCC Circulars** | OCC | Various | P2 | 0 | As issued | Week 5 |
| 16 | **FinCEN CTR** | FinCEN | AML | P2 | 0 | Ongoing | Week 5 |
| 17 | **GLBA Privacy** | FTC/FRB | Privacy | P2 | 2 | Annual | Week 5 |
| 18 | **State Regs** | State | Various | P3 | 0 | Varies | Future |

**Total:** 35 test cases across 18 sources

---

## 🛠️ **IMPLEMENTATION ROADMAP**

### **Week-by-Week Plan:**

```markdown
WEEK 1: Critical Foundations (19 test cases, 54%)
├─ Day 1-2: OCC BSA/AML Scraper
│  └─ Output: 8 test cases, 45 requirements
├─ Day 3-4: FinCEN SAR Scraper
│  └─ Output: 6 test cases, 12 requirements
└─ Day 5: OFAC SDN Scraper (QUICK WIN!)
   └─ Output: 5 test cases, 8000 SDN entries

WEEK 2: Fair Lending (7 test cases, 20%)
├─ Day 1-2: CFPB Section 1071
│  └─ Output: 4 test cases, 88 data points
└─ Day 3-5: CFPB HMDA
   └─ Output: 3 test cases, 25 data points

WEEK 3: Consumer Protection (4 test cases, 11%)
├─ Day 1-2: Federal Reserve Reg E
│  └─ Output: 2 test cases
├─ Day 3-4: FDIC Risk Management
│  └─ Output: 3 test cases
└─ Day 5: FFIEC BSA Manual
   └─ Output: 2 test cases

WEEK 4: Risk & KYC (4 test cases, 11%)
├─ Day 1-2: OCC Third-Party Risk
│  └─ Output: 2 test cases
├─ Day 3-4: FinCEN CDD Rule
│  └─ Output: 2 test cases
└─ Day 5: Testing & Validation

WEEK 5: Privacy & Remaining (2 test cases, 6%)
├─ GLBA Privacy Requirements
└─ Final integration & testing
```

---

## 📊 **DATABASE SCHEMA**

### **Tables to Create:**

```sql
-- Regulations master table
CREATE TABLE regulations (
    id SERIAL PRIMARY KEY,
    regulation_id VARCHAR(100) UNIQUE NOT NULL,
    regulation_name VARCHAR(500) NOT NULL,
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(50),
    regulator VARCHAR(50) NOT NULL,
    geography VARCHAR(20) DEFAULT 'usa',
    effective_date DATE,
    compliance_deadline DATE,
    exam_frequency VARCHAR(20),
    source_url TEXT,
    last_updated TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Compliance requirements
CREATE TABLE compliance_requirements (
    id SERIAL PRIMARY KEY,
    requirement_id VARCHAR(100) UNIQUE NOT NULL,
    regulation_id VARCHAR(100) REFERENCES regulations(regulation_id),
    requirement_text TEXT NOT NULL,
    rule_type VARCHAR(50),
    threshold_amount NUMERIC(15,2),
    threshold_currency VARCHAR(3),
    test_requirement TEXT,
    parameters JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- KPIs
CREATE TABLE compliance_kpis (
    id SERIAL PRIMARY KEY,
    kpi_id VARCHAR(100) UNIQUE NOT NULL,
    regulation_id VARCHAR(100) REFERENCES regulations(regulation_id),
    kpi_name VARCHAR(200) NOT NULL,
    kpi_formula TEXT,
    target_value NUMERIC(10,2),
    target_unit VARCHAR(20),
    monitoring_frequency VARCHAR(20),
    alert_threshold NUMERIC(10,2),
    criticality VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Test scenarios
CREATE TABLE test_scenarios (
    id SERIAL PRIMARY KEY,
    scenario_id VARCHAR(100) UNIQUE NOT NULL,
    regulation_id VARCHAR(100) REFERENCES regulations(regulation_id),
    scenario_name VARCHAR(200) NOT NULL,
    priority VARCHAR(20),
    given TEXT,
    when_step TEXT,
    then_step TEXT,
    pytest_template TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- OFAC SDN entries (special table)
CREATE TABLE ofac_sdn_entries (
    id SERIAL PRIMARY KEY,
    sdn_id VARCHAR(20) UNIQUE NOT NULL,
    uid VARCHAR(20),
    entity_type VARCHAR(50),
    full_name VARCHAR(200),
    programs TEXT[],
    addresses JSONB,
    dates_of_birth DATE[],
    effective_date DATE,
    last_updated TIMESTAMP DEFAULT NOW()
);
```

---

## ✅ **SUCCESS METRICS**

### **Phase 1 Complete (Week 2):**
```
✓ 4 scrapers implemented (OCC, FinCEN, OFAC, CFPB 1071)
✓ 19 test cases in database
✓ 8000+ OFAC SDN entries loaded
✓ 150+ compliance requirements extracted
✓ Daily OFAC updates automated
```

### **Phase 2 Complete (Week 4):**
```
✓ 10 scrapers implemented
✓ 30 test cases in database
✓ 250+ requirements extracted
✓ All P0 and P1 sources covered
```

### **Final (Week 5):**
```
✓ 15 scrapers implemented
✓ 35 test cases in database
✓ 300+ requirements extracted
✓ 100% coverage of mid-size bank needs
✓ Ready for customer pilots
```

---

## 📝 **NEXT STEPS**

### **Immediate Actions (This Week):**

```markdown
Day 1:
- [ ] Run database migration (create 5 tables)
- [ ] Implement OFAC SDN scraper (QUICK WIN - 4 hours)
- [ ] Load 8000+ SDN entries
- [ ] Test OFAC screening API

Day 2-3:
- [ ] Implement OCC BSA/AML scraper
- [ ] Extract wire transfer requirements
- [ ] Generate 8 test scenarios

Day 4-5:
- [ ] Implement FinCEN SAR scraper
- [ ] Extract SAR filing thresholds
- [ ] Generate 6 test scenarios
```

---

**This document provides everything needed to extract complete USA banking compliance data!** 🚀
