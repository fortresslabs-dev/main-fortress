# Fortress Labs — Business Logic, Use Cases & Flows

*Reference document for competitive comparison. Reflects current dashboard, API, and validator implementation.*

---

## 1. Product Positioning

**Fortress Labs** is an AI-native banking compliance automation platform for **regional & community US banks (SMB tier)**. It ingests regulatory rules from 22 federal + state regulators, translates them into machine-executable obligations, and runs them as validators against transactions — with a two-pipeline SaaS + BYOC (bring-your-own-cloud) split so **customer data never leaves the bank's AWS account**.

**Primary buyers:** CCO (Chief Compliance Officer), CRO (Chief Risk Officer), CIO (technology governance).
**Anchor differentiators vs incumbents (NICE Actimize, Verafin, ComplyAdvantage, Hummingbird):**

- Dual-pipeline architecture — Pipeline 1 (Fortress cloud) scrapes regulators; Pipeline 2 (bank AWS account) runs validators. No customer PII in Fortress cloud.
- Vector + BM25 hybrid rule retrieval (pgvector HNSW + tsvector + GIN trgm).
- Multi-persona readiness scorecard (CCO/CRO/CIO lenses on the same underlying data).
- Test-code auto-generation from JIRA stories via a 6-agent Bedrock pipeline.
- Every rule carries a CFR citation + authority type (LAW/STATUTE/GUIDANCE/INTERNAL_CONTROL) for defensible audit trails.

---

## 2. Actor & Role Model

| Role         | Scope                                                          | Key Screens                                             |
|--------------|-----------------------------------------------------------------|---------------------------------------------------------|
| **CCO**      | Regulatory posture, examiner readiness, sign-off on changes    | Readiness (CCO lens), Rules, Changes, Deadlines, Reports |
| **CRO**      | Enterprise & residual risk, KRIs, appetite variance            | Readiness (CRO lens), Risk Matrix, Enforcement, Peer benchmarks |
| **CIO**      | Engine integrity, vector staleness, sync lag, TPRM             | Readiness (CIO lens), Vector Intelligence, Architecture data-flow |
| **Analyst**  | OFAC case triage, wire/loan disposition, obligation mapping    | Screening Cases, Investigation, Rules Performance       |
| **Admin**    | User provisioning, API key rotation, connector config          | Admin console, credential rotation flows                |
| **Examiner** | (Read-only, packet handover) — receives evidence bundle        | Reports (examiner evidence package)                     |

Auth is Cognito JWT + role-based permission gates. Sync between P1 and P2 uses HMAC-signed API keys (`X-Api-Key`, `X-Customer-Id`).

---

## 3. Dashboard Feature Inventory (13 pages)

### 3.1 Executive / Compliance Hub

| Page | Route | What It Shows | Backing Endpoint(s) |
|------|-------|---------------|---------------------|
| **Executive Today** | `/dashboard/executive` | KPI snapshot: active rules, tested count, open changes, next deadline, top-risk rules, SDN freshness, last agent run | `GET /overview` |
| **Readiness Scorecard** | `/readiness` | 0–100 persona-weighted score; 10 dimensions (obligation coverage, test coverage, control coverage, enforcement risk, change velocity, SDN freshness, policy gap, vendor risk, architecture integrity, model drift); 3×3 risk×coverage grid | `GET /readiness?lens=cco\|cro\|cio`, `GET /risk-matrix`, `GET /findings` |
| **Rules Table** | `/rules` | 1000+ rules, filter by regulator/jurisdiction/authority, CFR citation, enforcement-risk score, test coverage, N/A + override actions | `GET /rules`, `PATCH /rules/{id}/status` |
| **Regulatory Changes** | `/changes` | Detected mutations w/ before/after diff, sign-off gates, bulk sign-off ≤500 refs | `GET /changes`, `POST /changes/{ref}/signoff`, `POST /changes/signoff/bulk` |
| **Deadlines** | `/deadlines` | Filing calendars upstream from FDIC/OCC/CFPB/FinCEN; days_remaining priority sort | `GET /deadlines` |
| **Enforcement Feed** | `/enforcement` | 12-month rolling enforcement actions, severity scored, linked to triggering rules | `GET /enforcement` |
| **Obligations** | `/obligations` | Extracted obligation sentences per rule, test traceability | `GET /obligations` |

### 3.2 Validators & Screening

| Page | Route | What It Shows | Backing Endpoint(s) |
|------|-------|---------------|---------------------|
| **OFAC / SDN Health** | `/ofac` | Entry count, variant count, embedding freshness, staleness SLA (24h), live screening | `GET /ofac-health`, `POST /screening/search` |
| **Screening Cases** | `/dashboard/cases` | Open/closed cases, status/priority/full-text filters, decision audit trail | `GET /screening/cases` |
| **Case Investigation** | `/dashboard/cases/:id` | Matched SDN entry, programs/countries, trgm/fuzz/vector scores, analyst decision panel | `GET /screening/cases/{id}`, `POST /screening/cases/{id}/decision` |
| **Rules Performance** | `/dashboard/rules-perf` | Rule TP/FP rates, precision/recall, threshold tuning | `GET /screening/rules-perf`, `PATCH /screening/rules/{id}` |

### 3.3 Governance & Engineering

| Page | Route | What It Shows | Backing Endpoint(s) |
|------|-------|---------------|---------------------|
| **Vector Intelligence** | `/dashboard/vector` | Embedding stats, HNSW performance, staleness tracker, model drift, recall/precision vs BM25 baseline | `GET /screening/vector-intel` |
| **Workflows** | `/workflows` | JIRA story pipeline: agent phase, artifacts (test code, PR, quality score), timeline | `GET /workflows`, `GET /workflow/{id}` |
| **Reports** | `/reports` | 6 HTML templates: examiner evidence, OFAC attestation, board audit, findings/MRA, change log, coverage gap | `POST /exports`, `GET /exports/{id}/download` |

---

## 4. Core Business Logic (invariants that must always hold)

### 4.1 Rule Authority Gate (SVC: `authority_gate.py`, `validators.py`)
Every compliance decision must resolve to a rule with:
- `cfr_citation` (or state code reference) — **not null**
- `authority_type ∈ {LAW, STATUTE, GUIDANCE, INTERNAL_CONTROL}`
- `verified_source_url` (migration 047 introduces `citation_verified_source`)
- `is_active = true` at decision time

If any is missing, the validator returns `REVIEW` (not `PASS`) and logs an authority-gate breach event. This prevents synthetic/hallucinated rules from ever driving a production decision.

### 4.2 Two-Pipeline Data Boundary
- P1 (Fortress cloud) contains **only** public regulatory data — rules, obligations, deadlines, SDN list, embeddings.
- P2 (bank AWS) contains **only** customer transactions + local replica of rules.
- Sync direction: **P1 → P2 only** (HMAC-signed pull by P2's UpdateService). No customer data flows back.

### 4.3 Screening Decision Thresholds
- `final_score ≥ 0.92` → **HOLD** (auto-block, case opened, priority=critical)
- `0.85 ≤ final_score < 0.92` → **REVIEW** (queued to analyst)
- `final_score < 0.85` → **PASS** (audit log only)
- Thresholds are per-rule tunable via `PATCH /screening/rules/{id}` — every change writes a governance audit row.

### 4.4 Change Sign-Off SLA
- Detected regulatory change → `status = detected` → CCO must sign off within 30 days (configurable in bank policy).
- No sign-off in 30 days → dashboard readiness score drops (change-velocity dimension) and OS notification fires.
- Sign-off requires `role ∈ {CCO, CRO}` + comment; audit row records `user_id`, `timestamp`, `comment`, `change_ref`.

### 4.5 Readiness Score Composition
Score = weighted sum across 10 dimensions. Weights vary per persona:

| Dimension              | CCO weight | CRO weight | CIO weight |
|------------------------|-----------:|-----------:|-----------:|
| Obligation coverage    | 20         | 10         | 5          |
| Test coverage          | 20         | 15         | 10         |
| Control coverage       | 15         | 20         | 10         |
| Enforcement risk       | 10         | 15         | 5          |
| Change velocity        | 10         | 5          | 5          |
| SDN freshness          | 10         | 10         | 15         |
| Policy gap             | 10         | 15         | 5          |
| Vendor risk (TPRM)     | 5          | 5          | 15         |
| Architecture integrity | 0          | 0          | 20         |
| Model drift            | 0          | 5          | 10         |
| **Total**              | **100**    | **100**    | **100**    |

Any dimension < 50 shows as "at risk" (red); 50–75 amber; ≥75 green.

### 4.6 Validator Idempotency
Every validator call is idempotent on `(tx_id, tx_type)`. Repeated calls return the same decision unless the underlying rule set has changed (tracked via `rule_version_hash` embedded in the audit row).

---

## 5. End-to-End Use Cases (competitive comparison targets)

### UC-01 · Daily Regulatory Rule Refresh (fully automated)
**Actor:** System (P1 EventBridge cron 02:00 UTC)
**Trigger:** Scheduled
**Flow:**
1. P1 scrapes 22 regulator sites → raw HTML/PDF landed in S3.
2. Bedrock extracts obligations (sentence-level) → structured JSON.
3. Titan v2 embeds each rule (1024-dim vectors).
4. Aurora rules table updated; changed rules generate `RegulatoryChangeRecord`.
5. P2's UpdateService (any bank) polls `/api/sync/postgres` → pulls diff → refreshes local Aurora + rebuilds pgvector HNSW index.
6. Dashboard Overview/Rules/Changes panels refresh on next load.
**Success:** All 22 sources scraped in < 30 min; embedding staleness < 24h.
**Failure:** Scrape retry with exponential backoff; if 3 consecutive failures → CIO alert + dashboard "SDN freshness" dimension drops.

### UC-02 · Regulatory Change Detection & Sign-Off
**Actor:** CCO
**Trigger:** UC-01 detects rule mutation
**Flow:**
1. Bedrock diff detects: e.g. FinCEN beneficial ownership threshold 25% → 20%.
2. `RegulatoryChangeRecord` created with `change_delta` JSONB (before/after).
3. Changes page inbox shows new item; email/push notification to CCO.
4. CCO opens change → sees full diff, linked rule, linked obligations.
5. CCO clicks "Sign off" with comment → `POST /changes/{ref}/signoff`.
6. Rule promoted to `is_active=true`; readiness change-velocity dimension improves.
**Bulk path:** CCO selects up to 500 refs → `POST /changes/signoff/bulk` with single comment.

### UC-03 · Wire Transfer Compliance Check
**Actor:** Teller / core banking system
**Trigger:** Wire initiated
**Flow:**
1. Core system calls `POST /validate/wire-transfer` with tx payload.
2. `WireTransferValidator` runs checks in order:
   - BSA CTR: amount ≥ $10k → require CTR
   - SAR indicator: pattern + amount ≥ $5k → require SAR review
   - Dual approval: amount ≥ $10k → require second officer
   - OFAC screening of beneficiary (see UC-04)
3. Returns `{decision, violations[], recommendation, cfr_citations[]}`.
4. Audit row written with `latency_ms`, `screening_id`, `rule_ids_invoked`.
**Decision:** allow / block / review. Block responses include the CFR citation the teller can quote to the customer.

### UC-04 · OFAC SDN Screening & Case Management
**Actor:** Analyst
**Trigger:** UC-03 embedded call OR standalone screening request
**Flow:**
1. `POST /validate/ofac-screen` with party name(s).
2. `OFACScreener` runs hybrid search on local SDN replica:
   - pgvector HNSW k-NN (semantic)
   - tsvector BM25 (lexical)
   - GIN trgm (fuzzy)
3. Composite `final_score` computed → threshold decision (PASS/REVIEW/HOLD).
4. `ScreeningEvent` written; `ScreeningCase` auto-opened if REVIEW/HOLD.
5. Analyst opens Investigation page → sees matched SDN entry, programs, country, match scores broken down by strategy.
6. Analyst posts decision via `POST /screening/cases/{id}/decision` with reason.
7. Case closed with audit signature; if HOLD confirmed → SAR filing workflow triggered.

### UC-05 · CFPB §1071 Loan Application Validation
**Actor:** Loan originator
**Trigger:** Commercial loan app submission
**Flow:**
1. `POST /validate/loan-application` with app data.
2. `LoanApplicationValidator` checks demographic field presence (race, sex, ethnicity of principal owners), data quality, reportability under §1071.
3. Returns violations list; originator corrects missing fields.
4. Re-validation until PASS; final validated app persisted with `rule_version_hash`.

### UC-06 · Exam-Ready Evidence Chain (JIRA-driven)
**Actor:** JIRA (compliance requirement story)
**Trigger:** JIRA issue transitions to "Ready for Automation"
**Flow:**
1. Webhook fires `POST /webhooks/jira` → creates `WorkflowExecution`.
2. **Discovery agent** parses story → extracts requirement text + acceptance criteria.
3. **Understanding agent** runs hybrid search over compliance KB → retrieves candidate rules with citations.
4. **Framework agent** designs test scenarios (positive/negative/boundary).
5. **Generation agent** (Bedrock Claude Sonnet 4) writes pytest code.
6. **Validation agent** runs authority gate — every scenario must map to a real CFR citation.
7. **Execution agent** runs pytest in sandbox → opens GitHub PR → posts summary to JIRA.
8. Workflows page shows phase, artifacts, quality score. CRO checkpoint approval required before merge.

### UC-07 · Multi-Persona Readiness Assessment
**Actor:** CCO/CRO/CIO
**Trigger:** Login OR daily digest email link
**Flow:**
1. Readiness page loads; default lens matches user's role.
2. Parallel fetch: `/readiness?lens={role}`, `/risk-matrix`, `/cro` OR `/cio`, `/findings`.
3. Score rendered with 10-dim breakdown; at-risk dimensions highlighted.
4. User can switch lens mid-session → same underlying data re-weighted.
5. Click any dimension → drills to source data (e.g., "test coverage 62%" → jump to Rules filtered by `has_test=false`).

### UC-08 · Vector Model Governance & Threshold Tuning
**Actor:** Admin / CIO
**Trigger:** Rules Performance shows FP spike
**Flow:**
1. Vector Intelligence page: HNSW query latency, recall vs BM25 baseline, staleness hours.
2. Admin identifies OFAC rule with elevated false positives.
3. Adjusts threshold from 0.85 → 0.87 via `PATCH /screening/rules/{id}`.
4. Governance audit row written (`user_id`, `old_threshold`, `new_threshold`, `reason`).
5. Next screening uses new threshold. Rules Performance updates over next 24h.

### UC-09 · Peer Benchmark & Enforcement Trend Review
**Actor:** CRO
**Trigger:** Monthly board prep OR enforcement alert
**Flow:**
1. Enforcement Feed → filter by regulator + peer asset size.
2. Compare bank's control coverage vs peer median (data pulled from FFIEC UBPR + CRA files).
3. Generate Board Audit Committee report → HTML packet with charts.
4. Attach to board packet; audit trail retained via WORM export.

### UC-10 · Bank Onboarding & API Credential Rotation
**Actor:** Admin
**Trigger:** New bank contract OR 90-day rotation policy
**Flow:**
1. `scripts/register-bank.sh` provisions `ClientCredential` in Secrets Manager.
2. Bank's NAT EIP added to Fortress P1 WAF allowlist.
3. First-run: P2 calls `/api/sync/initial` with new credentials → seeds all active rules + embeddings.
4. Rotation: `PATCH /clients/{customer_id}/rotate` → new key + secret; 24h dual-key window; old key expires.
5. Audit trail retained; CCO notified.

### UC-11 · Point-in-Time Compliance Snapshot (Examiner Handover)
**Actor:** CCO
**Trigger:** Examiner request OR quarterly attestation
**Flow:**
1. Reports page → "Examiner Evidence Package" → `POST /api/v1/exports`.
2. System captures: all active rules with citations, test executions with results, sign-off log, screening events for period, OFAC health snapshot.
3. Versioned hash computed → immutable S3 object (WORM bucket, Object Lock).
4. `POST /exports/{id}/sign` → CCO digital signature.
5. Examiner receives download link + hash verification instructions.

---

## 6. Regulatory Coverage Matrix

### Federal (12 connectors)

| Regulator | Coverage Highlights | Connector |
|-----------|---------------------|-----------|
| FinCEN    | SAR thresholds, travel rule, beneficial ownership | `fincen_sar.py` |
| OCC       | BSA/AML, wire dual-approval, CTR, SAR              | `occ_bsa.py` |
| FDIC      | Capital ratios, call report, peer benchmarks       | `fdic.py` |
| CFPB      | Reg Z, ECOA (Reg B), §1071 loan data               | `cfpb.py` |
| OFAC      | SDN list w/ vector + tsvector search               | `ofac_sdn.py` |
| FFIEC     | Call Report H.8/H.15, Cybersecurity, BSA guidance  | `ffiec_*.py` |
| CRA       | Community Reinvestment Act lending patterns        | `cra.py` |
| CRE       | Commercial real estate concentration limits        | `cre_concentration.py` |
| Liquidity | LCR, NSFR                                          | `liquidity_risk.py` |
| IRR       | Interest rate risk measurement                     | `irr.py` |
| Op Risk   | Operational risk capital                           | `operational_risk.py` |
| CECL      | Expected credit loss, CCAR/DFAST                   | `cecl.py` |

### State (5 connectors)
CA-DFPI · NY-NYDFS · TX-DOB · FL-OFR · IL-IDFPR

**Totals:** 17 jurisdictions · 70+ regulations · every rule tagged with `jurisdiction_level`, `authority_type`, `enforcement_risk_score`.

---

## 7. Data Model (comparison-ready summary)

**Compliance:** `ComplianceRule`, `Obligation`, `RegulatoryChangeRecord`, `ComplianceDeadline`
**Validation Audit:** `TransactionValidation`, `OFACScreening`, `ScreeningCase`
**Workflow:** `WorkflowExecution`, `CheckpointApproval`
**Governance:** `BankPolicy`, `PolicyRegulationMapping`, `BankOfficer`, `Control`, `ControlTest`
**Vector Search:** `OfacSdnVector` (HNSW + tsvector + trgm indexes)
**System Health:** `LocalSyncStatus`, `ScrapeRun`

Every entity carries `created_at`, `updated_at`, `is_active`. Validation entities carry `rule_version_hash` for idempotency.

---

## 8. Comparison Cheat Sheet (fill in vs competitors)

| Capability | Fortress | Verafin | NICE Actimize | ComplyAdvantage | Hummingbird |
|------------|:--------:|:-------:|:-------------:|:---------------:|:-----------:|
| BYOC (data stays in bank cloud) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Machine-readable CFR citations on every decision | ✅ |  |  |  |  |
| Multi-persona readiness (CCO/CRO/CIO) | ✅ |  |  |  |  |
| Vector + BM25 hybrid rule retrieval | ✅ |  |  |  |  |
| Auto test-code generation from JIRA | ✅ |  |  |  |  |
| Rule change sign-off audit trail | ✅ |  |  |  |  |
| OFAC SDN local replica (no external API dependency) | ✅ |  |  |  |  |
| State regulator coverage (CA/NY/TX/FL/IL) | ✅ |  |  |  |  |
| WORM-backed examiner evidence exports | ✅ |  |  |  |  |
| Bedrock-based obligation extraction | ✅ |  |  |  |  |

---

## 9. File Reference Index

- Frontend routes → `fortress_server/dashboard/src/App.jsx`
- API surface → `fortress_server/src/api/{dashboard_api,validation_api,screening_api,sync_api,webhooks}.py`
- Business logic → `fortress_server/src/services/{validators,compliance_rule_resolver,authority_gate,update_service}.py`
- Data model → `fortress_server/src/models/database.py`
- Regulator connectors → `fortress_server/src/connectors/regulatory/usa/`
- Agents → `fortress_server/src/agents/{discovery,generation_bedrock,execution}.py`
- Migrations → `fortress_server/alembic/versions/046_compliance_rules_authority_type.py`, `047_citation_verified_source.py`
- Architecture → `fortress_server/ARCHITECTURE.md`, `fortress_server/CLAUDE.md`
