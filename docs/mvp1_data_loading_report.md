# MVP1 Data Loading Report — Weaviate + Compliance Rules

**Date:** 2026-04-06  
**Branch:** `testmind_banking_v4`

## Summary

All 5 USA regulatory connectors scraped live and loaded into Weaviate (with AWS Bedrock Titan embedding) and PostgreSQL.

### Weaviate Collection Counts

| Collection | Objects | Source |
|-----------|---------|--------|
| **BankingWorkflow** | 7 | Seed data (UPI, Wire Transfer, OFAC, SAR, NEFT, RTGS, Section 1071) |
| **ComplianceRule** | 82 | 13 regulations + 31 requirements + 18 KPIs + 30 test scenarios (deduped) |
| **ComplianceDocument** | 648 | Live-scraped HTML sections + PDF chunks from all 5 regulators |
| **CodebaseChunk** | 0 | Not populated yet |

### Live Scrape Results by Regulator

| Regulator | Structured Rules | Scraped Documents | PDFs Downloaded |
|-----------|-----------------|-------------------|-----------------|
| OCC BSA | 24 (3 regs, 8 reqs, 5 KPIs, 8 scenarios) | 538 (including 515 PDF chunks) | 2 (bank-supervision-process.pdf, large-bank-supervision.pdf) |
| FDIC | 12 (3 regs, 4 reqs, 2 KPIs, 3 scenarios) | 23 HTML sections + 20 FIL entries | 0 (risk-management-manual.pdf → 404) |
| CFPB | 26 (4 regs, 9 reqs, 5 KPIs, 8 scenarios) | 79 HTML sections | 0 (2024-Guide.pdf → 404) |
| FinCEN SAR | 17 (2 regs, 6 reqs, 3 KPIs, 6 scenarios) | 11 HTML sections | 0 (sarreview_issue21.pdf → 404) |
| OFAC SDN | 13 (1 reg, 4 reqs, 3 KPIs, 5 scenarios) + 18,698 SDN entries | 1 HTML section | N/A (CSV download) |

## Architecture

### Vectorizer: text2vec-aws (Bedrock Titan Embed v1)
- **Model:** `amazon.titan-embed-text-v1` (1536 dimensions)
- **Module:** Enabled via `ENABLE_MODULES: "text2vec-aws"` in docker-compose
- **Embedding:** Server-side — Weaviate calls Bedrock automatically on insert and query
- **Cost:** ~$0.0001 per 1K tokens (Bedrock on-demand pricing)
- `near_text` queries work out of the box — no client-side embedding needed

### Deduplication: Deterministic UUIDs
- UUID5 generated from `collection + key fields` (e.g., `ComplianceRule + rule_id + regulator + geography`)
- Same data produces same UUID → idempotent re-runs (upsert semantics)
- Running `seed_weaviate.py` multiple times won't create duplicates

### Chunking Strategy
| Content Type | Strategy | Chunk Size | Metadata |
|-------------|----------|------------|----------|
| **HTML pages** | Section-based (heading → next heading) | Variable (truncated at 10KB) | regulator, category, source_url, content_hash, scraped_at |
| **PDF documents** | `unstructured` `chunk_by_title` | max 2000 chars, no overlap | doc_id, regulator, title, section_title, page_number, chunk_index |
| **Structured rules** | Full rule text as single document | Variable (truncated at 10KB) | rule_id, regulator, category, rule_type, geography |

### Dual Write: Weaviate + PostgreSQL
- PostgreSQL (`compliance_rules` table): Primary source of truth with SHA-256 hash-based change detection, versioning, and soft-delete tombstones
- Weaviate: Vector search index for semantic RAG queries
- Scheduler writes to both on every 24h scrape cycle

## Known Limitations

### 1. Tables — Structure Lost
HTML tables are extracted as flat text by BeautifulSoup. Table structure (rows/columns/headers) is lost.
- **Impact:** Fee schedules, threshold tables, comparison matrices lose their tabular meaning
- **Affected:** FDIC FIL entries, OCC exam schedules, CFPB fee tables
- **Mitigation:** None currently — would need specialized table extraction (e.g., `pandas.read_html()` + structured storage)

### 2. Images — Not Extracted
Images, charts, diagrams, and infographics are completely ignored.
- BeautifulSoup (HTML): Only processes text content
- `unstructured` (PDF): In text-only mode, images/figures are skipped
- **Impact:** Flowcharts in OCC handbooks, organizational charts, risk heat maps not captured
- **Mitigation:** Would need OCR (Tesseract) or multimodal LLM for image-to-text extraction

### 3. JavaScript-Rendered Content — Not Supported
`requests` + `BeautifulSoup` only process static HTML. JS-rendered content is invisible.
- **Impact:** Some FDIC and CFPB pages use React/JS frameworks
- **Affected:** Interactive tools, dynamically loaded tables, SPA pages
- **Mitigation:** Would need Playwright/Selenium for JS rendering

### 4. PDF Download Failures (3 URLs return 404)
- `https://www.fdic.gov/system/files/2024-06/risk-management-manual.pdf` → 404
- `https://www.consumerfinance.gov/data-research/hmda/static/for-filers/2024-Guide.pdf` → 404
- `https://www.fincen.gov/sites/default/files/shared/sarreview_issue21.pdf` → 404

These PDFs may have been moved or updated by the agencies. URLs need periodic verification.

### 5. OFAC SDN Entries — Not in Weaviate
18,698 SDN entries are extracted from the CSV but are **not** loaded into Weaviate as ComplianceRules. They're entity names/IDs, not compliance rule text suitable for semantic search. They're available via the connector for programmatic lookups.

### 6. RSS Feeds — Mostly Dead
- FinCEN RSS (`fincen.gov/rss.xml`): Returns 0 items
- FDIC RSS: Returns 0 items  
- Only CFPB RSS (`consumerfinance.gov/about-us/blog/feed/`) works
- `feedparser` is installed but only used for supplementary update detection

### 7. No HTML Chunking / Overlap
HTML-scraped sections are stored as-is (up to 10KB). No sliding window or overlap chunking is applied.
- **Impact:** Long sections may exceed the embedding model's context window (Titan v1: 8192 tokens ≈ 32K chars; 10KB limit is safe)
- **Trade-off:** Simple and preserves section context, but large sections could benefit from further splitting

### 8. OpenAI Embedder (`embedder.py`) — Orphaned
`src/services/embedder.py` uses OpenAI `text-embedding-3-large` (3072 dims) but is never called by any pipeline. The system now uses Weaviate's server-side `text2vec-aws` module (Bedrock Titan, 1536 dims).

## Files Modified

| File | Change |
|------|--------|
| `docker-compose.pipeline1.yml` | Added `text2vec-aws` module, gRPC port 50051, AWS env vars |
| `src/services/weaviate_client.py` | Added vectorizer config, deterministic UUIDs, `delete_all_collections()`, upsert logic |
| `scripts/seed_weaviate.py` | Added ComplianceDocument loading, fixed regulator inference, added regulation/KPI/scenario text extraction |
| `src/services/scraper_scheduler.py` | Fixed `scraped_documents` separation, added regulator inference, expanded text field extraction |

## Running

```bash
# Seed/load all data (scrapes live, takes ~60s)
WEAVIATE_URL=http://localhost:8080 python -m scripts.seed_weaviate

# Check collection counts
WEAVIATE_URL=http://localhost:8080 python -m scripts.seed_weaviate --check
```
