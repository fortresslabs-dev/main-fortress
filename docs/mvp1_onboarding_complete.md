# MVP1 Compliance Source Onboarding — Completion Report

**Date:** April 6, 2026 (updated April 8, 2026 — scraper stability fixes)
**Branch:** `testmind_banking_v4`
**Commits:** `9d37e7c` (initial HTML+PDF pipeline), `7c70683` (coverage gap closure)

---

## Status: ✅ COMPLETE

All 5 US regulatory sources specified in `mvp1.md` are fully onboarded with both HTML and PDF extraction.

---

## Source Coverage Summary

| Regulator | HTML | PDF | CSV | Total URLs | Status |
|-----------|------|-----|-----|------------|--------|
| OCC       | 4    | 2   | —   | 6          | ✅     |
| FDIC      | 4    | 1   | —   | 5          | ✅     |
| CFPB      | 8    | 1   | —   | 9          | ✅     |
| FinCEN    | 5    | 1   | —   | 6          | ✅     |
| OFAC      | 1    | —   | 1   | 2          | ✅     |
| **Total** | **22** | **5** | **1** | **28** |      |

---

## Connector Detail

### 1. OCC BSA/AML (`src/connectors/regulatory/usa/occ_bsa.py`)

**Class:** `OccBsaConnector`

| # | URL | Category | Type |
|---|-----|----------|------|
| 1 | `https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html` | BSA/AML Guidance | HTML |
| 2 | `https://www.occ.gov/news-issuances/bulletins/index-bulletins.html` | OCC Bulletins | HTML |
| 3 | `https://www.occ.gov/topics/laws-and-regulations/index-laws-and-regulations.html` | Laws and Regulations | HTML |
| 4 | `https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/index-comptrollers-handbook.html` | Comptrollers Handbook | HTML |
| 5 | `https://www.occ.gov/.../pub-ch-bank-supervision-process.pdf` | Bank Supervision Process | PDF |
| 6 | `https://www.occ.gov/.../pub-ch-large-bank-supervision.pdf` | Large Bank Supervision | PDF |

**Static rules:** 4 regulations, 15 requirements, 9 KPIs, 5 test scenarios
**Live validation:** PDF #5 = 294 chunks (154 pages), PDF #6 = 221 chunks

---

### 2. FDIC (`src/connectors/regulatory/usa/fdic.py`)

**Class:** `FdicConnector`

| # | URL | Category | Type |
|---|-----|----------|------|
| 1 | `https://www.fdic.gov/consumer-compliance-examination-manual` | Compliance Manual | HTML |
| 2 | `https://www.fdic.gov/news/financial-institution-letters` | FILs | HTML (custom scraper, follows up to 20 individual FIL links) |
| 3 | `https://www.fdic.gov/capital-markets` | Capital Adequacy | HTML |
| 4 | `https://www.fdic.gov/laws-and-regulations` | Laws and Regulations | HTML |
| 5 | `https://www.fdic.gov/system/files/2024-06/risk-management-manual.pdf` | Risk Management Manual | PDF |

**Static rules:** 3 regulations, 12 requirements, 8 KPIs, 4 test scenarios

---

### 3. CFPB + Reg E (`src/connectors/regulatory/usa/cfpb.py`)

**Class:** `CfpbConnector`

| # | URL | Category | Type |
|---|-----|----------|------|
| 1 | `https://www.consumerfinance.gov/.../small-business-lending.../` | Section 1071 | HTML |
| 2 | `https://www.consumerfinance.gov/data-research/hmda/` | HMDA | HTML |
| 3 | `https://www.consumerfinance.gov/rules-policy/regulations/1005/` | Regulation E | HTML |
| 4 | `https://www.consumerfinance.gov/rules-policy/regulations/1002/` | Regulation B | HTML |
| 5 | `https://www.consumerfinance.gov/rules-policy/regulations/1005/interp/` | Reg E Commentary | HTML |
| 6 | `https://www.consumerfinance.gov/rules-policy/final-rules/` | Final Rules Index | HTML |
| 7 | `https://www.consumerfinance.gov/compliance/supervisory-highlights/` | Supervisory Highlights | HTML |
| 8 | `https://www.consumerfinance.gov/compliance/compliance-resources/` | Compliance Resources | HTML |
| 9 | `https://www.consumerfinance.gov/.../2024-Guide.pdf` | HMDA Filing Guide | PDF |

**Static rules:** 5 regulations (incl. Reg E, Reg B, Section 1071), 14 requirements, 10 KPIs, 8 test scenarios
**RSS feed:** `https://www.consumerfinance.gov/feed/` (used for `check_updates()`)

---

### 4. FinCEN SAR (`src/connectors/regulatory/usa/fincen_sar.py`)

**Class:** `FincenSarConnector`

| # | URL | Category | Type |
|---|-----|----------|------|
| 1 | `https://www.fincen.gov/resources/filing-information` | SAR Filing | HTML |
| 2 | `https://www.fincen.gov/resources/fincens-legal-authorities` | Legal Authorities | HTML |
| 3 | `https://www.fincen.gov/boi` | Beneficial Ownership | HTML |
| 4 | `https://www.fincen.gov/resources/statutes-regulations/administrative-rulings` | Administrative Rulings | HTML |
| 5 | `https://www.fincen.gov/resources/statutes-regulations/guidance` | Guidance | HTML |
| 6 | `https://www.fincen.gov/.../sarreview_issue21.pdf` | SAR Activity Review | PDF |

**Static rules:** 3 regulations, 11 requirements, 7 KPIs, 5 test scenarios
**RSS feed:** `https://www.fincen.gov/rss.xml` (used for `check_updates()`)

**Note:** The original `mvp1.md` specified `/resources/advisories` and `/financial-institution-advisories` but both returned 404 (site restructured). Replaced with `/resources/statutes-regulations/administrative-rulings` and `/resources/statutes-regulations/guidance` which return 200 OK.

---

### 5. OFAC SDN (`src/connectors/regulatory/usa/ofac_sdn.py`)

**Class:** `OfacSdnConnector`

| # | URL | Category | Type |
|---|-----|----------|------|
| SDN | `https://www.treasury.gov/ofac/downloads/sdn.csv` | SDN List | CSV |
| 1 | `https://ofac.treasury.gov/sanctions-programs-and-country-information` | Sanctions Programs | HTML |

**SDN CSV parsing:** Extracts entity UID, name, type, programs, title, remarks
**Content selectors:** `.usa-prose`, `article` (Treasury Drupal site)
**XML update check:** `SDN_ADVANCED.XML` endpoint for publish-date detection
**Static rules:** 1 regulation, 6 requirements, 5 KPIs, 3 test scenarios

---

## Infrastructure Built

### PDF Pipeline (`src/services/pdf_pipeline.py`)
- Downloads PDFs via `requests` with retry/timeout
- Extracts text using `unstructured[pdf]` (v0.22.16) with `partition()`
- Chunks by title using `chunk_by_title()` with configurable `max_characters` (default 2000)
- Stores chunks in Weaviate `ComplianceDocument` collection
- Saves local PDF copies to `data/pdfs/<REGULATOR>/` for offline access

### Weaviate Collections
- **ComplianceDocument** (new) — 11 properties: `doc_id`, `regulator`, `document_title`, `section_title`, `content`, `page_number`, `chunk_index`, `source_url`, `document_type`, `content_hash`, `scraped_at`
- **ComplianceRule** — existing collection for structured rules
- Embeddings: OpenAI `text-embedding-3-large`

### HTML Scraper (`src/connectors/regulatory/usa/_scraper_utils.py`)
- `scrape_html_page()` — heading-based section extraction with fallback to full-content single chunk
- `scrape_pdf_url()` — PDF download + `unstructured` extraction with local save
- `make_session()` — configured requests session with User-Agent and retry
- `content_hash()` — SHA-256 hash for change detection
- Supports custom `content_selectors` per-page (e.g., `.usa-prose` for Treasury sites)

### Scheduler Integration (`src/services/scraper_scheduler.py`)
- `_sync_pdf_docs_to_weaviate()` — bulk-inserts PDF chunks into Weaviate
- `_sync_docs_via_processor()` — single-pass URL processing (fixed from double-fetch)
- `_system_healthy()` — circuit breaker checking CPU (>80%) and memory (>85%) before each cycle
- `SCRAPER_RUN_ON_STARTUP` — controls whether scrape fires on boot (default: off)
- Runs on 24-hour cycle alongside HTML rule sync

### Connector Plugin System
- `PluginManager` loads all 5 connectors from `config/connectors/usa.yaml`
- Each connector extends `BaseConnector` with `extract_all_rules()` and `_scrape_live()`
- Staggered execution: `SCRAPER_CONNECTOR_DELAY` seconds between each connector (default: 10s)
- `_scrape_live()` wrapped in try/except — failures fall back to static rules gracefully

### Rate Limiting (`src/connectors/regulatory/usa/_scraper_utils.py`)
- `_rate_limit_for_domain()` — per-hostname tracker enforcing minimum delay between requests
- `_get_request_delay()` — reads `SCRAPER_REQUEST_DELAY` from config (default: 1.0s)
- Applied to: `scrape_html_page`, `scrape_pdf_url`, `scrape_linked_pages`, `scrape_discovered_urls`, `scrape_hmda_fig_pages`, `scrape_fdic_risk_manual_sections`
- Discovery caps reduced: `max_html_pages=30`, `max_pdfs=10` (was 100/20)

---

## Test Suite

**File:** `tests/unit_test/test_scrapers.py`
**Total:** 37 tests, all passing (1.5s)

| Test Class | Tests | What it covers |
|------------|-------|----------------|
| `TestOccBsaConnector` | 5 | Static rules, regulation IDs, wire transfer rule, requirement keys, connect/disconnect |
| `TestFdicConnector` | 4 | Static rules, deposit insurance, regulation IDs, connect/disconnect |
| `TestCfpbConnector` | 5 | Static rules, Reg E presence, Reg E requirements, Section 1071, regulation IDs |
| `TestFincenSarConnector` | 4 | Static rules, SAR filing triggers, 30-day deadline, regulation IDs |
| `TestOfacSdnConnector` | 3 | SDN CSV parsing (mocked), entity fields, connection check |
| `TestScraperUtils` | 6 | HTML heading extraction, article fallback, HTTP error handling, SHA-256 hash, session creation, PDF without unstructured |
| `TestPluginManager` | 2 | Loads all 5 connectors, names match config |
| `TestPDFPipeline` | 5 | Local save, regulator dirs, HTTP error, chunk extraction, metadata keys |
| `TestWeaviateComplianceDocument` | 3 | Collection exists, properties correct, ComplianceRule unchanged |

**Autouse fixture** mocks `_scrape_live()` on all 5 connectors to prevent live HTTP calls during unit tests.

---

## Live Validation Results (April 6, 2026)

All 9 newly added HTML URLs verified live:

| Source | Category | Docs Scraped | Chars |
|--------|----------|-------------|-------|
| OCC | Laws & Regulations | 3 | 942 |
| OCC | Comptrollers Handbook | 8 | 13,134 |
| FinCEN | Administrative Rulings | 1 | 849 |
| FinCEN | Guidance | 1 | 472 |
| CFPB | Reg E Commentary | 2 | 234 |
| CFPB | Final Rules | 32 | 7,948 |
| CFPB | Supervisory Highlights | 17 | 8,161 |
| CFPB | Compliance Resources | 2 | 377 |
| OFAC | Sanctions Programs | 1 | 291 |

---

## Dependencies Added

| Package | Version | Purpose |
|---------|---------|---------|
| `unstructured[pdf]` | 0.22.16 | PDF text extraction and chunking |
| `tiktoken` | 0.12.0 | Token counting for chunk size management |
| `weaviate-client` | 4.20.4 | Weaviate v4 vector DB client |

**System dependencies:** `poppler-utils`, `tesseract-ocr`, `libmagic-dev`

---

## Deviations from mvp1.md

| Original Plan | Actual Implementation | Reason |
|---------------|----------------------|--------|
| FinCEN `/resources/advisories` | `/resources/statutes-regulations/administrative-rulings` | Original URL returns 404 (site restructured) |
| FinCEN `/financial-institution-advisories` | `/resources/statutes-regulations/guidance` | Original URL returns 404 (site restructured) |
| OFAC XML API (`SDN.XML`) | SDN CSV (`sdn.csv`) | CSV is simpler to parse, same data, more reliable |
| LangChain / MCP for PDF | `unstructured[pdf]` directly | Over-engineered; `unstructured` alone handles everything |
| Separate Reg E connector | Combined in `CfpbConnector` | Reg E is a CFPB regulation (12 CFR 1005) — single connector covers both |

---

## Scraper Stability Fixes (April 8, 2026)

### Problem: Instance CPU Exhaustion on Startup

Pipeline 1 (`fortress-pipeline1`) was causing the EC2 instance to become unresponsive within ~5 minutes of boot. Root cause: the scraper scheduler fired 30 seconds after startup with **zero concurrency controls**, hitting ~477 sequential HTTP GETs across 5 government domains simultaneously, while `_sync_docs_via_processor` **re-fetched every URL a second time** (double scrape bug). The container reached 99.4% CPU, 459 MB network I/O in 2 minutes, and starved the host of SSH access.

### 9 Fixes Implemented

| # | Fix | File(s) Changed | Description |
|---|-----|-----------------|-------------|
| 1 | **Per-request rate limiting** | `_scraper_utils.py` | Added `time.sleep(delay)` after every iteration in all batch scraping loops (`scrape_linked_pages`, `scrape_hmda_fig_pages`, `scrape_fdic_risk_manual_sections`, `scrape_discovered_urls`) |
| 2 | **Double scrape bug removed** | `scraper_scheduler.py` | `_sync_docs_via_processor` was calling `agent.execute()` (which calls `process_url()` on every URL) then calling `process_url()` **again** per URL. Now calls `process_url()` once per URL directly from `plan()` output |
| 3 | **Startup scrape disabled by default** | `scraper_scheduler.py`, `config.py` | `SCRAPER_RUN_ON_STARTUP=false` (default). If disabled, scheduler sleeps until first scheduled cycle instead of firing immediately |
| 4 | **Staggered connector execution** | `manager.py` | Added `SCRAPER_CONNECTOR_DELAY=10` seconds sleep between each connector's `extract_all_rules()` call to prevent burst traffic |
| 5 | **Per-domain rate limiter** | `_scraper_utils.py` | New `_rate_limit_for_domain()` function tracks last-request-time per hostname, enforces minimum delay between hits to the same gov domain (occ.gov, fdic.gov, etc.) |
| 6 | **Docker CPU/memory limits** | `docker-compose.pipeline1.yml` | Added `mem_limit: 2g` and `cpus: 2` so the container can never starve the host |
| 7 | **Circuit breaker (CPU/memory backoff)** | `scraper_scheduler.py` | New `_system_healthy()` check before each scrape cycle. If CPU > 80% or memory > 85%, backs off 60s. Uses `psutil` if available, falls back to `/proc/meminfo` on Linux |
| 8 | **Reduced discovery caps** | `_scraper_utils.py` | Default `max_html_pages` reduced from 100 → 30, `max_pdfs` from 20 → 10 in `scrape_discovered_urls()` |
| 9 | **DocumentPipelineAgent rate limiting** | `document_pipeline_agent.py` | Added `time.sleep(request_delay)` between each URL in `execute()` loop |

### New Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCRAPER_RUN_ON_STARTUP` | `false` | Run full scrape immediately on container start |
| `SCRAPER_STARTUP_DELAY` | `300` | Seconds to wait before first scrape (if startup enabled) |
| `SCRAPER_REQUEST_DELAY` | `1.0` | Minimum seconds between HTTP requests per domain |
| `SCRAPER_CONNECTOR_DELAY` | `10` | Seconds to wait between connector executions |

All four are exposed in `docker-compose.pipeline1.yml` with safe defaults and configurable via `.env`.

### Config Changes (`src/utils/config.py`)

Added to `Settings` class:
```python
# Scraper
SCRAPER_RUN_ON_STARTUP: bool = Field(default=False)
SCRAPER_STARTUP_DELAY: int = Field(default=300)
SCRAPER_REQUEST_DELAY: float = Field(default=1.0)
SCRAPER_CONNECTOR_DELAY: int = Field(default=10)
```

### Docker Compose Changes (`docker-compose.pipeline1.yml`)

```yaml
pipeline1:
  environment:
    - SCRAPER_RUN_ON_STARTUP=${SCRAPER_RUN_ON_STARTUP:-false}
    - SCRAPER_STARTUP_DELAY=${SCRAPER_STARTUP_DELAY:-300}
    - SCRAPER_REQUEST_DELAY=${SCRAPER_REQUEST_DELAY:-1.0}
    - SCRAPER_CONNECTOR_DELAY=${SCRAPER_CONNECTOR_DELAY:-10}
  mem_limit: 2g
  cpus: 2
```

### Expected Impact

| Metric | Before Fix | After Fix |
|--------|-----------|-----------|
| CPU on startup | 99%+ within 60s | No scrape on startup (default) |
| Network burst | 459 MB in 2 min | Rate-limited: ~1 req/sec per domain |
| Time to SSH hang | ~5 minutes | Never (container capped at 2 CPU / 2 GB) |
| URLs fetched per cycle | ~954 (477 × 2 double scrape) | ~477 (single pass, rate-limited) |
| Scrape cycle duration | N/A (crashed) | ~15–20 min (staggered, rate-limited) |

---

## File Inventory

| File | Role |
|------|------|
| `src/connectors/regulatory/usa/occ_bsa.py` | OCC BSA/AML connector |
| `src/connectors/regulatory/usa/fdic.py` | FDIC connector |
| `src/connectors/regulatory/usa/cfpb.py` | CFPB + Reg E connector |
| `src/connectors/regulatory/usa/fincen_sar.py` | FinCEN SAR connector |
| `src/connectors/regulatory/usa/ofac_sdn.py` | OFAC SDN connector |
| `src/connectors/regulatory/usa/_scraper_utils.py` | Shared scraping utilities + per-domain rate limiter |
| `src/connectors/regulatory/usa/__init__.py` | Package init |
| `src/connectors/manager.py` | Plugin manager + staggered connector execution |
| `src/services/pdf_pipeline.py` | PDF extraction + Weaviate storage pipeline |
| `src/services/weaviate_client.py` | Weaviate client with ComplianceDocument collection |
| `src/services/scraper_scheduler.py` | 24h scrape cycle + Weaviate sync + circuit breaker |
| `src/services/document_pipeline_agent.py` | Document pipeline agent + rate-limited URL processing |
| `src/utils/config.py` | Pydantic settings incl. scraper tuning env vars |
| `config/connectors/usa.yaml` | Connector registry config |
| `docker-compose.pipeline1.yml` | Pipeline 1 Docker Compose + CPU/mem limits |
| `tests/unit_test/test_scrapers.py` | 37 unit tests |
| `data/pdfs/` | Local PDF storage (gitignored) |
