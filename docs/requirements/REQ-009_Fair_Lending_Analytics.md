# REQ-009: Fair Lending Statistical Analysis

## Overview
**Priority:** Medium
**Type:** Enhancement — New Analytics Layer on Existing CFPB/HMDA Data
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 first (HMDA peer benchmark data), then Pipeline 2 (analytics service)
**Implementation Order:** Start with Pipeline 2 analytics using data already in the VPC.
Pipeline 1 HMDA peer benchmark sync is a follow-on once the analytics layer is validated.
**Status:** Backlog

## Business Context
The system already collects HMDA data (Reg B, HMDA LAR) and validates Section 1071 fields.
What's missing is the statistical analytics layer that regulators and compliance officers use
to self-screen for fair lending risk before an examination. Examiners use comparative file
review (CFR), HMDA peer group benchmarking, and redlining geographic screening. OneSumX
wraps these analytics around HMDA data and sells it as a standalone risk management tool.
Building this layer on top of the existing HMDA/Reg B infrastructure closes the gap without
requiring a new connector.

## Requirements

### Functional Requirements
1. Add a `FairLendingAnalyzer` service in `src/services/fair_lending_analyzer.py`:
   - **Comparative File Review (CFR):** Compare approval rates, pricing, and loan terms
     across demographic groups (race, ethnicity, sex) for similarly situated applicants
     (same product, same geography, similar credit profile). Flag when approval rate gap
     exceeds configurable threshold (default: 10 percentage points)
   - **HMDA Peer Benchmarking:** Compare bank's denial rate by demographic group against
     FFIEC peer group data for similar asset-size institutions in the same MSA
   - **Redlining Screening:** Geographic analysis — flag if application volume from majority-
     minority census tracts is disproportionately low relative to the bank's assessment area
   - **Pricing Disparity Check:** Flag when average APR spread differs by more than threshold
     across demographic groups for comparable loan products
2. Add new API endpoint `POST /api/v1/fair-lending/analyze` accepting HMDA LAR data subset
3. Output: `FairLendingAnalysisResult` with flagged disparity findings, affected groups,
   gap percentages, and recommended review actions
4. Add minimum 8 test scenarios for each analysis type

### Pipeline 2 Changes
- Add `FairLendingAnalyzer` service to Pipeline 2 only (customer data never leaves VPC)
- Add `fair_lending` domain to `LocalComplianceProvider`
- Add new API endpoint in `src/api/validation_api.py`
- Add fair lending test scenarios to MCP compliance knowledge server
- 6-agent pipeline: recognize `fair_lending`, `hmda`, `redlining`, `disparate_impact`
  as compliance domains and generate fair lending test cases from JIRA stories

### Pipeline 1 Changes

The existing `CfpbConnector` has HMDA *regulation* URLs but none of the public *aggregate
data* URLs required for peer benchmarking and redlining. Three new source URL groups must
be added to Pipeline 1.

#### 1. Add to `CfpbConnector._DEFAULT_URLS` — HMDA Aggregate / Peer Data

```python
# HMDA aggregate data — public FFIEC/CFPB APIs, no PII
"hmda_data_browser_api":   "https://ffiec.cfpb.gov/api/public/hmda/",
"hmda_aggregate_reports":  "https://www.ffiec.gov/hmda/hmdaflat.htm",
"hmda_disclosure_reports": "https://www.ffiec.gov/npw/FinancialReport/ReturnFinancialReport",
"cfpb_fair_lending":       "https://www.consumerfinance.gov/fair-lending/",
```

**What they provide:**
- `hmda_data_browser_api` — CFPB HMDA Data Browser REST API; query aggregate approval/denial
  rates by MSA, loan type, and demographic group. No individual applicant data — all responses
  are aggregate counts. Used by `FairLendingAnalyzer` for HMDA peer benchmarking.
- `hmda_aggregate_reports` — FFIEC flat file downloads of HMDA aggregate tables published
  annually. Shows industry-wide lending patterns by institution peer group.
- `hmda_disclosure_reports` — Lender-level HMDA disclosure reports. Used to compare the
  bank's lending patterns against peer lenders in the same MSA.
- `cfpb_fair_lending` — CFPB fair lending supervisory guidance and exam procedures page.
  Adds fair lending exam scope to the existing CFPB compliance knowledge.

#### 2. Add to `CfpbConnector._DEFAULT_URLS` — Census Tract Data (Redlining)

```python
# FFIEC census / geography data — required for redlining geographic analysis
"ffiec_census_flat_files":   "https://www.ffiec.gov/censusproducts.htm",
"ffiec_geocoding_service":   "https://geomap.ffiec.gov/FFIECGeocMap/GeocodeMap1.aspx",
"census_acs_summary":        "https://data.census.gov/table/ACSDP5Y2023.DP05",
```

**What they provide:**
- `ffiec_census_flat_files` — FFIEC annual census flat files: maps every census tract to
  MSA, minority population percentage, median family income. Required to determine which
  tracts are majority-minority for redlining screening.
- `ffiec_geocoding_service` — FFIEC public geocoding API: converts an address to census
  tract + MSA. Used to assign census tract codes to HMDA LAR records that lack them.
- `census_acs_summary` — Census Bureau ACS demographic profile table DP05: population by
  race and ethnicity per census tract. Authoritative source for majority-minority
  tract classification (tract is majority-minority when non-white population > 50%).

#### 3. Add new connector entry — FDIC BankFind API (Peer Group Identification)

Add to `FdicConnector._DEFAULT_URLS`:
```python
# FDIC Statistics on Depository Institutions — public REST API
"bankfind_api":       "https://banks.data.fdic.gov/api/",
"bankfind_summary":   "https://banks.data.fdic.gov/api/summary",
"bankfind_financials":"https://banks.data.fdic.gov/api/financials",
```

**What they provide:**
- `bankfind_api` — FDIC BankFind REST API: query any FDIC-insured bank's asset size,
  charter type, and peer group code. Used to identify which peer group (asset tier) the
  bank falls into for HMDA peer benchmarking.
- `bankfind_financials` — Annual financial data per institution: total assets, total loans,
  HMDA reportable loan counts. Used to build the peer comparison group (same asset tier,
  same MSA, same loan products).

#### Scraping Changes in `CfpbConnector._scrape_live()`

Add three new scrape steps after existing step 8:
```
Step 10. Scrape cfpb_fair_lending page
Step 11. Download FFIEC census flat file (annual CSV — cache aggressively, changes once/year)
Step 12. Call HMDA Data Browser API for aggregate denial rates by MSA and demographic group
         (parameterised by the bank's assessment area MSA codes, stored in client config)
```

Store census flat file and HMDA aggregate data as `doc_type: "aggregate_data"` entries
in `scraped_documents` — these sync to Pipeline 2 where `FairLendingAnalyzer` consumes them.

## Acceptance Criteria
- [ ] `FairLendingAnalyzer.comparative_file_review()` correctly calculates approval rate
      gap and flags when gap exceeds threshold
- [ ] Redlining screen returns flagged census tracts when application volume is
      disproportionately low in majority-minority areas
- [ ] All HMDA demographic data processed locally — no demographic data sent to Pipeline 1
- [ ] 8 fair lending test scenarios covering CFR, peer benchmarking, redlining, pricing
- [ ] New API endpoint documented and returns structured JSON result

## Technical Notes
- All demographic data processing must occur in Pipeline 2 only — HMDA applicant
  demographic data must never leave the customer's infrastructure
- The three new Pipeline 1 data sources (HMDA aggregate API, FFIEC census flat files,
  FDIC BankFind) are entirely public and contain no PII — safe to scrape in Pipeline 1
  and sync to Pipeline 2
- FFIEC census flat file is large (~50 MB CSV) — cache in Pipeline 1 DB and re-download
  annually only; do not re-download on every scrape cycle
- HMDA Data Browser API is a REST JSON API; use `requests.get()` with query params
  rather than BeautifulSoup HTML scraping — add a dedicated `scrape_hmda_aggregate_api()`
  helper in `_scraper_utils.py`
- FDIC BankFind API is also a REST JSON API; add `scrape_fdic_bankfind()` helper
- CFR statistical significance: use Fisher's exact test or chi-squared for small samples;
  document methodology in service docstring for examiner defensibility
- MSA code configuration: the bank's assessment area MSA codes must be stored in
  client config (`config/clients/{client}.yaml`) — HMDA Data Browser queries are
  scoped to those MSAs, not nationwide
