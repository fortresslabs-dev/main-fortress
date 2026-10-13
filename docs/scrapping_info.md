# Scraping Info — USA Regulatory Compliance Sources

## Table of Contents

1. [Scraping Links by Connector](#1-scraping-links-by-connector)
2. [Example Scraped Data](#2-example-scraped-data)
3. [Storage Destinations (PostgreSQL vs Redis)](#3-storage-destinations)
4. [Chunking & Weaviate Embedding](#4-chunking--weaviate-embedding)
5. [Data Flow Diagram](#5-data-flow-diagram)
6. [Current Status & Gaps](#6-current-status--gaps)

---

## 1. Scraping Links by Connector

### OCC BSA/AML (`src/connectors/regulatory/usa/occ_bsa.py`)

| URL | Type | Category | What It Scrapes |
|-----|------|----------|-----------------|
| `https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html` | HTML | BSA/AML Guidance | Wire transfer rules, CTR thresholds, CDD requirements |
| `https://www.occ.gov/news-issuances/bulletins/index-bulletins.html` | HTML | OCC Bulletins | Recent regulatory bulletins and guidance |
| `https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/bsa-aml/pub-ch-bsa.pdf` | PDF | BSA/AML Handbook | Full Comptroller's Handbook (~100+ pages) |

### FDIC (`src/connectors/regulatory/usa/fdic.py`)

| URL | Type | Category | What It Scrapes |
|-----|------|----------|-----------------|
| `https://www.fdic.gov/consumer-compliance-examination-manual` | HTML | Compliance Manual | Consumer compliance exam guidance |
| `https://www.fdic.gov/news/financial-institution-letters` | HTML | FILs | Financial Institution Letters (~20 most recent, scraped individually) |
| `https://www.fdic.gov/capital-markets` | HTML | Capital Adequacy | Risk-based capital requirements |
| `https://www.fdic.gov/laws-and-regulations` | HTML | Laws & Regulations | Deposit insurance rules, legal framework |

### CFPB (`src/connectors/regulatory/usa/cfpb.py`)

| URL | Type | Category | What It Scrapes |
|-----|------|----------|-----------------|
| `https://www.consumerfinance.gov/rules-policy/final-rules/small-business-lending-under-the-equal-credit-opportunity-act-regulation-b/` | HTML | Section 1071 | Small business lending demographics (July 2026 deadline) |
| `https://www.consumerfinance.gov/data-research/hmda/` | HTML | HMDA | Home Mortgage Disclosure Act reporting requirements |
| `https://www.consumerfinance.gov/rules-policy/regulations/1005/` | HTML | Regulation E | Electronic Fund Transfer Act (12 CFR Part 1005) |
| `https://www.consumerfinance.gov/rules-policy/regulations/1002/` | HTML | Regulation B | Equal Credit Opportunity Act |
| `https://www.consumerfinance.gov/feed/` | RSS | Updates | Keyword-filtered: "1071", "hmda", "reg e", "lending", "compliance" |

### FinCEN SAR (`src/connectors/regulatory/usa/fincen_sar.py`)

| URL | Type | Category | What It Scrapes |
|-----|------|----------|-----------------|
| `https://www.fincen.gov/resources/filing-information` | HTML | SAR Filing | SAR filing triggers, thresholds, deadlines |
| `https://www.fincen.gov/resources/fincens-legal-authorities` | HTML | Legal Authorities | Statutes underlying SAR/BSA requirements |
| `https://www.fincen.gov/boi` | HTML | Beneficial Ownership | BOI program requirements |
| `https://www.fincen.gov/rss.xml` | RSS | Updates | Keyword-filtered: "advisory", "guidance", "sar", "bsa", "aml" |

### OFAC SDN (`src/connectors/regulatory/usa/ofac_sdn.py`)

| URL | Type | Category | What It Scrapes |
|-----|------|----------|-----------------|
| `https://www.treasury.gov/ofac/downloads/sdn.csv` | **CSV** | SDN List | ~18,700 sanctioned entities (daily, direct download) |
| `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML` | XML | Update Check | Detects when SDN list is refreshed |

---

## 2. Example Scraped Data

### 2.1 Regulation Record

Each connector returns regulations via `_build_regulations()`:

```json
{
    "regulation_id": "OCC-BSA-WIRE-001",
    "regulation_name": "Wire Transfer Dual Approval Requirement",
    "category": "BSA/AML",
    "subcategory": "Wire Transfers",
    "regulator": "OCC",
    "geography": "usa",
    "effective_date": "2023-01-01",
    "exam_frequency": "quarterly",
    "source_url": "https://www.occ.gov/publications-and-resources/.../index-bsa.html",
    "last_updated": "2026-04-05T10:23:45"
}
```

### 2.2 Requirement Record

Each connector returns requirements via `_build_requirements()`:

```json
{
    "requirement_id": "OCC-BSA-WIRE-001-R1",
    "regulation_id": "OCC-BSA-WIRE-001",
    "requirement_text": "Wire transfers exceeding $10,000 require dual approval from two separate authorized employees",
    "rule_type": "dual_approval_limit",
    "threshold_amount": 10000.00,
    "threshold_currency": "USD",
    "test_requirement": "Verify system prevents single-person approval for wire transfers >= $10,000",
    "parameters": {
        "threshold_type": "per_transaction",
        "approval_count_required": 2,
        "separation_of_duties": true
    }
}
```

More examples by regulator:

| Connector | Example Requirement | Rule Type | Threshold |
|-----------|-------------------|-----------|-----------|
| OCC BSA | CTR filing for cash > $10K | `ctr_threshold` | $10,000 |
| FinCEN | SAR filing for insider abuse (any amount) | `sar_trigger` | $0 |
| FinCEN | SAR filing for identified suspects | `sar_trigger` | $5,000 |
| CFPB | Collect race/sex/ethnicity for SBL | `required_field` | N/A |
| CFPB | Reg E error resolution within 10 business days | `error_resolution` | N/A |
| OFAC | Screen all wire beneficiaries against SDN | `screening` | N/A |
| FDIC | Deposit insurance disclosure at $250K | `disclosure` | $250,000 |

### 2.3 Scraped HTML Document

Returned from `scrape_html_page()` in `_scraper_utils.py`:

```json
{
    "source": "OCC",
    "category": "BSA/AML Guidance",
    "title": "Wire Transfer Authorization Procedures",
    "content": "Wire transfers exceeding $10,000 require dual approval...",
    "url": "https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html",
    "scraped_at": "2026-04-05T10:23:45",
    "content_hash": "a7f3c9d2e1b8f4c6a3d5e2f8b0c1a9d3e7f4b2c5a8d1e3f6a9b2c4d7e0f1a3"
}
```

### 2.4 OFAC SDN Entry

```json
{
    "sdn_id": "12345",
    "uid": "12345",
    "entity_type": "individual",
    "full_name": "DOE, John",
    "sdn_type": "individual",
    "programs": "SDGT; IRAN",
    "title": "",
    "remarks": "DOB 15 Jan 1970; POB Kabul, Afghanistan",
    "effective_date": null,
    "last_updated": "2026-04-05T10:23:45"
}
```

### 2.5 KPI Record

```json
{
    "kpi_id": "OCC-BSA-KPI-001",
    "regulation_id": "OCC-BSA-WIRE-001",
    "kpi_name": "Wire Transfer Dual Approval Rate",
    "description": "Percentage of wire transfers >$10K with dual approval",
    "target_value": "100%",
    "measurement_method": "Count of compliant wires / total wires > $10K"
}
```

### 2.6 Test Scenario Record

```json
{
    "scenario_id": "OCC-BSA-TEST-001",
    "regulation_id": "OCC-BSA-WIRE-001",
    "scenario_name": "Wire transfer $15K single approval blocked",
    "given": "A wire transfer of $15,000",
    "when": "Only one employee approves",
    "then": "System blocks the transfer and alerts compliance",
    "priority": "P0"
}
```

---

## 3. Storage Destinations

### 3.1 PostgreSQL — Primary Structured Storage

**Where:** Pipeline 1 (Fortress Cloud) via SQLAlchemy async + asyncpg

**Table: `compliance_rules`** — Main rule storage

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | Auto-generated primary key |
| `rule_id` | String(100) | Unique rule identifier, e.g. `OCC-BSA-WIRE-001` |
| `regulator` | String(50) | `occ`, `cfpb`, `fincen`, `ofac`, `fdic` |
| `category` | String(100) | `bsa_aml`, `section_1071`, `sanctions`, etc. |
| `geography` | String(10) | `usa` |
| `domain` | String(100) | `wire_transfer`, `loan`, `screening`, etc. |
| `rule_type` | String(50) | `dual_approval`, `ctr_threshold`, `screening`, etc. |
| `rule_text` | Text | Full rule text |
| `threshold_amount` | Float | e.g. `10000.00` |
| `threshold_currency` | String(3) | `USD` |
| `field_name` | String(100) | For `required_field` rules |
| `effective_date` | DateTime | When rule became effective |
| `source_url` | Text | Source URL |
| `test_template` | Text | Test generation template |
| `is_active` | Boolean | Soft delete flag |
| `hash` | String(64) | SHA-256 for change detection |
| `version` | BigInteger | Global sync version |
| `created_at` | DateTime | Row creation time |
| `updated_at` | DateTime | Last update time |

**What gets stored in PostgreSQL:**
- ✅ All structured rules (regulations, requirements, KPIs, test scenarios)
- ✅ OFAC SDN entries (in `ofac_sdn_entries` table)
- ✅ Sync versions and deleted rule tombstones
- ❌ Raw scraped HTML content is NOT stored in PostgreSQL

**Supporting tables:**

| Table | Purpose |
|-------|---------|
| `sync_versions` | Global version counter (1 row, BigInteger) |
| `deleted_rules` | Tombstones for soft-deleted rules |
| `ofac_sdn_entries` | Full OFAC SDN list (~18,700 entries) |
| `sync_metadata` | Per-customer last sync version tracking |
| `local_sync_status` | Pipeline 2 sync log |

### 3.2 Redis — Caching Layer

**Where:** Both pipelines, via `aioredis`

| Cache Use | Key Pattern | TTL | What's Cached |
|-----------|-------------|-----|---------------|
| LLM Responses | LLM prompt hash | 86,400s (24h) | Claude/Bedrock API responses |
| Compliance Rule Lookups | `rule:{rule_id}` | Configurable | Frequently queried rules |
| Session Storage | `session:{exec_id}` | Session-scoped | Agent execution contexts |

**Config:** `REDIS_URL` from `.env`, `LLM_CACHE_TTL` defaults to 86400 seconds.

**What gets stored in Redis:**
- ✅ LLM response cache (avoids repeated API calls)
- ✅ Hot compliance rule lookups (fast reads for validators)
- ❌ No bulk scraped data — Redis is a cache, not primary storage

### 3.3 Weaviate — Vector Search

**Where:** Pipeline 1 (Fortress Cloud)

**Collection: `ComplianceRule`**

| Property | Data Type | Example |
|----------|-----------|---------|
| `rule_id` | TEXT | `OCC-BSA-WIRE-001` |
| `regulator` | TEXT | `OCC` |
| `category` | TEXT | `BSA/AML` |
| `rule_type` | TEXT | `dual_approval_limit` |
| `rule_text` | TEXT | Full rule text (up to 10K chars) — **this is embedded** |
| `source_url` | TEXT | Source URL |
| `geography` | TEXT | `usa` |

**Other Weaviate collections:**

| Collection | Purpose | Seeded By |
|------------|---------|-----------|
| `BankingWorkflow` | Banking workflow patterns (7 examples) | `seed_weaviate.py` |
| `CodebaseChunk` | Code snippets for agents | `seed_weaviate.py` |

**What gets stored in Weaviate:**
- ✅ All rule text (regulations + requirements) — vectorized for semantic search
- ✅ Scraped document content — pushed from scraper_scheduler after each cycle
- ✅ Banking workflow patterns (pre-seeded)

---

## 4. Chunking & Weaviate Embedding

### 4.1 HTML Chunking — Done ✅

**File:** `src/connectors/regulatory/usa/_scraper_utils.py` → `scrape_html_page()`

**Strategy:** Heading-based semantic chunking

```
HTML Page
  ├─ Find content area (div.article-body, main, article, etc.)
  ├─ Split by H1/H2/H3 headings into sections
  │   ├─ Chunk 1: heading text → title, following p/ul/ol → content
  │   ├─ Chunk 2: next heading → title, following content → content
  │   └─ ...
  └─ Fallback: if no headings found → grab entire article/main as one chunk (max 10K chars)
```

**Change detection:** SHA-256 hash on content → `content_hash` field. Same hash = no re-processing.

### 4.2 PDF Chunking — Needs `unstructured` Package

**File:** `src/connectors/regulatory/usa/_scraper_utils.py` → `scrape_pdf_url()`

**Strategy:** `unstructured` library with `chunk_by_title()`

```
PDF File
  ├─ Download to temp file
  ├─ partition(filename) → extract elements (titles, text, tables)
  ├─ chunk_by_title(elements, max_characters=2000) → group by title sections
  │   ├─ Chunk 1: "Chapter 3: CDD" → up to 2,000 chars
  │   ├─ Chunk 2: "Chapter 4: SAR" → up to 2,000 chars
  │   └─ ...
  └─ Returns: [{source, category, title, content, page_number, content_hash}]
```

**Current status:** `unstructured[pdf]` is NOT installed. PDF scraping gracefully returns `[]`. This is a gap for the OCC BSA/AML Handbook.

**To enable:**
```bash
pip install "unstructured[pdf]"
```

### 4.3 CSV Parsing (OFAC) — No Chunking Needed

OFAC SDN CSV is parsed row-by-row via `csv.reader()`. Each row = one SDN entity. No chunking needed — each entity is atomic.

### 4.4 Weaviate Embedding — Preparation Status

| Step | Status | Details |
|------|--------|---------|
| HTML chunking | ✅ Done | Heading-based, with article fallback |
| PDF chunking | ⚠️ Needs `unstructured` | Gracefully skipped if not installed |
| Weaviate collection schema | ✅ Done | `ComplianceRule` with 7 properties |
| Bulk insert function | ✅ Done | `bulk_insert_compliance_rules()` with dynamic batching |
| Scraper → Weaviate pipeline | ✅ Done | `_sync_rules_to_weaviate()` runs after each scrape cycle |
| Seed script | ✅ Done | `scripts/seed_weaviate.py` seeds from connectors |
| Embedding model | ✅ Configured | `text-embedding-3-large` (OpenAI) in `embedder.py` |
| Vectorizer in Weaviate | ⚠️ Uses default | Weaviate's built-in vectorizer (or needs `text2vec-openai` module config) |

### 4.5 What Gets Embedded

The `rule_text` property in Weaviate is the field that gets vectorized. When data flows from connectors to Weaviate:

```python
rule_text = (
    rule_data.get("rule_text")           # From regulations
    or rule_data.get("requirement_text")  # From requirements
    or rule_data.get("content")           # From scraped documents
    or ""
)
# Truncated to 10,000 chars before insert
```

This means **all three data types** (static rules, requirements, and scraped documents) are embedded in the same collection for unified semantic search.

---

## 5. Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│  REGULATORY SOURCES (Government Websites)                     │
│                                                               │
│  OCC ─────── HTML (2 pages) + PDF (1 handbook)               │
│  FDIC ────── HTML (3 pages) + FILs (20 letters)              │
│  CFPB ────── HTML (4 pages) + RSS feed                       │
│  FinCEN ──── HTML (3 pages) + RSS feed                       │
│  OFAC ────── CSV (18,700 SDN entries)                        │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│  SCRAPER SCHEDULER (runs every 24 hours)                      │
│  src/services/scraper_scheduler.py                            │
│                                                               │
│  1. PluginManager loads 5 connectors                         │
│  2. Each connector.extract_all_rules() returns:              │
│     ├── regulations[]     (static, hardcoded)                │
│     ├── requirements[]    (static, hardcoded)                │
│     ├── kpis[]            (static, hardcoded)                │
│     ├── test_scenarios[]  (static, hardcoded)                │
│     └── scraped_documents[] (live HTTP scraping)             │
│                                                               │
│  3. SHA-256 hash for change detection                        │
│  4. Upsert to PostgreSQL                                     │
│  5. Push to Weaviate                                         │
└───────┬──────────────────────────┬───────────────────────────┘
        │                          │
        ▼                          ▼
┌───────────────────┐   ┌─────────────────────────────────────┐
│  PostgreSQL        │   │  Weaviate (Vector Search)            │
│                    │   │                                      │
│  compliance_rules  │   │  ComplianceRule collection           │
│  ├── rule_id       │   │  ├── rule_id                        │
│  ├── regulator     │   │  ├── regulator                      │
│  ├── rule_text     │   │  ├── rule_text  ──→ [EMBEDDED]      │
│  ├── hash (SHA256) │   │  ├── category                       │
│  ├── version       │   │  └── geography                      │
│  └── is_active     │   │                                      │
│                    │   │  BankingWorkflow collection          │
│  ofac_sdn_entries  │   │  CodebaseChunk collection            │
│  sync_versions     │   └─────────────────────────────────────┘
│  deleted_rules     │
│  sync_metadata     │           ┌─────────────────────┐
└───────┬────────────┘           │  Redis (Cache)       │
        │                        │                      │
        │  Sync API              │  LLM response cache  │
        ▼                        │  TTL: 86400s (24h)   │
┌────────────────────┐           │  Rule lookup cache   │
│  Pipeline 2         │           └─────────────────────┘
│  (Customer VPC)     │
│                     │
│  Pulls incremental  │
│  diffs via          │
│  /api/v1/sync/      │
└─────────────────────┘
```

---

## 6. Current Status & Gaps

### What's Working ✅

| Item | Status | Details |
|------|--------|---------|
| 5 connectors load via PluginManager | ✅ | OCC, FDIC, CFPB, FinCEN, OFAC |
| HTML scraping (all gov sites) | ✅ | 70 documents scraped across all connectors |
| Heading-based chunking | ✅ | H1/H2/H3 boundaries + article fallback |
| Article fallback for flat pages | ✅ | Gov Drupal sites without heading structure |
| OFAC SDN CSV download/parse | ✅ | 18,700 entities in ~15 seconds |
| SHA-256 change detection | ✅ | Content hashing on every scraped doc |
| Weaviate ComplianceRule schema | ✅ | 7-property collection defined |
| `bulk_insert_compliance_rules()` | ✅ | Dynamic batch insert function |
| Scraper → Weaviate sync pipeline | ✅ | `_sync_rules_to_weaviate()` in scheduler |
| Seed script for Weaviate | ✅ | `scripts/seed_weaviate.py` updated |
| 29 unit tests passing | ✅ | Mocked HTTP, all connectors + utils tested |

### Gaps / Needs Attention ⚠️

| Item | Status | What's Needed |
|------|--------|---------------|
| PDF chunking | ⚠️ Not active | Install `unstructured[pdf]` (~1 GB) |
| Weaviate vectorizer config | ⚠️ Uses default | May need `text2vec-openai` module for production embeddings |
| RSS feed scraping | ⚠️ Partial | CFPB and FinCEN RSS defined but `check_rss_feed()` only used in `check_updates()`, not in `_scrape_live()` |
| PostgreSQL upsert logic | ⚠️ Needs DB | Scheduler expects running PostgreSQL; tested only via connector unit tests |
| FDIC FIL individual page scraping | ⚠️ Layout-dependent | Depends on `div.news-content` selector matching FDIC site |
| Scraper error handling | ✅ Graceful | All scrapers catch exceptions and return `[]` on failure |

### Live Scraping Results (April 2026)

| Connector | Regulations | Requirements | KPIs | Tests | Scraped Docs | Time |
|-----------|------------|-------------|------|-------|-------------|------|
| OCC BSA | 3 | 8 | 5 | 8 | 12 | 0.9s |
| FDIC | 3 | 4 | 2 | 3 | 23 | 4.9s |
| CFPB | 4 | 9 | 5 | 8 | 26 | 0.4s |
| FinCEN | 2 | 6 | 3 | 6 | 9 | 0.8s |
| OFAC | 1 | 4 | 3 | 5 | 18,698 SDN | 15.0s |
| **Total** | **13** | **31** | **18** | **30** | **18,768** | **22s** |
