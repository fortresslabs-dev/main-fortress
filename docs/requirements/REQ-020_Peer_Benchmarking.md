# REQ-020: Peer Benchmarking

## Overview
**Priority:** Low-Medium
**Type:** New Feature — New Data Service + Dashboard Component
**Source:** Competitor gap — Compliance.ai / Corlytics
**Pipeline Impact:** Pipeline 1 (anonymized aggregate data) + Pipeline 2 (benchmarking display)
**Status:** Backlog

## Business Context
Corlytics provides anonymized benchmarking: "Banks of your asset size typically have controls
mapped to 65% of BSA obligations; you're at 40%." Regional banks want to know whether their
compliance coverage is adequate relative to peers. This is particularly useful in board-level
reporting — a board risk committee wants context for what a "good" coverage score looks like.
The data for this comes from two sources: (1) public FFIEC/FDIC aggregate data on examination
findings and (2) anonymized aggregate coverage scores across all Fortress client banks
(opt-in, no PII).

## Requirements

### Functional Requirements

#### Public Benchmark Data
1. Add `PeerBenchmarkConnector` under `src/connectors/regulatory/usa/peer_benchmarks.py`:
   - FFIEC Uniform Bank Performance Report (UBPR) public aggregate data
   - FDIC BankFind public statistics (examination frequency by asset tier, finding rates)
   - OCC Semiannual Risk Perspective (published 2x/year) — aggregate examination finding trends
   - Store as benchmark records: `asset_tier`, `regulation`, `avg_coverage_percent`,
     `median_coverage_percent`, `top_quartile_percent`, `data_period`

#### Anonymized Network Benchmarks (Opt-In)
2. Add opt-in aggregate reporting to Pipeline 1:
   - Banks that opt in contribute anonymized coverage scores (regulation + coverage %) to
     Fortress Cloud — no bank name, no customer data, no transaction data
   - Pipeline 1 aggregates contributions into peer group statistics by asset tier
   - Participation flag: `PEER_BENCHMARK_OPT_IN=true` env var in Pipeline 2 config
   - What is sent: `{"asset_tier": "1B-10B", "regulation_id": "OCC-BSA-001", "coverage_pct": 74}`
   - What is never sent: bank name, customer ID, transaction data, policy documents

#### Benchmark Data Model
3. Add `PeerBenchmark` to `src/models/database.py`:
   - `peer_benchmarks` table: id, regulation_id, asset_tier, source (public/network),
     avg_coverage_pct, median_coverage_pct, top_quartile_pct, bottom_quartile_pct,
     sample_size (nullable for public), data_period, updated_at

#### Benchmarking API
4. New API endpoints:
   - `GET /api/v1/benchmarks` — all peer benchmark data for the bank's asset tier
   - `GET /api/v1/benchmarks/comparison` — bank's coverage scores vs peer benchmarks,
     with delta (above/below peer median) per regulation

#### Dashboard Integration
5. Add benchmark overlay to Panel 5 (coverage bar chart) in REQ-019 dashboard:
   - Peer median line overlaid on coverage bars
   - Below-peer-median bars highlighted in amber
   - Tooltip: "Peer median: 68%, You: 52% — 16 points below peer"

### Pipeline 1 Changes
- Register `PeerBenchmarkConnector` — scrape public data quarterly
- Receive and aggregate opt-in coverage submissions from Pipeline 2 clients
- Expose anonymized aggregate benchmarks via `/api/v1/sync/benchmarks` endpoint
- Store aggregated network benchmarks in Pipeline 1 DB

### Pipeline 2 Changes
- Sync peer benchmark records from Pipeline 1 via UpdateService
- Opt-in submission: if `PEER_BENCHMARK_OPT_IN=true`, send anonymized coverage scores
  to Pipeline 1 after each coverage score calculation (REQ-010)
- Add `GET /api/v1/benchmarks/comparison` endpoint
- Integrate into REQ-019 dashboard Panel 5

## Acceptance Criteria
- [ ] `PeerBenchmarkConnector` successfully retrieves FDIC BankFind public aggregate data
- [ ] `peer_benchmarks` table populated with at least public-source benchmark records
- [ ] `GET /api/v1/benchmarks/comparison` returns per-regulation delta vs peer median
- [ ] Opt-in submission sends only anonymized data — verified by inspecting outbound payload
      (no bank name, no customer ID, no transaction data)
- [ ] `PEER_BENCHMARK_OPT_IN=false` (default) prevents any data being sent to Pipeline 1
- [ ] Dashboard Panel 5 shows peer median overlay when benchmark data is available

## Technical Notes
- Default opt-in status is FALSE — banks must explicitly set `PEER_BENCHMARK_OPT_IN=true`
  to participate in network benchmarking
- Asset tiers for benchmarking: <$1B, $1B–$10B, $10B–$50B, $50B–$100B
  (mirrors FDIC/OCC supervisory categories)
- UBPR data is published by FFIEC as downloadable CSV/XML — use direct download rather
  than HTML scraping for reliability
- Network benchmark sample size: only publish aggregate statistics when sample size >= 5
  to prevent reverse-engineering of individual bank data from the aggregate
- This requirement depends on REQ-010 (coverage scores) being implemented first —
  there is nothing to benchmark without coverage scores
