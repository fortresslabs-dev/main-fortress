# Pipeline 2 — CCO & Business-User Walkthrough

**Audience:** Chief Compliance Officer + business stakeholders validating the
bank-side product (CCO Dashboard, validators, 6-agent test-generation pipeline,
exports).
**Scope:** Everything P2 does inside the bank's VPC — runtime gates,
dashboard panels, naming conventions, audit/evidence flow.
**Mode constant:** `PIPELINE_MODE=pipeline2`.
**Companion doc:** [PIPELINE1_CCO_WALKTHROUGH.md](./PIPELINE1_CCO_WALKTHROUGH.md)
for the regulatory data plane that feeds P2.

This doc is organised around **business use cases**. Each use case has its
own sequence diagram + data-flow notes + dashboard panel mapping so a CCO can
walk one screen at a time.

---

## 1. Sixty-second summary

P2 is the bank-side deployment — one ECS Fargate cluster + Aurora + Cognito-
gated React dashboard per bank. It does **three jobs**:

1. **Runtime compliance gates** — wire, OFAC, loan validators sit in front of
   core banking calls. Customer transaction data never leaves the bank.
2. **CCO dashboard** — 13 panels covering rule inventory, regulatory-change
   review with sign-off, OFAC case management, examination readiness,
   workflow status, and audit-grade exports.
3. **6-agent JIRA pipeline** — story → discovery → understanding → framework →
   Bedrock test code → validation → GitHub PR + obligation-coverage writeback.

Everything the bank consumes — rules, OFAC list, change records — is **pulled
daily from P1** via the sync API (read-only, HMAC-authenticated). Everything
the bank produces — screenings, validations, cases, findings, exports —
**stays in the bank's Aurora**. Examination evidence lands in an S3 bucket
with Object-Lock COMPLIANCE mode (WORM, 7-year retention).

---

## 2. Architecture diagram

```mermaid
flowchart LR
  subgraph P1["P1 — Fortress Cloud (shared)"]
    P1API["/api/sync/*<br/>HMAC-authenticated"]
  end

  subgraph BANK["Bank deployment (P2) — inside customer VPC"]
    direction TB
    subgraph EDGE["Edge"]
      CF["CloudFront + WAF<br/>(blocklist IPSet + XFF rule)"]
      ALB["ALB"]
    end
    subgraph APP["ECS Fargate (2–10 tasks)"]
      MAIN["main_pipeline2.py FastAPI<br/>routers: webhooks, approvals,<br/>validate, screening, dashboard,<br/>exports, internal/sync"]
      UPD["UpdateService<br/>(daemon thread or one-shot)"]
      AGENTS["6-agent pipeline<br/>discovery → understanding →<br/>framework → generation →<br/>validation → execution"]
    end
    subgraph DATA["Data plane"]
      PG[("Aurora Postgres v2<br/>pgvector + tsvector<br/>~23 P2 tables")]
      REDIS[("ElastiCache Redis<br/>resolver Tier-2 cache")]
    end
    subgraph WORKER["Exports worker (separate ECS TD)"]
      EXP["exports/worker.py<br/>snapshot → canonical → render"]
    end
    subgraph STORE["Storage"]
      S3EXP[("S3 — exports bucket<br/>Object-Lock COMPLIANCE<br/>7-year retention")]
    end
    subgraph IDP["Identity"]
      COG["Cognito User Pool<br/>groups: Analyst, CCO,<br/>CRO, Admin"]
    end
    SEC["Secrets Manager<br/>/fortress/{bank_id}/*<br/>SSM Parameters"]
    BR["Bedrock<br/>Claude Sonnet 4 (codegen)<br/>Titan Embed v2 (vectors)"]
  end

  subgraph EXT["External systems"]
    CORE["Core banking system<br/>(wire, loan, KYC)"]
    JIRA["JIRA Cloud (MDP project)"]
    GH["GitHub repo"]
  end

  CORE -->|POST /validate/wire-transfer<br/>POST /validate/ofac-screen<br/>POST /validate/loan-application| ALB
  CF --> ALB
  JIRA -->|POST /webhooks/jira<br/>HMAC / Bearer auth| ALB
  ALB --> MAIN
  MAIN <--> PG
  MAIN <--> REDIS
  MAIN --> COG
  MAIN -->|InvokeModel<br/>(agents)| BR
  SEC -.-> MAIN
  SEC -.-> UPD
  UPD -->|HMAC sync calls<br/>(daily or EventBridge)| P1API
  P1API -->|rules, embeddings,<br/>SDN entries+variants,<br/>changes, deadlines| UPD
  UPD --> PG
  MAIN -->|enqueue artifact| EXP
  EXP <--> PG
  EXP -->|PUT-if-absent<br/>+ Object Lock| S3EXP
  AGENTS -->|create PR| GH
  AGENTS -->|comment| JIRA
```

**One-line CCO summary:** the bank's compliance brain (P2) reads from the
regulatory plane (P1), enforces gates on real banking calls, and gives the
CCO a single dashboard + a tamper-evident evidence drawer.

---

## 3. Naming conventions — the ID glossary

This is what every CCO/auditor first asks for. All formats are real (from
code or migrations).

| Identifier | Where it lives | Format | Real example | Notes |
|---|---|---|---|---|
| `bank_id` | `bank_profile.bank_id` | lower-case slug, no underscore | `testbank`, `firstnational` | Per memory `project_p2_test_bank_id` |
| `customer_id` | env / SSM, used as HMAC `X-Customer-Id` to P1 | same as bank_id | `firstnational` | One per bank |
| `rule_id` | synced from P1 `compliance_rules` | `{REGULATOR}-{CATEGORY}-{SUBCATEGORY?}-{SEQ}` | `OCC-BSA-WIRE-001`, `CFPB-1071-001`, `FINCEN-SAR-TRIG-001` | Same convention as P1 |
| `obligation_id` | synced from P1 `obligations` | `{REGULATOR}-{RULE}-{TYPE}-{SEQ}` | `FINCEN-SAR-TRIG-001`, `OCC-BSA-WIRE-001-R1` | `obligation_type` ∈ {shall, must, prohibited, required, may_not} |
| `change_ref` | `regulatory_change_records.change_ref` | `CHG-YYYY-MM-DD-NNN` (or `CR-…` for some sources) | `CHG-2026-06-30-001` | Stable across P1 ↔ P2 |
| `workflow_id` | `workflow_executions.id` | UUID v4 | `550e8400-e29b-41d4-a716-446655440000` | Generated on JIRA webhook |
| `jira_story_key` | `workflow_executions.jira_story_key` | `MDP-NNN` (MDP project, Idea issuetype) | `MDP-42`, `MDP-105` | Per memory `project_jira_mdp_idea_issuetype` |
| `policy_ref` | `bank_policies.policy_ref` | `POL-YYYY-CATEGORY-NNN` | `POL-2026-BSA-001` | Unique per `(policy_ref, client_id)` |
| `policy_id` | `bank_policies.id` | UUID v4 | — | Used as FK target |
| `screening_id` | `screening_events.screening_id` | VARCHAR(64) — caller-supplied idempotency key | UUID or `SCREENING-20260630-000001` | Caller chooses; SHA-256 of criteria gives 24h dedup |
| `criteria_hash` | `screening_events.criteria_hash` | SHA-256 hex (64 chars) | — | Dedup identical re-screens within 24h |
| `case_id` | `screening_cases.case_id` | `CASE-YYYY-MM-NNNNNN` | `CASE-2026-06-000152` | One open case per `(external_customer_id, sdn_number)` |
| `sdn_number` | from OFAC XML, synced | numeric UID from OFAC | `3526`, `11234` | — |
| `screening_rule_id` | `screening_rules.rule_id` | `OFAC-SCREEN-{TYPE}-NNN` | `OFAC-SCREEN-FUZZY-001`, `OFAC-SCREEN-OWN50-001`, `OFAC-SCREEN-FRESH-001`, `OFAC-SCREEN-CLEAN-001` | Seeded by migration 034 |
| `control_id` | `controls.control_id` | `CTRL-{DOMAIN}-NNN` | `CTRL-BSA-KYC-001` | One row per bank control |
| `test_id` | `control_tests.test_id` | `TEST-{control_id}-{framework}-NNN` | `TEST-CTRL-BSA-KYC-PYTEST-001` | Links to JIRA story + PR |
| `execution_id` | `test_executions.execution_id` | UUID v4 | — | One per pytest sandbox run |
| `finding_id` | `findings.finding_id` | `FIND-YYYY-MM-{BANK}-NNN` | `FIND-2026-06-TESTBANK-001` | OCC 5-Cs structure |
| `artifact_id` | `export_artifacts.artifact_id` | UUID v4 (also used in S3 key) | — | One per generated report |
| `version_hash` | `export_artifacts.version_hash` | first 7 hex of canonical SHA-256 | `4a1f93c` | Idempotency: same as_of + same source = same hash = same artifact |
| `validation_id` | `ofac_validations.validation_id` | UUID v4 | — | Annual OFAC independent test |
| `person_id` | `bank_officers.person_id` | UUID v4 | — | Officer roster |
| `vocab_version` | `vocab_versions.version_id` | `YYYY-MM-DD-{tag}` | `2026-06-01-alpha`, `q18-final` | Frozen enum vocabulary snapshot |

**Three rules of thumb to give the CCO:**
1. Anything ending in `_ref` or `_key` is a **string natural key** the human
   reads (e.g. `change_ref`, `policy_ref`, `jira_story_key`).
2. Anything ending in `_id` is **either a UUID** (machine key) **or a
   prefixed string** if the row also needs to be human-recognisable
   (control_id, case_id, finding_id, test_id, screening_rule_id).
3. `rule_id` and `obligation_id` are **synced from P1 verbatim** — P2 never
   mints new ones; it only references them.

---

## 4. Business use cases — sequence + data flow per scenario

Every use case below has the same shape: **trigger → actors → sequence
diagram → what got written → which dashboard panel it surfaces in**.

### UC-1: Daily sync — P2 pulls fresh rules + OFAC list from P1

**Who triggers it:** background daemon (`UpdateService`) or EventBridge.
**Frequency:** every `SYNC_INTERVAL_HOURS` (default 24) or on-demand via
`POST /internal/sync/trigger`.

```mermaid
sequenceDiagram
  autonumber
  participant EB as EventBridge / daemon timer
  participant UPD as UpdateService<br/>src/services/update_service.py
  participant P1 as Pipeline 1<br/>/api/sync/*
  participant PG as Aurora (P2)
  participant CW as CloudWatch

  EB->>UPD: tick / one-shot
  UPD->>UPD: acquire pg_advisory_lock<br/>(prevents N replicas from syncing in parallel)
  UPD->>P1: POST /api/license/verify<br/>(X-Customer-Id, X-Api-Key)
  P1-->>UPD: {status: ACTIVE}
  Note over UPD,P1: fail-closed unless LICENSE_FAIL_OPEN_OVERRIDE=true

  alt first boot (from_version=0)
    UPD->>P1: POST /api/sync/initial?page=1..N
    P1-->>UPD: paginated compliance_rules
    UPD->>PG: UPSERT compliance_rules<br/>ON CONFLICT (rule_id, regulator, geography)
    UPD->>PG: deactivate orphan rule_ids (reconcile)
  else incremental (from_version=X)
    UPD->>P1: POST /api/sync/postgres {from_version: X}
    P1-->>UPD: {added[], updated[], deleted[], current_version}
    UPD->>PG: transaction: apply diffs
  end

  UPD->>P1: POST /api/sync/embeddings
  P1-->>UPD: 1024-dim Titan vectors
  UPD->>PG: UPDATE compliance_rules SET embedding

  UPD->>P1: POST /api/sync/ofac-sdn (paginated)
  UPD->>P1: POST /api/sync/sdn-vectors (paginated, embedded_only)
  UPD->>P1: POST /api/sync/sdn-changes (keyset cursor)
  UPD->>PG: UPSERT ofac_sdn_entries, ofac_sdn_vectors
  UPD->>PG: cursor sync_metadata.last_sdn_change_consumed_*

  par ancillary syncs
    UPD->>P1: POST /api/sync/obligations
    UPD->>P1: POST /api/sync/deadlines
    UPD->>P1: POST /api/sync/change-records
    UPD->>P1: POST /api/sync/risk-scores
    UPD->>P1: POST /api/sync/enforcement-actions
    UPD->>P1: POST /api/sync/benchmarks
    UPD->>P1: POST /api/sync/ofac-list-version
  end

  UPD->>PG: ANALYZE compliance_rules<br/>(refresh HNSW planner stats)
  UPD->>PG: INSERT local_sync_status<br/>(rules_added, rules_updated, ..., sync_status)
  UPD->>P1: POST /api/sync/status<br/>(report telemetry back to P1)
  UPD->>CW: emit sync metric
```

**Tables written this cycle:** `compliance_rules`, `obligations`,
`compliance_deadlines`, `regulatory_change_records`, `enforcement_actions`,
`enforcement_risk_scores`, `peer_benchmarks`, `ofac_sdn_entries`,
`ofac_sdn_vectors`, `ofac_list_versions`, `sync_metadata`, `local_sync_status`.

**Idempotency:** every write is `ON CONFLICT … DO UPDATE` keyed on a natural
unique constraint, so re-running a failed cycle is safe.

**Backoff:** `_post_with_retry()` does exponential backoff (1s, 2s, 4s, 8s)
on network errors and 5xx/429. 401/403 → re-raise immediately (auth issue).

**Surfaces in dashboard:** `/health/sync` endpoint and the readiness panel's
"engine integrity" lens (CIO view).

---

### UC-2: CCO reviews a regulatory change & signs it off

**Who triggers it:** CCO opens the `Changes` panel, drills into a row,
records a disposition + signs off.

```mermaid
sequenceDiagram
  autonumber
  actor CCO as CCO (Cognito group)
  participant FE as Dashboard SPA<br/>dashboard/src/pages/Changes.jsx
  participant API as P2 FastAPI<br/>src/api/dashboard_api.py
  participant PG as Aurora
  participant JIRA as (optional) JIRA

  CCO->>FE: open /changes
  FE->>API: GET /api/v1/dashboard/changes?page=1
  API->>PG: SELECT change_ref, rule_id, regulator,<br/>change_summary, semantic_diff, impact_rating,<br/>change_type, status, created_at<br/>FROM regulatory_change_records<br/>WHERE is_active AND regulator<>'OFAC'<br/>ORDER BY created_at DESC LIMIT 20
  PG-->>API: rows
  API-->>FE: list (badges, counts)
  CCO->>FE: click row → drawer opens
  FE->>API: GET /api/v1/dashboard/changes/{change_ref}
  API->>PG: SELECT rec.*, cr.rule_text, cr.previous_content,<br/>cr.cfr_citation<br/>FROM regulatory_change_records rec<br/>LEFT JOIN compliance_rules cr<br/>  ON cr.rule_id=rec.rule_id<br/>WHERE rec.change_ref=:ref
  API->>PG: SELECT comment_type, author, status_from,<br/>status_to, created_at<br/>FROM change_record_comments c<br/>JOIN regulatory_change_records r ON r.id=c.change_record_id<br/>WHERE r.change_ref=:ref
  PG-->>API: rec + comments + change_delta JSONB
  API-->>FE: drawer shows added/removed sentences<br/>(from change_delta — frozen at detection time)
  Note over FE: change_delta is per-record (mig 037)<br/>so the drawer always shows the diff at detection time
  CCO->>FE: pick decision (acknowledged / requires_action / not_applicable)<br/>+ optional note
  FE->>API: POST /api/v1/dashboard/changes/{change_ref}/signoff<br/>{decision, note}<br/>Auth: Bearer JWT (require_approver)
  API->>PG: UPDATE regulatory_change_records<br/>SET status='signed_off', signed_off_at=NOW(),<br/>    reviewer_name=:email
  Note over PG: trigger prevent_signoff_overwrite()<br/>blocks any later UPDATE of signed_off_at
  API->>PG: INSERT change_record_comments<br/>(comment_type='sign_off', author, status_from, status_to)
  API->>PG: UPSERT change_record_dispositions<br/>(bank_id, change_ref, disposition, owner_person_id,<br/>vocab_version)
  alt impl link provided
    API->>JIRA: comment "Disposition acknowledged: <note>"
  end
  API-->>FE: {change_ref, decision, signed_by, timestamp}
  FE-->>CCO: toast "Signed off CHG-2026-06-30-001"
```

**Dashboard panels touched:** Panel 3 — `Changes` (list + drawer + signoff
modal). Also surfaces in: readiness scorecard ("changes signed off") and
exports (Report 5 — Regulatory change log).

**Tables written:** `regulatory_change_records` (status + signed_off_at),
`change_record_comments` (audit row), `change_record_dispositions` (per-bank
disposition with frozen `vocab_version`).

**What the CCO can prove with this:** every regulator-side change has a
named reviewer, a timestamped sign-off (immutable thanks to the Postgres
trigger), and an optional bank disposition tied to a vocab version that's
frozen at the moment of sign-off.

---

### UC-3: Wire transfer validation (runtime gate)

**Who triggers it:** core banking calls `POST /validate/wire-transfer`
before releasing a wire. Reachable only from inside the bank's VPC.

```mermaid
sequenceDiagram
  autonumber
  participant CORE as Core banking
  participant API as P2 validation_api<br/>src/api/validation_api.py
  participant W as WireTransferValidator<br/>src/services/validators.py
  participant RES as ComplianceRuleResolver<br/>(Postgres → Redis → pgvector → fallback)
  participant SCR as OFACScreener
  participant PG as Aurora
  participant CW as CloudWatch (Fortress/P2/Validation)

  CORE->>API: POST /validate/wire-transfer<br/>{amount, beneficiary_name, originator_info, currency, ...}
  API->>API: per-IP rate-limit<br/>(FORTRESS_P2_VALIDATE_RPM default 60/min)
  API->>W: validate(payload)
  W->>RES: resolve_domain('wire_transfer')
  RES-->>W: {ctr_threshold=10000, dual_approval_threshold=10000,<br/>travel_rule_threshold=3000} (source: postgres|redis|pgvector|fallback)
  W->>W: CTR check (amount ≥ $10K → Violation BSA)
  W->>W: Dual-approval check (amount ≥ $10K)
  W->>W: Travel Rule check (amount ≥ $3K → require originator info)
  W->>SCR: screen(beneficiary_name, country)
  SCR->>PG: lookup ofac_sdn_vectors / ofac_sdn_entries<br/>(see UC-4 for inner detail)
  SCR-->>W: ValidationResult(decision=PASS|REVIEW|HOLD,<br/>match_detail, screening_id, case_id?)
  W->>PG: INSERT transaction_validations<br/>(validation_type='wire_transfer', action, violations[],<br/>request_metadata={amount, ts — no PII}, latency_ms)
  W-->>API: ValidationResult
  API->>CW: emit Decisions{Bank, Endpoint, Decision} + LatencyMs
  API-->>CORE: {action: allow|block|review,<br/>decision: PASS|REVIEW|HOLD,<br/>violations: [...], final_score, latency_ms}
```

**Decision matrix the CCO will ask about:**

| Amount | OFAC result | Dual approval present | → Result |
|---|---|---|---|
| < $3K | clear | — | PASS, no flags |
| $3K – $10K | clear | — | PASS, Travel Rule check on originator info |
| ≥ $10K | clear | yes | PASS, CTR filing flagged |
| ≥ $10K | clear | no | REVIEW (dual approval missing) |
| any | fuzzy 0.85–0.92 | — | REVIEW + case opened |
| any | match ≥ 0.92 | — | HOLD + case opened (priority HIGH) |
| any | SDN list > 36h stale | — | HOLD (FRESH-001 staleness gate) |
| any | SDN list 24–36h stale | — | bump one level (PASS→REVIEW or REVIEW→HOLD) |

**Tables written:** `transaction_validations` (audit row, no PII), plus
whatever OFAC screen wrote (`screening_events` always, `screening_cases` on
REVIEW/HOLD).

**Dashboard panels touched:** none directly — validators are silent gates.
Their output rolls up into readiness (Panel 1) and into cases (Panels 10–11)
when OFAC fires.

---

### UC-4: OFAC SDN screen from the dashboard (real-time)

**Who triggers it:** an Analyst (or CCO) opens the SDN Search panel and
screens a party.

```mermaid
sequenceDiagram
  autonumber
  actor U as User (Analyst/CCO)
  participant FE as SdnSearch.jsx
  participant API as screening_api.py
  participant SCR as OFACScreener<br/>src/services/validators.py
  participant PG as Aurora (ofac_sdn_vectors)
  participant BR as Bedrock Titan v2

  U->>FE: enter name + country + DOB + customer_id
  FE->>API: POST /api/v1/dashboard/screening/search<br/>Bearer JWT (require_screening_auth)
  API->>API: require_screening_auth()<br/>resolve Cognito groups → role
  API->>SCR: screen(criteria)
  SCR->>SCR: normalise (NFD → uppercase → strip punct)
  SCR->>SCR: criteria_hash = SHA-256(canonical(criteria))
  SCR->>PG: lookup prior screening_events<br/>where criteria_hash=:h AND created_at > NOW() - INTERVAL '24h'
  alt cache hit (idempotent replay)
    PG-->>SCR: prior ScreeningEvent
    SCR-->>API: same result + X-Idempotent: true
  else first call within 24h
    SCR->>PG: read screening_rules<br/>(FUZZY-001, OWN50-001, FRESH-001, CLEAN-001 — weights + is_active)
    SCR->>SCR: staleness gate<br/>(SDN data age vs FRESH-001 threshold)
    alt vector mode
      SCR->>BR: embed party_name → vector(1024)
      SCR->>PG: HNSW cosine search on ofac_sdn_vectors<br/>WHERE is_active AND embedding IS NOT NULL<br/>ORDER BY embedding <=> :q LIMIT 10
    else legacy mode
      SCR->>PG: SequenceMatcher ratio vs ofac_sdn_entries.entity_name
    end
    PG-->>SCR: candidates
    SCR->>SCR: blend scores: fuzz + alias + vector → final_score
    SCR->>SCR: decide<br/>≥0.92 → HOLD<br/>0.85–0.92 → REVIEW<br/>else PASS
    alt decision in (REVIEW, HOLD)
      SCR->>PG: INSERT screening_cases<br/>(case_id='CASE-YYYY-MM-NNNNNN', status='open',<br/>priority=HIGH|MEDIUM, decision_at_open,<br/>first_event_id, last_event_id, matched_rule_id)
      Note over PG: UNIQUE (external_customer_id, sdn_number)<br/>WHERE status NOT IN ('cleared','closed')<br/>→ second hit on same pair re-uses the open case
    end
    SCR->>PG: INSERT screening_events<br/>(screening_id, criteria, criteria_hash, decision,<br/>final_score, engine_mode, engine_response,<br/>sdn_data_age_hours, actor_kind='dashboard',<br/>actor_user_id={Cognito sub})
    SCR-->>API: ValidationResult
  end
  API-->>FE: {decision, final_score, matches[], screening_id, case_id?, sdn_data_age_hours, staleness_bumped}
  FE-->>U: render decision badge + matches table
  U->>FE: (if matches) click "Pass" / "Review" / "Escalate"
  FE->>API: POST /api/v1/dashboard/screening/cases/{case_id}/decision
  API->>PG: UPDATE screening_cases<br/>SET status, reviewer_action, reviewer_notes,<br/>resolved_at, resolved_by_user_id
  Note over API: passing a HOLD case requires CCO role +<br/>≥50 char justification note
```

**Dashboard panels touched:**
- Panel 9 — **SDN Search** (this flow's start)
- Panel 10 — **Screening Cases** (list view of all open cases)
- Panel 11 — **Investigation** (drill-down + decision recording)
- Panel 12 — **Rules Performance** (FUZZY-001/OWN50-001/FRESH-001/CLEAN-001
  weights + alert volume by decision)
- Panel 13 — **Executive Today** (today's screenings, matches, HP cases)
- Panel 6 — **OFAC / SDN Engine Health** (freshness gauge, counts)
- Bonus — **Vector Intelligence** (Admin/CRO — embedding coverage, similarity
  distribution last 30d, engine mode A/B split)

**Tables written:** `screening_events` (always, immutable), `screening_cases`
(on REVIEW/HOLD), `screening_case_events` (join row linking new event to
existing case on re-hit).

**Auditor proof points:** every screen has a row in `screening_events` with
`actor_kind` + `actor_user_id` (who screened), `criteria` (what was
screened, PII stays P2-local), `engine_mode` (legacy vs vector for A/B
analysis), and `sdn_data_age_hours` (freshness at decision time).

---

### UC-5: Loan application validation (Section 1071 + ECOA + HMDA)

**Who triggers it:** loan origination system calls `POST /validate/loan-application`.

```mermaid
sequenceDiagram
  autonumber
  participant LO as Loan origination
  participant API as validation_api.py
  participant V as LoanApplicationValidator
  participant RES as ComplianceRuleResolver
  participant PG as Aurora

  LO->>API: POST /validate/loan-application<br/>{applicant_demographics, loan_amount,<br/>loan_term, business_revenue, applicant_id}
  API->>V: validate(payload)
  V->>RES: resolve_domain('loan')<br/>resolve_domain('section_1071')
  RES-->>V: {section_1071 obligations,<br/>ECOA disparate-impact threshold (80%),<br/>HMDA LAR required fields}
  V->>V: Section 1071 — demographics collected?<br/>firewall enforced?
  V->>V: HMDA — LAR completeness (if mortgage)
  V->>V: ECOA / Reg B — rolling fair-lending parity
  V->>PG: INSERT transaction_validations<br/>(validation_type='loan_application', action,<br/>violations[], latency_ms)
  V-->>API: ValidationResult
  API-->>LO: {action, decision, violations[]}
```

**Dashboard panels touched:** rolls up into Panel 1 (readiness) and Panel 2
(compliance rules — fair-lending obligations). No dedicated loan panel
exists yet (gap, flag to CCO).

---

### UC-6: JIRA story → 6-agent pipeline → GitHub PR

**Who triggers it:** developer writes a JIRA story in the MDP project
(issuetype = Idea). JIRA Cloud fires the webhook.

```mermaid
sequenceDiagram
  autonumber
  participant JIRA as JIRA Cloud (MDP project)
  participant WH as webhooks.py<br/>POST /webhooks/jira
  participant ORC as WorkflowOrchestrator<br/>workflow_engine.py
  participant A1 as 1. Discovery
  participant A2 as 2. Understanding
  participant A3 as 3. Framework
  participant A4 as 4. Generation<br/>(Bedrock Claude Sonnet 4)
  participant A5 as 5. Validation
  participant A6 as 6. Execution
  participant PG as Aurora
  participant BR as Bedrock
  participant GH as GitHub
  participant APR as POST /approvals<br/>(CCO/CRO gate)

  JIRA->>WH: POST /webhooks/jira (issue.created/updated)
  WH->>WH: authenticate (Bearer or HMAC SHA-256)
  WH->>PG: SELECT WorkflowExecution<br/>WHERE jira_story_id=:k ORDER BY started_at DESC
  alt prior completed run with PR URL
    WH-->>JIRA: return prior PR (no re-gen)
  else fresh
    WH->>PG: pg_advisory_lock(0x70327367)<br/>(cluster-wide serial — one Bedrock call at a time)
    WH->>PG: INSERT workflow_executions<br/>(workflow_id=UUID, jira_story_id, jira_story_key,<br/>status='pending')
    WH->>ORC: enqueue (BackgroundTasks)
  end

  ORC->>A1: discovery(story)
  A1->>A1: regex APIs, detect banking patterns<br/>(wire_transfer, ofac, sar, 1071, hmda, reg_e)
  A1-->>ORC: {discovered_apis, banking_patterns,<br/>compliance_requirements, dependencies}

  ORC->>A2: understanding(story, discovery)
  A2->>PG: resolve obligation_ids by domain<br/>(JOIN compliance_rules ↔ obligations)
  A2->>PG: ComplianceRuleResolver (Tier1→2→3→fallback)
  A2-->>ORC: {requirements, banking_rules,<br/>compliance_context, edge_cases,<br/>obligation_ids[]}

  ORC->>A3: framework(understanding)
  A3-->>ORC: {scenarios[Given/When/Then],<br/>scenario_count, coverage}

  Note over ORC,APR: (optional) pre_generation checkpoint —<br/>workflow_executions.status='waiting_approval' until CCO POST /approvals

  ORC->>A4: generation(scenarios, banking_rules)
  A4->>BR: InvokeModel<br/>us.anthropic.claude-sonnet-4-5-20250929-v1:0<br/>max_tokens=16000, temperature=0.3
  BR-->>A4: pytest test code
  A4-->>ORC: {test_code, scenario_count, lines_of_code}

  ORC->>A5: validation(test_code, scenarios)
  A5->>A5: ast.parse() syntax check
  A5->>A5: compliance keyword scoring<br/>(audit, OFAC, CTR/SAR, PII, encryption)<br/>threshold 50/100 (PR review is human gate)
  A5-->>ORC: {is_valid, quality_score 0–100,<br/>compliance_score, recommendation}

  Note over ORC,APR: (optional) pre_execution checkpoint — CCO can approve/reject

  ORC->>A6: execution(test_code, validation,<br/>understanding.obligation_ids, scenarios)
  A6->>GH: create branch fortress/{key}-compliance-tests<br/>commit tests/compliance/test_{key}.py<br/>open PR "[Fortress] {key} — Compliance Tests ({score}/100)"
  GH-->>A6: PR URL
  A6->>JIRA: comment "PR opened: {url}"  (best-effort)
  A6->>PG: UPDATE obligations<br/>SET has_test_coverage=TRUE,<br/>    coverage_updated_at=NOW(),<br/>    test_scenario_ids = ... append [{key}::{scenario}]<br/>WHERE obligation_id = ANY(:ids) AND is_active
  A6->>PG: UPDATE workflow_executions<br/>SET status='completed' (or 'degraded' if JIRA comment failed),<br/>completed_at=NOW(), artifacts={pr_url, ...}
  A6-->>ORC: ExecutionResult
```

**Dashboard panels touched:**
- Panel 7 — **Workflows** (status, duration, PR link)
- Panel 4 — **Obligations** (coverage %)
- Panel 2 — **Compliance Rules** (test_count per rule via join to workflow_executions)
- Panel 1 — **Readiness** (testing dimension)

**Tables written:** `workflow_executions` (create + status updates),
`checkpoint_approvals` (when CCO approves/rejects), `obligations.has_test_coverage`
(coverage writeback), `obligations.test_scenario_ids` (scenario IDs:
`{jira_key}::{scenario_name}`). Also: GitHub PR (external), JIRA comment
(external, best-effort).

**Safety properties:**
- One-in-flight per story: partial unique index
  `uq_workflow_jira_story_inflight` (mig 033) keeps webhook retries from
  spawning parallel runs.
- Single Bedrock call cluster-wide: advisory lock `0x70327367` (prevents
  Bedrock quota thrashing across ECS tasks).
- Test runner is fully sandboxed (`PYTHONPATH` intentionally excluded so
  generated tests can't pull production modules).

---

### UC-7: Generate an examiner package (CCO clicks "Export")

**Who triggers it:** CCO/CRO opens Reports panel, picks a report type +
format + as-of date, clicks Generate.

```mermaid
sequenceDiagram
  autonumber
  actor CCO
  participant FE as Reports.jsx
  participant API as exports.py
  participant PG as Aurora
  participant ECS as ECS RunTask<br/>(exports-worker — separate TD family)
  participant SNAP as snapshot.gather()<br/>src/exports/snapshot.py
  participant CAN as canonical.hash_envelope()<br/>src/exports/canonical.py
  participant S3 as S3 — exports bucket<br/>(Object Lock COMPLIANCE)

  CCO->>FE: select type + format + as_of, click Generate
  FE->>API: POST /api/v1/exports<br/>{bank_id, type, as_of, format}<br/>Bearer JWT (require_approver)
  alt format = CSV
    API->>PG: SELECT … gather rows
    API->>API: csv_writer.write()
    API->>S3: PutObject (deterministic key, PUT-if-absent)
    API->>PG: INSERT export_artifacts<br/>(artifact_id, bank_id, type, as_of, format='csv',<br/>version_hash, s3_bucket, s3_key, sha256,<br/>snapshot_canonical_sha256, vocab_version, template_version,<br/>app_commit, object_lock_retain_until=NOW()+7y)
    API-->>FE: artifact row (immediate)
  else format = PDF
    API->>PG: INSERT export_artifacts (status pending)
    API->>ECS: RunTask exports-worker --one-shot {artifact_id}
    API-->>FE: 202 — poll list endpoint
    ECS->>SNAP: gather report data
    SNAP->>PG: SELECT (rules, controls, tests, findings,<br/>change records, OFAC validations, signoffs…)
    SNAP-->>ECS: snapshot dict
    ECS->>ECS: vocabulary.validate(snapshot)
    ECS->>CAN: hash_envelope(snapshot)<br/>→ snapshot_canonical_sha256
    Note over CAN: canonical._default must serialise every<br/>value type — audit per memory<br/>`feedback_canonical_uuid_serialize`
    ECS->>ECS: Playwright render → PDF bytes
    ECS->>S3: PutObject with Object Lock retain-until = NOW() + 7y<br/>KMS-encrypted
    ECS->>PG: UPDATE export_artifacts<br/>(sha256, s3_etag, s3_version_id, byte_size,<br/>generated_at, object_lock_retain_until)
  end

  CCO->>FE: refresh — sees new artifact
  CCO->>FE: click Download
  FE->>API: GET /api/v1/exports/{artifact_id}/download
  API->>PG: INSERT export_access_log<br/>(artifact_id, accessed_by, accessed_at, ip_address, jwt_jti)
  API->>S3: GetObject
  S3-->>API: bytes
  API-->>CCO: streamed file

  opt sign-off
    CCO->>FE: Sign artifact
    FE->>API: POST /api/v1/exports/{artifact_id}/sign
    API->>PG: INSERT export_signoffs<br/>(signoff_id, artifact_id, signer_person_id,<br/>signer_role, signature_method=sso_session,<br/>ip_address, user_agent, jwt_jti, acknowledgement)
  end
```

**Six report types** (`export_artifacts.type` CHECK constraint):

| Type | What it proves | Source tables |
|---|---|---|
| `examiner_package` | Full obligation inventory → mapped control → test result → JIRA/PR trace | `compliance_rules`, `obligations`, `controls`, `control_rule_mappings`, `control_tests`, `test_executions`, `workflow_executions` |
| `ofac_attestation` | OFAC list freshness + tested screening controls + completeness | `ofac_list_versions`, `ofac_sdn_vectors`, `screening_rules`, `screening_events`, `screening_cases`, `ofac_validations` |
| `board_report` | Quarterly coverage trend, changes signed off, open findings | `obligations`, `regulatory_change_records`, `findings` |
| `mra_tracker` | Each finding with owner, deadline, status, closure evidence | `findings`, `finding_history`, `bank_officers` |
| `change_log` | Dated signed-off record of every change + bank disposition | `regulatory_change_records`, `change_record_comments`, `change_record_dispositions` |
| `coverage` | Mapped rules vs tested rules (mock-exam self-assessment) | `compliance_rules`, `control_rule_mappings`, `control_tests`, `workflow_executions` |

**Idempotency:** `UNIQUE (bank_id, type, as_of, version_hash)` on
`export_artifacts`. Same as-of + same source data → same canonical hash →
same artifact returned (no re-render).

**Tamper evidence:** every artifact carries `sha256` of bytes,
`snapshot_canonical_sha256` of input, `vocab_version` (frozen enum
snapshot), `template_version`, `app_commit`. S3 Object Lock COMPLIANCE +
KMS makes them deletable only after `object_lock_retain_until`.

**Dashboard panels touched:** Panel 8 — **Reports**.

---

### UC-8: Policy gap analysis (Panel 6)

**Who triggers it:** CCO maintains the bank's internal-policy register and
wants to see which regulations are not yet covered by an active policy.

```mermaid
sequenceDiagram
  autonumber
  actor CCO
  participant FE as Policy gap panel
  participant API as dashboard_api
  participant PG as Aurora

  CCO->>FE: open policy gap matrix
  FE->>API: GET /api/v1/dashboard/policies?client_id={bank_id}
  API->>PG: SELECT p.* FROM bank_policies p<br/>WHERE p.client_id=:bank AND p.status='active'
  API->>PG: SELECT m.policy_id, m.regulation_id, m.obligation_id,<br/>m.ai_suggested, m.ai_confidence<br/>FROM policy_regulation_mappings m<br/>JOIN bank_policies p ON p.id=m.policy_id<br/>WHERE p.client_id=:bank
  API->>PG: SELECT rule_id, regulator, cfr_citation,<br/>enforcement_risk_score<br/>FROM compliance_rules WHERE is_active
  API->>API: LEFT JOIN rules ←→ mappings → identify uncovered
  API-->>FE: matrix: rule_id × {covered_by_policies[], gap=true|false}
  CCO->>FE: click a gap → "Suggest mapping" (uses pgvector)
  FE->>API: POST /api/v1/dashboard/policies/suggest<br/>{regulation_id}
  API->>PG: hybrid pgvector + tsv on policy descriptions<br/>(reuse compliance_rule_resolver pattern)
  API-->>FE: top-K candidate policies with confidence
  CCO->>FE: confirm mapping
  FE->>API: POST /api/v1/dashboard/policies/{policy_id}/mappings<br/>{regulation_id, obligation_id, mapping_rationale}
  API->>PG: INSERT policy_regulation_mappings<br/>(policy_id, regulation_id, obligation_id,<br/>mapping_rationale, mapped_by, ai_suggested=true, ai_confidence)
```

**Dashboard panel:** (not yet wired in UI per dashboard inventory — backend
endpoints exist; flag as a near-term build).

**Tables written:** `policy_regulation_mappings` (one INSERT per mapping;
`UNIQUE (policy_id, regulation_id)`).

---

### UC-9: Case management — Analyst clears a REVIEW case

```mermaid
sequenceDiagram
  autonumber
  actor A as Analyst
  participant FE as Cases.jsx / Investigation.jsx
  participant API as screening_api
  participant PG as Aurora

  A->>FE: open /dashboard/cases?status=open
  FE->>API: GET /api/v1/dashboard/screening/cases?status=open
  API->>PG: SELECT case_id, priority, decision_at_open,<br/>matched_name, sdn_number, external_customer_id,<br/>event_count, status, created_at<br/>FROM screening_cases<br/>WHERE status='open'<br/>ORDER BY priority DESC, created_at ASC
  PG-->>API: rows
  API-->>FE: list
  A->>FE: click case → /dashboard/cases/{case_id}
  FE->>API: GET /api/v1/dashboard/screening/cases/{case_id}
  API->>PG: SELECT case + JOIN ofac_sdn_entries on sdn_number<br/>+ JOIN screening_events via case_events
  API-->>FE: case header + score breakdown + matched entity card<br/>+ decision history
  A->>FE: enter notes, click "Send to review" or "Pass"
  alt Pass on HOLD case (requires CCO + ≥50 char note)
    FE->>API: POST .../decision {action=pass, notes}
    API->>API: check role==CCO + len(notes)≥50
  else Review / Escalate
    FE->>API: POST .../decision {action, notes}
  end
  API->>PG: UPDATE screening_cases<br/>SET status, reviewer_action, reviewer_notes,<br/>resolved_at, resolved_by_user_id, last_event_id
  API->>PG: INSERT new screening_events row<br/>(actor_kind='dashboard', engine_mode same as opener)
  API->>PG: INSERT screening_case_events join row
```

**Dashboard panels touched:** Panel 10 (cases list) and Panel 11 (investigation).

**Tables written:** `screening_cases` (status update), `screening_events`
(new audit row for the decision), `screening_case_events` (join).

**Auditor-friendly properties:** decision history table on the
Investigation panel is just `SELECT … FROM screening_events WHERE id IN
(case.first_event_id, case_events…) ORDER BY created_at` — there's no
hidden state.

---

### UC-10: Executive readiness scorecard (CCO/CRO/CIO)

```mermaid
sequenceDiagram
  autonumber
  actor EXEC as CCO / CRO / CIO
  participant FE as Readiness.jsx
  participant API as dashboard_api

  EXEC->>FE: open /readiness — pick lens (CCO|CRO|CIO)
  FE->>API: GET /api/v1/dashboard/readiness?lens=cco
  API->>API: _dimension_scores() runs ~12 aggregate queries
  Note over API: compliance_rules: total, with CFR, with enforcement_risk<br/>workflow_executions: tested rules<br/>obligations: total, covered, attributed<br/>regulatory_change_records: total, open non-OFAC<br/>enforcement_actions: recent by regulator (180d)
  API-->>FE: readiness {0–100} + 10 dimensions + band
  FE->>API: GET /api/v1/dashboard/risk-matrix
  API-->>FE: 3×3 risk × coverage grid (untested-high quadrant in red)
  alt lens=cro
    FE->>API: GET /api/v1/dashboard/cro
    API-->>FE: residual-vs-appetite + KRIs + peer signal
  else lens=cio
    FE->>API: GET /api/v1/dashboard/cio
    API-->>FE: engine integrity + sync freshness + architecture manifest
  end
  FE->>API: GET /api/v1/dashboard/findings
  API-->>FE: findings list (empty if no findings table yet)
  FE-->>EXEC: gauge + matrix + 10 dimensions + findings
```

**Dashboard panel:** Panel 1 — **Readiness**.

**Tables read (no writes):** `compliance_rules`, `workflow_executions`,
`obligations`, `regulatory_change_records`, `enforcement_actions`,
`rule_overrides`, `findings`.

---

## 5. Dashboard panel inventory — single page

| # | Route | Component | API(s) | Key tables | RBAC |
|---|---|---|---|---|---|
| 1 | `/readiness` | Readiness.jsx | `/readiness`, `/risk-matrix`, `/cro`, `/cio`, `/findings` | compliance_rules, workflow_executions, obligations, regulatory_change_records, enforcement_actions, findings | All read |
| 2 | `/rules` | Rules.jsx | `/rules`, `/rules/{id}/status` (PATCH), `/rules/{id}/generate-test` | compliance_rules, rule_overrides, workflow_executions | Read all; edit → CCO/Admin |
| 3 | `/changes` | Changes.jsx | `/changes`, `/changes/{ref}`, `/changes/{ref}/signoff` | regulatory_change_records, change_record_comments, change_record_dispositions | Read all; signoff → CCO/CRO/Admin |
| 4 | `/obligations` | Obligations.jsx | `/obligations` | obligations | All read |
| 5 | `/enforcement` | Enforcement.jsx | `/enforcement` | enforcement_actions | All read |
| 6 | `/ofac` | OfacHealth.jsx | `/ofac-health` | ofac_sdn_entries, ofac_sdn_vectors | All read |
| 7 | `/workflows` | Workflows.jsx | `/workflows` | workflow_executions | All read |
| 8 | `/reports` | Reports.jsx | `/exports` (POST/GET), `/exports/{id}/download`, `/exports/{id}/sign` | export_artifacts, export_signoffs, export_access_log | Generate/sign → CCO/CRO/Admin |
| 9 | `/dashboard/search` | SdnSearch.jsx | `/screening/search`, `/screening/cases/{id}/decision` | ofac_sdn_vectors, screening_events, screening_cases | Analyst+ |
| 10 | `/dashboard/cases` | Cases.jsx | `/screening/cases?status=…` | screening_cases | Analyst+ |
| 11 | `/dashboard/cases/:id` | Investigation.jsx | `/screening/cases/{id}`, decision | screening_cases, screening_events, ofac_sdn_entries | Analyst+; Pass on HOLD → CCO |
| 12 | `/dashboard/rules-perf` | RulesPerf.jsx | `/screening/rules-perf`, `/screening/rules/{id}` (PATCH) | screening_rules, screening_events (engine_mode split) | CCO/CRO read; Admin edit |
| 13 | `/dashboard/executive` | ExecutiveToday.jsx | `/screening/executive-today` | screening_events (today) | CCO |
| + | `/dashboard/vector` | VectorIntel.jsx | `/screening/vector-intel` | ofac_sdn_vectors, screening_events | Admin/CRO read; Admin write |

**Cognito role mapping** (from `dashboard/src/App.jsx`):
- `canEdit` — CCO, Admin (write to rules/changes)
- `canApprove` — CCO, CRO, Admin (sign-off + export generation)
- `canScreen` — Analyst, CCO, CRO, Admin
- `canViewVectorIntel` — Admin, CRO
- `canGovernModel` — Admin only (tune weights/thresholds)

**Panels NOT yet built (but data exists):** policy gap matrix (UC-8),
compliance-deadline countdown, control attestation, findings/MRA detail view,
peer benchmarking comparison, audit-log viewer. Useful list to share with the
CCO so they know what's coming.

---

## 6. ERD — P2-native tables + P1-synced replicas

```mermaid
erDiagram
  BANK_PROFILE ||--o{ BANK_OFFICERS : employs
  BANK_PROFILE ||--o{ CONTROLS : owns
  BANK_PROFILE ||--o{ FINDINGS : has
  BANK_PROFILE ||--o{ EXPORT_ARTIFACTS : generates
  BANK_PROFILE ||--o{ CHANGE_RECORD_DISPOSITIONS : dispositions
  BANK_PROFILE ||--o{ OFAC_VALIDATIONS : tests

  BANK_OFFICERS ||--o{ FINDINGS : owns
  BANK_OFFICERS ||--o{ EXPORT_SIGNOFFS : signs
  BANK_OFFICERS ||--o{ CONTROLS : owns
  BANK_OFFICERS ||--o{ CHANGE_RECORD_DISPOSITIONS : owns
  BANK_OFFICERS ||--o{ OFAC_VALIDATIONS : performs

  CONTROLS ||--o{ CONTROL_RULE_MAPPINGS : maps
  CONTROLS ||--o{ CONTROL_TESTS : tested_by
  CONTROL_TESTS ||--o{ TEST_EXECUTIONS : runs
  CONTROL_RULE_MAPPINGS }o..o{ COMPLIANCE_RULES : "loose: rule_id"

  FINDINGS ||--o{ FINDING_HISTORY : audit
  FINDINGS ||--o{ FINDINGS : repeat_of

  WORKFLOW_EXECUTIONS ||--o{ CHECKPOINT_APPROVALS : has
  WORKFLOW_EXECUTIONS }o..o{ COMPLIANCE_RULES : "loose: rule_id"

  BANK_POLICIES ||--o{ POLICY_REGULATION_MAPPINGS : covers
  POLICY_REGULATION_MAPPINGS }o..o{ COMPLIANCE_RULES : "loose: regulation_id"
  POLICY_REGULATION_MAPPINGS }o..o{ OBLIGATIONS : "loose: obligation_id"

  SCREENING_EVENTS ||--o{ SCREENING_CASES : opens
  SCREENING_CASES ||--o{ SCREENING_CASE_EVENTS : re_hits
  SCREENING_CASE_EVENTS }o--|| SCREENING_EVENTS : refs
  SCREENING_CASES }o..|| SCREENING_RULES : matched_rule_id

  EXPORT_ARTIFACTS ||--o{ EXPORT_SIGNOFFS : signed_by
  EXPORT_ARTIFACTS ||--o{ EXPORT_ACCESS_LOG : downloads
  EXPORT_ARTIFACTS }o..|| VOCAB_VERSIONS : vocab_version

  REGULATORY_CHANGE_RECORDS ||--o{ CHANGE_RECORD_COMMENTS : audited_by
  REGULATORY_CHANGE_RECORDS ||--o{ CHANGE_RECORD_DISPOSITIONS : "loose: change_ref"

  TRANSACTION_VALIDATIONS }o..o{ COMPLIANCE_RULES : "loose: rule_id in violations"

  COMPLIANCE_RULES ||--o{ OBLIGATIONS : "loose: regulation_id"
  OFAC_SDN_ENTRIES ||--o{ OFAC_SDN_VECTORS : "loose: sdn_number"

  LOCAL_SYNC_STATUS {
    uuid id
    bigint sync_version
    timestamp sync_timestamp
    int rules_added
    int rules_updated
    int rules_deleted
    varchar sync_status
    varchar sync_type
  }
  SYNC_METADATA {
    uuid id
    varchar customer_id
    bigint last_sync_version
    timestamp p1_sdn_last_scraped_at
    timestamp last_sdn_change_consumed_at
    varchar last_sdn_change_consumed_ref
  }
```

**Notes on the graph:**
- Single dashed edges = **loose string links** (no enforced FK). Used for
  cross-pipeline references (`rule_id`, `regulation_id`, `change_ref`,
  `obligation_id`, `sdn_number`) so partial syncs and per-cycle writes
  don't trigger FK violations.
- Solid edges = **enforced FK**. Used inside P2-native clusters
  (controls→tests, workflow→checkpoint, artifact→signoff, finding→history,
  case→case_events).
- `regulatory_change_records` is shared schema with P1, but P2 enriches it
  with `client_id`, `updated_at`, and the sign-off workflow columns.

---

## 7. Migration timeline (P2-specific)

| Mig | Tables touched | What it does |
|---|---|---|
| 016 | `workflow_executions`, `checkpoint_approvals` | Agent workflow orchestration core |
| 017 | `bank_policies`, `policy_regulation_mappings` | Policy gap matrix (Panel 6) |
| 020 | `regulatory_change_records` | `*_at` columns → `TIMESTAMPTZ` |
| 028 | `screening_events`, `screening_cases`, `screening_case_events` | OFAC case management + immutable audit log |
| 030 | `sync_metadata` | `p1_sdn_last_scraped_at` (SDN freshness cache) |
| 031 | `sync_metadata` | `last_sdn_change_consumed_at` + `_ref` (keyset cursor for /sync/sdn-changes) |
| 033 | `workflow_executions` | Partial unique index `uq_workflow_jira_story_inflight` (one-in-flight per story) |
| 034 | `screening_rules` | DB-backed inventory + seeds FUZZY-001 / CLEAN-001 / OWN50-001 / FRESH-001 |
| 035 | `screening_cases.matched_rule_id` | FK to `screening_rules` (ON DELETE SET NULL) |
| 037 | `regulatory_change_records.change_delta` | Per-record JSONB diff (frozen at detection) |
| 038 | `ofac_list_versions` | OFAC freshness anchor (used in attestation export) |
| 042 | 14 tables (bank_profile, bank_officers, controls, control_rule_mappings, control_tests, test_executions, findings, finding_history, change_record_dispositions, ofac_validations, export_artifacts, export_signoffs, export_access_log, vocab_versions) | Examination evidence & reports (M16) |

P1 migrations 007–023 + 037 still apply because P2 replicates those tables —
see [PIPELINE1_CCO_WALKTHROUGH.md §11](./PIPELINE1_CCO_WALKTHROUGH.md).

---

## 8. Auth, multi-tenancy, and runtime guardrails

| Question CCO will ask | Where the answer lives |
|---|---|
| "How do we keep one bank's data out of another bank's view?" | Per-bank ECS cluster, ALB, Aurora, and S3 export bucket. Each P2 deployment is fully isolated; only the regulatory plane (P1) is shared. SSM paths are namespaced `/fortress/{bank_id}/*`. |
| "How do users log in?" | Cognito hosted UI. JWT carries `cognito:groups`; P2 API resolves to one of {Analyst, CCO, CRO, Admin}. Dev token fallback fails closed when `ENVIRONMENT=production`. |
| "How do we prevent a runaway test from leaking secrets?" | Sandbox test runner inherits **only** PATH/LANG/LC_ALL/TERM/PYTHONUSERBASE; `PYTHONPATH` is intentionally excluded so generated tests can't `import src.api.sync_api`. HOME/TMPDIR forced to the per-run output dir so pytest's cache can't reach `~/.aws/credentials`. |
| "How do we prevent the dashboard from being scraped externally?" | CloudFront in front of S3 SPA + WAF on the API ALB. Per memory `project_p2_waf_blocklist`: priority-0 IPSet blocklist with XFF rule (because ALB sees CloudFront's IP, not the client). |
| "How do we know an artifact wasn't doctored?" | `export_artifacts.sha256` (bytes) + `snapshot_canonical_sha256` (input) + `vocab_version` + `template_version` + `app_commit`. S3 Object Lock COMPLIANCE prevents deletion until `object_lock_retain_until` (default NOW + 7 years). |
| "Can a runaway agent re-process the same JIRA story?" | No. Webhook dedup on completed run + partial unique index `uq_workflow_jira_story_inflight` keeps one in-flight; advisory lock `0x70327367` serialises Bedrock calls cluster-wide. |
| "Can we revoke a CCO mid-shift?" | Remove user from the Cognito group → next JWT refresh (≤1 h) loses the role. For immediate revocation, disable the Cognito user. |
| "Where does customer transaction data go?" | Nowhere outbound. All `validators.py` flows write a non-PII audit row to `transaction_validations` and screening_events. The only data that leaves P2 is the **telemetry** sent back to P1 via `/sync/status` (counts only, no PII). |

---

## 9. Where to look in the code

| Topic | File:line |
|---|---|
| Entry point (routers, lifespan, CORS) | `src/main_pipeline2.py:1–124` |
| 6 agents | `src/agents/{discovery, understanding, framework, generation_bedrock, validation, execution}.py` |
| Workflow orchestration | `src/orchestration/workflow_engine.py` |
| JIRA webhook (auth + dedup + lock) | `src/api/webhooks.py:1–150` |
| Checkpoint approvals | `src/api/approvals.py:1–87` |
| Validators (Wire / OFAC / Loan) | `src/services/validators.py` |
| Validation API (rate-limit + metrics) | `src/api/validation_api.py:1–150` |
| Screening API (dual auth + idempotency) | `src/api/screening_api.py:1–100` |
| Dashboard API | `src/api/dashboard_api.py:1–1000` |
| Exports API | `src/api/exports.py:74–150` |
| Exports worker (snapshot + canonical + render) | `src/exports/worker.py`, `snapshot.py`, `canonical.py`, `csv_writer.py` |
| UpdateService (P1 sync) | `src/services/update_service.py:1–1600` |
| ComplianceRuleResolver | `src/services/compliance_rule_resolver.py:121–577` |
| GitHub PR service | `src/services/github_service.py` |
| Sandbox test runner | `src/services/test_runner.py` |
| Cognito auth + RBAC | `src/api/auth.py:1–150` |
| Models (all P2 tables) | `src/models/database.py` |
| Migrations | `alembic/versions/` (016, 017, 020, 028, 030–031, 033–035, 037–038, 042) |
| Dashboard SPA | `dashboard/src/App.jsx`, `dashboard/src/pages/*` |
| Dashboard deploy | `dashboard/deploy.sh` |
| CloudFormation (P2) | `infra/cloudformation/pipeline2-{vpc,data,compute,security,dashboard,exports-worker,exports-bucket,exports-alarms,billing,ba-jump}.yml` |
