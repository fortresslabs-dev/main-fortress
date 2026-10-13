# REQ-015: Interpretive Guidance — Scraping Letters, FAQs, and Informal Guidance

## Overview
**Priority:** Medium-High
**Type:** Enhancement — Pipeline 1 Scraper Enhancement + New Scrape Targets
**Source:** Competitor gap — Thomson Reuters Regulatory Intelligence
**Pipeline Impact:** Pipeline 1 first (scraper enhancement + sync), then Pipeline 2 benefits automatically
**Implementation Order:** All work is in Pipeline 1 — enhance existing connector scrape
targets and add `doc_type` field. Once richer interpretive documents flow through the
existing sync to Pipeline 2, the MCP compliance knowledge server and Generation agent
benefit automatically with no Pipeline 2 code changes needed.
**Status:** Backlog

## Business Context
Current connectors scrape regulation pages and handbooks but miss the layer below: OCC
interpretive letters, CFPB supervisory FAQs, FDIC FIL Q&A attachments, FinCEN administrative
rulings. These answer the "how do we apply this rule in practice?" question. Thomson Reuters
curates and indexes all of it. The practical consequence for Fortress: tests generated from
regulation text alone may miss implementation nuances that only appear in interpretive guidance.
When an examiner cites a violation, the legal basis is often an interpretive letter, not
the regulation text. The scraper infrastructure already exists — this is an expansion of
scrape targets, not a new architecture.

## Requirements

### Functional Requirements
1. Enhance `OccBsaConnector` — add interpretive letter scraping:
   - OCC Interpretive Letters index page
   - OCC No-Objection Letters index page
   - Filter to BSA/AML, wire transfer, CDD relevant letters
   - Store as `scraped_documents` with `category: "Interpretive Letter"` and `doc_type: "interpretive"`
2. Enhance `CfpbConnector` — add supervisory FAQ scraping:
   - CFPB Supervision & Examination manual pages with FAQ sections
   - CFPB HMDA FAQ pages (separate from the FIG already scraped)
   - Store as `category: "Supervisory FAQ"`, `doc_type: "faq"`
3. Enhance `FdicConnector` — add Q&A content from FIL attachments:
   - Each FIL scrape should attempt to follow PDF links to Q&A attachments
   - Store Q&A PDFs as separate `scraped_documents` entries
4. Enhance `FincenSarConnector` — add administrative rulings:
   - Already partially present (`administrative_rulings` URL in connector)
   - Ensure full listing page is scraped with pagination
   - Add FinCEN guidance documents listing page
5. Add new `doc_type` field to `scraped_documents` schema:
   - Values: `regulation`, `handbook`, `guidance`, `interpretive`, `faq`, `administrative_ruling`,
     `enforcement_order`, `fil`, `bulletin`
6. Tag interpretive guidance documents with `is_interpretive: true` in metadata
7. LLM summarization pass: after scraping interpretive documents, generate a one-paragraph
   summary of the obligation or clarification the document provides (use Bedrock/Anthropic client)

### Pipeline 1 Changes
- Update `_scrape_live()` methods in `OccBsaConnector`, `CfpbConnector`, `FdicConnector`,
  `FincenSarConnector` to include new scrape targets
- Add `doc_type` field to all `scraped_documents` dict outputs
- Add interpretive document LLM summarization step to `ScraperScheduler` post-scrape processing
- Interpretive documents participate in full sync cycle to Pipeline 2

### Pipeline 2 Changes
- None required — interpretive documents flow through existing sync mechanism
- MCP compliance knowledge server benefits automatically from richer scraped document corpus

## Acceptance Criteria
- [ ] `OccBsaConnector._scrape_live()` successfully fetches at least 5 OCC interpretive letters
      related to BSA/AML or wire transfers
- [ ] `CfpbConnector._scrape_live()` fetches CFPB HMDA FAQs in addition to existing content
- [ ] All new documents have `doc_type` field populated
- [ ] `is_interpretive: true` documents are flagged in sync payload metadata
- [ ] LLM summarization generates a plain-English summary for each interpretive document
- [ ] No regression in existing scrape targets — existing test suite passes

## Technical Notes
- OCC interpretive letters are paginated HTML tables — use existing `scrape_linked_pages`
  utility pattern from `_scraper_utils.py`
- CFPB FAQs are often embedded within regulation pages rather than standalone pages;
  look for `<section class="faq">` or equivalent selectors in BeautifulSoup parsing
- LLM summarization: run in batch after scrape completes, not inline — add to
  `ScraperScheduler._run_scrape_cycle()` as a post-processing step
- Rate limiting: interpretive letter indexes can have 100+ pages; apply existing retry
  and timeout logic from `make_session()` and respect 2-second inter-request delay
