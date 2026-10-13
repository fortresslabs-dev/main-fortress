# Examination Evidence & Reports — Design Document

| Meta | Value |
|---|---|
| Owner | Dashboard / P2 Compliance |
| Status | Draft v1 — awaiting CCO + Engineering sign-off |
| Sources | `dashboard/prompt.md`, `dashboard/0[1-6]*.html`, `dashboard/src/pages/Reports.jsx`, `src/api/dashboard_api.py`, `src/models/database.py` |
| Pipelines | P1 (Fortress Cloud, source-of-truth) + P2 (per-bank dashboard) |
| Date | 2026-06-20 |

---

## 1. Executive summary

The dashboard's **Examination evidence & reports** tab exposes six exports
(`examiner_package`, `ofac_attestation`, `board_report`, `mra_tracker`,
`change_log`, `coverage_gap`). Today the flow renders ad-hoc HTML in a browser
print dialog, with no immutable storage, no per-bank S3 bucket, no e-signature
chain, and large data gaps (no findings table, no control catalogue, no OFAC
list-version log).

`prompt.md` mandates server-rendered, content-hashed, WORM-stored artifacts
that match the six gold HTML templates exactly. This document is the field-
level design: what changes in **P1 (cloud)**, what changes in **P2 (per-bank)**,
how data flows from **UI → API → DB → S3 → PDF**, and how each banking-
compliance control is implemented.

---

## 2. Scope

In scope:
- Six exports listed in `prompt.md §1`.
- P1 and P2 DB schema changes required to populate every field in `prompt.md §4`.
- Server-side render path (Playwright headless Chromium).
- S3 WORM artifact store, per-bank KMS, e-signature chain, access log.
- UI changes to `Reports.jsx` (remove `window.print()`, add sign-off step, history view).

Out of scope:
- Non-export dashboard pages (Rules, Changes, OFAC Health, etc.) — they remain
  the data sources, not the artifact producers.
- Vector / BM25 search engine internals (already covered by separate runbooks).

---

## 3. Current state (as-is)

### 3.1 High-level (today)

```mermaid
flowchart LR
  U["CCO / CRO user (browser)"]
  R["Reports.jsx tile"]
  A["POST /api/v1/dashboard/exports<br/>(dashboard_api.py:1160)"]
  G["_gather_export_payload()<br/>(dashboard_api.py:1095)"]
  DB[("P2 Aurora<br/>compliance_rules, ofac_*,<br/>regulatory_change_records,<br/>workflow_executions")]
  TOK["Base64 token (stateless)<br/>_encode_export_token()"]
  D["GET /exports/{id}/download<br/>(dashboard_api.py:1321)"]
  REN["_render_pdf_like_html()<br/>generic key/value HTML"]
  PRN["window.open + window.print()<br/>(Reports.jsx:54)"]

  U --> R --> A --> G --> DB
  A --> TOK --> R
  R --> D --> G
  D --> REN --> PRN --> U
```

### 3.2 Current sequence

```mermaid
sequenceDiagram
  autonumber
  participant U as CCO / CRO
  participant UI as Reports.jsx
  participant API as dashboard_api.py
  participant DB as P2 Aurora
  participant BR as Browser

  U->>UI: Click "PDF" tile
  UI->>API: POST /exports {type, as_of, format}
  API->>DB: SELECT (per type, no isolation level set)
  API->>API: Canonical JSON -> SHA-256[:7] = version_hash
  API->>API: base64 token (no persistence)
  API-->>UI: {export_id, version_hash, status:"ready"}
  UI->>API: GET /exports/{id}/download
  API->>DB: Same SELECT again
  API->>API: Re-hash + render HTML (key/value table)
  API-->>UI: text/html bytes
  UI->>BR: window.open(blobUrl)
  BR->>BR: window.print()
```

### 3.3 Current API surface (in `dashboard_api.py`)

| Endpoint | Lines | Notes |
|---|---|---|
| `POST /api/v1/dashboard/exports` | 1160-1194 | Stateless token; no S3, no persistence. |
| `GET /api/v1/dashboard/exports/{id}/download` | 1321-1366 | Re-gathers + re-renders. Returns HTML labelled `text/html` with `.html` extension. |
| `_gather_export_payload` | 1095-1151 | Per-type SELECTs; `mra_tracker` returns `[]` (no backing table). |
| `_render_pdf_like_html` | 1235-1318 | Generic Georgia-serif HTML, **does not match** any of the six gold templates. |
| `_canonical_export_bytes` | 1197-1203 | `json.dumps(sort_keys=True, default=str)` -> SHA-256 prefix. **Reusable** for §4 hash. |

### 3.4 Current DB schema (relevant subset)

```mermaid
erDiagram
  compliance_rules ||--o{ obligations : has
  compliance_rules ||--o{ workflow_executions : "tested by"
  compliance_rules ||--o{ regulatory_change_records : "changed by"
  compliance_rules ||--o{ compliance_deadlines : "filing for"

  compliance_rules {
    string rule_id PK
    string regulator
    text cfr_citation
    text rule_text
    float enforcement_risk_score
    int obligation_count
    jsonb change_delta
    bool is_active
  }
  workflow_executions {
    uuid id PK
    string jira_story_id
    string jira_story_key
    string rule_id FK
    string status
    datetime started_at
    json artifacts
  }
  regulatory_change_records {
    string change_ref PK
    string regulator
    string rule_id FK
    text change_summary
    jsonb semantic_diff
    string status
    datetime signed_off_at
    jsonb change_delta
  }
  ofac_sdn_vectors {
    bigint id PK
    string sdn_number
    text name_variant
    vector embedding
    timestamptz last_seen_at
  }
  compliance_deadlines {
    string deadline_name PK
    int days_remaining
    bool is_active
  }
```

### 3.5 Current UI structure (`Reports.jsx`)

```mermaid
flowchart TD
  S[State: asOf, stamp, busy, error]
  T["TILES[] - 6 entries with {type, tag, title, formats}"]
  H["asOf input + stamp display"]
  G["Tile grid -> per-tile PDF/CSV buttons"]
  R["run(type, format) calls exportApi.create"]
  X["if PDF: window.open + setTimeout window.print"]
  Y["if CSV: a.download"]

  S --> H
  T --> G
  G --> R --> X
  R --> Y
```

### 3.6 Gaps vs `prompt.md` (the as-is checklist)

| `prompt.md` requirement | Current | Gap |
|---|---|---|
| §2.1 server-render not browser-print | `window.print()` | **HARD GAP** — change everything below the API. |
| §2.2 hash data not PDF | `_canonical_export_bytes` already hashes data | **OK** — keep & extend with template/vocab version. |
| §2.3 immutable, retained, S3 keyed by `{bank,type,as_of,hash}` | Base64 token only | **HARD GAP** — needs S3 + DB row. |
| §2.4 point-in-time reproducibility | Reads "now" each call | **HARD GAP** — needs snapshot isolation + stored snapshot. |
| §2.5 footer on every page | None | **GAP** — needs Playwright `footerTemplate`. |
| §2.6 fixed vocabulary | No CHECK constraints, no Python Enum | **GAP** — needs DB CHECK + Enum + render validator. |
| §2.7 CSV companions with §4 columns | Generic column-from-payload CSV | **GAP** — per-type CSV writer. |
| §4 institution / charter / officers | Not in DB | **HARD GAP** — needs `bank_profile`, `bank_officers`. |
| §4 control + threshold + threshold_rationale + commit_hash + evidence_uri | Not in DB | **HARD GAP** — needs `controls`, `control_tests`, `test_executions`. |
| §4 findings 5 Cs + repeat_flag + root_cause | `findings: []` placeholder | **HARD GAP** — needs `findings` + history. |
| §4 OFAC list version + publication time + independent validation | `last_seen_at` only | **GAP** — needs `ofac_list_versions` (P1) + `ofac_validations` (P2). |
| §4 change disposition with vocab | Free-text `status` | **GAP** — needs `change_record_dispositions` + CHECK. |
| §4 coverage trend vs prior as-of | No history | **GAP** — automatic once artifacts are immutable. |

---

## 4. Target state (to-be per `prompt.md`)

### 4.1 High-level architecture (target)

```mermaid
flowchart TB
  subgraph User["Browser"]
    UI["Reports.jsx<br/>(tiles, sign-off modal,<br/>history table)"]
  end

  subgraph P2API["P2 ECS - API task"]
    EX["src/api/exports.py<br/>POST create / sign / GET list / download"]
    SNAP["src/exports/snapshot.py<br/>pg_export_snapshot()"]
    CAN["src/exports/canonical.py<br/>SHA-256[:7]"]
    VOC["src/exports/vocabulary.py<br/>Enums + validators"]
    STO["src/exports/storage.py<br/>S3 WORM PUT"]
  end

  subgraph Worker["P2 ECS - exports-worker task (Chromium)"]
    REN["src/exports/renderer.py<br/>Playwright headless"]
    TPL["src/exports/templates/*.jinja<br/>(6 templates + _base)"]
    FONT["self-hosted fonts<br/>Spectral, IBM Plex"]
  end

  subgraph P2DB["P2 Aurora pgvector"]
    BP[("bank_profile<br/>bank_officers")]
    CTL[("controls<br/>control_rule_mappings<br/>control_tests<br/>test_executions")]
    FND[("findings<br/>finding_history")]
    CHG[("change_record_dispositions")]
    OV[("ofac_validations")]
    ART[("export_artifacts<br/>export_signoffs<br/>export_access_log")]
  end

  subgraph S3["S3 (per bank, Object Lock COMPLIANCE)"]
    PDF[("fortress-p2-exports-{bank}-{env}<br/>{bank}/{type}/{as_of}/{hash}.pdf")]
    CSV[("...{hash}.csv")]
    SRC[("snapshots/{hash}.json")]
  end

  subgraph P1API["P1 ECS - sync API"]
    SYNC["/sync/rules?include=threshold<br/>/sync/ofac-list-version<br/>/sync/enforcement-summary"]
  end

  subgraph P1DB["P1 Aurora"]
    CR[("compliance_rules + threshold_default<br/>threshold_rationale (NEW)")]
    OLV[("ofac_list_versions (NEW)")]
    EA[("enforcement_actions + peer_signal_score")]
  end

  UI -->|JWT bearer| EX
  EX --> SNAP --> P2DB
  SNAP -.daily refresh.-> P1API
  P1API --> P1DB
  EX --> CAN --> VOC
  EX --> STO --> S3
  EX -.enqueue render.-> Worker
  Worker --> TPL
  Worker --> FONT
  Worker --> STO
  STO --> ART
  EX --> ART
```

### 4.2 Component flow (per export request)

```mermaid
flowchart LR
  A["1. UI POST /exports<br/>{type, as_of}"] --> B["2. AuthZ: require_approver +<br/>bank_id == JWT.bank_id"]
  B --> C["3. snapshot.gather(type, as_of)<br/>REPEATABLE READ + pg_export_snapshot"]
  C --> D["4. canonical.hash(snapshot,<br/>template_ver, vocab_ver, app_commit)"]
  D --> E{"5. export_artifacts<br/>exists for<br/>(bank,type,as_of,hash)?"}
  E -- yes --> F["Return existing artifact_id<br/>(idempotent)"]
  E -- no --> G["6. PUT snapshot JSON to S3<br/>(WORM, KMS)"]
  G --> H["7. INSERT export_artifacts row<br/>(status=pending_render)"]
  H --> I["8. Enqueue render job<br/>(SQS -> exports-worker)"]
  I --> J["9. Worker: jinja render + Playwright<br/>page.pdf(footerTemplate)"]
  J --> K["10. PUT PDF/CSV to S3<br/>(WORM 5y, KMS)"]
  K --> L["11. UPDATE export_artifacts<br/>status=rendered, s3_etag, byte_size"]
  L --> M["12. UI shows artifact in history<br/>+ Sign button"]
  M --> N["13. POST /exports/{id}/sign<br/>INSERT export_signoffs"]
  N --> O{"All required<br/>signoffs present?"}
  O -- no --> M
  O -- yes --> P["14. status=sealed<br/>downloadable to external"]
  P --> Q["15. GET /download -> S3 GET<br/>INSERT export_access_log"]
```

### 4.3 Sequence — create export (point-in-time)

```mermaid
sequenceDiagram
  autonumber
  participant U as CCO
  participant UI as Reports.jsx
  participant API as exports.py
  participant SNAP as snapshot.py
  participant DB as P2 Aurora
  participant CAN as canonical.py
  participant S3 as S3 (Object Lock)
  participant SQS as SQS render queue
  participant W as exports-worker
  participant TPL as renderer + jinja

  U->>UI: select as_of=2026-06-17, click PDF
  UI->>API: POST /exports {type:"examiner_package", as_of}
  API->>API: require_approver + bank_id match
  API->>SNAP: gather(type, as_of)
  SNAP->>DB: BEGIN ISOLATION LEVEL REPEATABLE READ
  SNAP->>DB: SET TRANSACTION SNAPSHOT <as_of_snapshot_id>
  SNAP->>DB: SELECT bank_profile, controls, test_executions, ...
  DB-->>SNAP: rows as-of 2026-06-17
  SNAP-->>API: snapshot dict
  API->>CAN: hash(snapshot + template_ver + vocab_ver + app_commit)
  CAN-->>API: v.A1B2C3D
  API->>DB: SELECT export_artifacts WHERE (bank,type,as_of,hash)
  alt artifact exists
    DB-->>API: row
    API-->>UI: {artifact_id, status:"sealed", version_hash}
  else not exists
    API->>S3: PUT snapshots/{hash}.json (WORM, KMS)
    S3-->>API: ETag
    API->>DB: INSERT export_artifacts (status=pending_render)
    API->>SQS: enqueue {artifact_id}
    API-->>UI: {artifact_id, status:"pending_render"}
    SQS->>W: poll
    W->>DB: SELECT artifact + snapshot ref
    W->>S3: GET snapshots/{hash}.json
    W->>TPL: render(type, snapshot) -> HTML
    TPL-->>W: HTML bytes
    W->>W: Playwright page.pdf(footerTemplate)
    W->>S3: PUT {bank}/{type}/{as_of}/{hash}.pdf (WORM, KMS)
    W->>S3: PUT ...{hash}.csv (when applicable)
    W->>DB: UPDATE status=rendered, s3_etag, byte_size, sha256
  end
  UI->>API: poll GET /exports/{id}
  API-->>UI: {status:"rendered"}
```

### 4.4 Sequence — sign-off and seal

```mermaid
sequenceDiagram
  autonumber
  participant CCO as CCO
  participant IA as Internal Audit
  participant UI as Reports.jsx
  participant API as exports.py
  participant DB as P2 Aurora
  participant S3 as S3

  CCO->>UI: Click "Sign as BSA Officer / CCO"
  UI->>API: POST /exports/{id}/sign
  API->>API: require_approver, role=cco
  API->>DB: INSERT export_signoffs (signer, role, jti, ip, ua)
  API->>DB: SELECT count(*) signed vs required_signers
  API-->>UI: {signed_by:[cco], remaining:[internal_audit]}

  IA->>UI: Click "Sign as Independent Reviewer"
  UI->>API: POST /exports/{id}/sign (role=internal_audit)
  API->>DB: INSERT export_signoffs
  API->>DB: UPDATE export_artifacts SET status='sealed'
  API->>S3: PUT sealed/{hash}.pdf (re-stamps signoff block,<br/>same hash, new key prefix)
  API-->>UI: {status:"sealed"}
```

### 4.5 Sequence — download (every download is logged)

```mermaid
sequenceDiagram
  autonumber
  participant U as User
  participant UI as Reports.jsx
  participant API as exports.py
  participant DB as P2 Aurora
  participant S3 as S3

  U->>UI: Click row in history -> Download
  UI->>API: GET /exports/{id}/download
  API->>DB: SELECT export_artifacts WHERE id AND bank_id=JWT.bank_id
  alt status != 'sealed' AND consumer='external'
    API-->>UI: 409 not yet sealed
  else
    API->>S3: GET sealed/{hash}.pdf (KMS decrypt)
    S3-->>API: bytes
    API->>DB: INSERT export_access_log (user, ip, jti)
    API-->>UI: application/pdf + Content-Disposition
  end
```

---

## 5. DB schema changes — split by pipeline

### 5.1 Pipeline 1 (Fortress Cloud — shared, source-of-truth)

Migration numbers continue from current head `037`.

```mermaid
erDiagram
  compliance_rules ||--o| compliance_rules_v2 : "ALTER ADD COLUMNS"
  scrape_runs ||--o{ ofac_list_versions : "produced by"
  enforcement_actions ||--o| enforcement_actions_v2 : "ALTER ADD COLUMNS"

  compliance_rules_v2 {
    jsonb threshold_default "NEW migration 039"
    text threshold_rationale "NEW migration 039"
    text threshold_source_citation "NEW migration 039"
  }
  ofac_list_versions {
    bigint id PK "NEW migration 038"
    string list_name "sdn or consolidated"
    string version_id "vendor's version string"
    timestamptz published_at
    timestamptz ingested_at
    text source_url
    char64 sha256_of_xml "tamper-evident anchor"
    uuid scrape_run_id FK
  }
  enforcement_actions_v2 {
    text summary_pretty "NEW migration 040"
    numeric peer_signal_score "NEW migration 040"
  }
  regulatory_change_records_v2 {
    text disposition_vocab_version "NEW migration 041"
  }
```

#### 5.1.1 P1 migration list

| # | DDL summary | Backfill |
|---|---|---|
| 038 | `CREATE TABLE ofac_list_versions (...)` + FK to `scrape_runs` | none — populated on next OFAC scrape |
| 039 | `ALTER compliance_rules ADD threshold_default JSONB, threshold_rationale TEXT, threshold_source_citation TEXT` | seed from `compliance_rule_resolver.py` constants (one-shot script) |
| 040 | `ALTER enforcement_actions ADD summary_pretty TEXT, peer_signal_score NUMERIC(5,2)` | seed via Bedrock summariser (background job) |
| 041 | `ALTER regulatory_change_records ADD disposition_vocab_version TEXT DEFAULT '2026-06'` | default fills existing rows |

#### 5.1.2 P1 sync API additions

| Verb | Path | Returns | Consumer |
|---|---|---|---|
| `GET` | `/sync/ofac-list-version` | latest row of `ofac_list_versions` | P2 `update_service._sync_ofac_list_versions()` |
| `GET` | `/sync/rules?include=threshold` | `compliance_rules` + new threshold cols | P2 `_sync_rules_with_thresholds()` |
| `GET` | `/sync/enforcement-summary?period=YYYY-Qn` | aggregate with `peer_signal_score` | P2 board_report snapshot |

### 5.2 Pipeline 2 (per-bank — where exports live)

P2 migrations are namespaced `NNNp2` because P1 and P2 have independent Alembic
heads (per CLAUDE.md schema table). Numbers continue from current P2 head.

```mermaid
erDiagram
  bank_profile ||--o{ bank_officers : "has"
  bank_officers ||--o{ findings : "owns"
  bank_officers ||--o{ controls : "owns"
  bank_officers ||--o{ export_signoffs : "signs"

  controls ||--o{ control_rule_mappings : "covers"
  control_rule_mappings }o--|| compliance_rules_synced : "P1 rule_id"
  controls ||--o{ control_tests : "tested by"
  control_tests ||--o{ test_executions : "executed"

  findings ||--o{ finding_history : "audited"
  findings ||--o| findings : "repeat_of"

  regulatory_change_records_synced ||--o{ change_record_dispositions : "dispositioned"

  bank_profile ||--o{ ofac_validations : "validates"

  export_artifacts ||--o{ export_signoffs : "signed by"
  export_artifacts ||--o{ export_access_log : "downloaded by"

  bank_profile {
    string bank_id PK
    string legal_name
    string charter_number
    string primary_regulator
    string_array secondary_regulators
    numeric total_assets_usd
    date total_assets_as_of
    date fiscal_year_end
    text address
  }
  bank_officers {
    uuid person_id PK
    string bank_id FK
    string full_name
    enum role "bsa_officer|cco|cro|internal_audit|audit_committee_chair"
    string email
    date started_at
    date ended_at "NULL when active"
  }
  controls {
    string control_id PK
    string bank_id
    string name
    enum owner_role
    uuid owner_person_id FK
    text description
    enum control_type "preventive|detective|corrective"
  }
  control_rule_mappings {
    bigint id PK
    string control_id FK
    string rule_id "synced from P1"
    enum mapping_strength "full|partial"
    string created_by
    timestamptz created_at
  }
  control_tests {
    string test_id PK
    string bank_id
    string control_id FK
    enum framework "pytest|manual|continuous"
    numeric threshold_value
    string threshold_unit
    text threshold_rationale
    string threshold_source_rule_id "P1 rule_id"
    string jira_story_key
    string github_pr_url
    string commit_hash
    string code_path
  }
  test_executions {
    uuid execution_id PK
    string bank_id
    string test_id FK
    timestamptz executed_at
    enum result "PASS|PARTIAL|FAIL|NOT_TESTED"
    string evidence_s3_uri
    string executor
    string run_log_s3_uri
    text exception_text
  }
  findings {
    uuid finding_id PK
    string bank_id
    enum type "MRA|MRIA|MRBA|VIOLATION|OBSERVATION"
    enum severity "high|medium|low"
    string regulatory_source
    text concern "5C1"
    text cause "5C2"
    text consequence "5C3"
    text corrective_action "5C4"
    text commitment "5C5"
    text root_cause
    bool root_cause_identified
    uuid owner_person_id FK
    date identified_date
    date due_date
    enum status "open|past_due|pending_validation|validated_closed"
    string evidence_s3_uri
    text validation_notes
    bool repeat_flag
    uuid repeat_of_finding_id FK "self"
  }
  finding_history {
    bigint id PK
    uuid finding_id FK
    timestamptz changed_at
    string changed_by
    string field
    text old_value
    text new_value
  }
  change_record_dispositions {
    string change_ref "from P1 sync"
    string bank_id
    enum disposition "acknowledged|requires_action|not_applicable"
    uuid owner_person_id FK
    string impl_link
    enum status
    timestamptz dispositioned_at
    string dispositioned_by
    string vocab_version
  }
  ofac_validations {
    uuid validation_id PK
    string bank_id
    date performed_on
    uuid performed_by_person_id FK
    text scope
    text methodology
    text findings_summary
    date next_due
    string attestation_s3_uri
  }
  export_artifacts {
    uuid artifact_id PK
    string bank_id
    enum type
    date as_of
    enum format "pdf|csv"
    char7 version_hash
    string s3_bucket
    string s3_key
    string s3_etag
    string s3_version_id
    string kms_key_arn
    bigint byte_size
    char64 sha256
    string generated_by
    timestamptz generated_at
    timestamptz object_lock_retain_until
    char64 snapshot_canonical_sha256
    string source_data_uri
    string template_version
    string vocab_version
    string app_commit
    enum status "pending_render|rendered|sealed"
  }
  export_signoffs {
    uuid signoff_id PK
    uuid artifact_id FK
    uuid signer_person_id FK
    enum signer_role
    timestamptz signed_at
    enum signature_method "sso_session|jwt_jti|manual_upload"
    inet ip_address
    string user_agent
    string jwt_jti
  }
  export_access_log {
    uuid access_id PK
    uuid artifact_id FK
    string accessed_by
    timestamptz accessed_at
    inet ip_address
    string jwt_jti
    int http_status
  }
```

#### 5.2.1 P2 migration list

| # | Group | Purpose | Compliance anchor |
|---|---|---|---|
| 016p2 | A — identity | `bank_profile` | Cover-block source-of-truth (Call Report alignment) |
| 017p2 | A — identity | `bank_officers` | Officer attestation history (OCC Heightened Stds) |
| 018p2 | B — controls | `controls` | Named owner per control (OCC HS) |
| 019p2 | B — controls | `control_rule_mappings` | Coverage = mapped + tested |
| 020p2 | B — controls | `control_tests` + CHECK on `threshold_unit` | Trace columns §4 type 1 |
| 021p2 | B — controls | `test_executions` + CHECK on `result` enum | Vocab lock §2.6 |
| 022p2 | C — findings | `findings` + CHECK on `type` / `status` enums | 5 Cs lock §2.6 |
| 023p2 | C — findings | `finding_history` (trigger-driven) | OCC 2012-7 workpapers |
| 024p2 | D — change | `change_record_dispositions` + CHECK on `disposition` | Vocab §2.6 |
| 025p2 | E — OFAC | `ofac_validations` | OFAC 2019 Framework Pillar 4 |
| 026p2 | F — artifacts | `export_artifacts` UNIQUE(bank,type,as_of,hash) | Idempotency + immutability |
| 027p2 | F — artifacts | `export_signoffs` | E-signature audit chain |
| 028p2 | F — artifacts | `export_access_log` | FFIEC IT Handbook access logging |

---

## 6. UI → API → DB field mapping (per report)

Bold rows are **new** columns added by this design. Plain rows are existing.

### 6.1 Report 1 — `examiner_package`

| Template field (HTML) | UI source | API field | DB column |
|---|---|---|---|
| Institution name | metabox cell | `bank.legal_name` | **`bank_profile.legal_name`** |
| Charter & primary regulator | metabox | `bank.charter_number`, `bank.primary_regulator` | **`bank_profile.charter_number/primary_regulator`** |
| Total assets | metabox | `bank.total_assets_usd` | **`bank_profile.total_assets_usd`** |
| As of | UI date input | `request.as_of` | snapshot timestamp |
| Version `v.XXXXXXX` | hash | `version_hash` | **`export_artifacts.version_hash`** |
| Document ID | computed | `{type}-{as_of}-{hash6}` | derived |
| Prepared by | metabox | officer where role='cco' | **`bank_officers`** |
| Reviewed by | metabox | officer where role='internal_audit' | **`bank_officers`** |
| Obligations in scope | tile | count from snapshot | `compliance_rules` (synced) |
| With tested control | tile | count(rules with PASS test) | `control_rule_mappings` JOIN `test_executions` |
| Untested (gaps) | tile | inverse | derived |
| Trace row: rule_id | mono cell | `rule_id` | `compliance_rules.rule_id` |
| Trace row: regulator | cell | `regulator` | `compliance_rules.regulator` |
| Trace row: citation | mono cell | `cfr_citation` | `compliance_rules.cfr_citation` |
| Trace row: control_id | mono cell | `control_id` | **`controls.control_id`** |
| Trace row: control_owner | cell | officer name | **`bank_officers.full_name`** via `controls.owner_person_id` |
| Trace row: test_id | mono cell | `test_id` | **`control_tests.test_id`** |
| Trace row: threshold | cell | `threshold_value + unit` | **`control_tests.threshold_value/unit`** |
| Trace row: threshold_rationale | (not on row, in CSV) | `threshold_rationale` | **`control_tests.threshold_rationale`** |
| Trace row: result chip | chip cell | latest `test_executions.result` | **`test_executions.result`** |
| Trace row: executed_at | (CSV) | `executed_at` | **`test_executions.executed_at`** |
| Trace row: jira_key | mono cell | `jira_story_key` | **`control_tests.jira_story_key`** |
| Trace row: pr_url | mono cell | `github_pr_url` | **`control_tests.github_pr_url`** |
| Trace row: commit_hash | mono cell | `commit_hash` | **`control_tests.commit_hash`** |
| Trace row: evidence_uri | (CSV) | `evidence_s3_uri` | **`test_executions.evidence_s3_uri`** |
| Exceptions appendix: rule_id, reason | table | join | `compliance_rules` + missing `control_rule_mappings` |
| Exceptions remediation_ref | mono | FK to finding | **`findings.finding_id`** |
| Sign-off block | UI sign button | signoff insert | **`export_signoffs`** |

### 6.2 Report 2 — `ofac_attestation`

| Template field | API field | DB column |
|---|---|---|
| List version + publication time | `list.version_id, published_at` | **`ofac_list_versions`** (P1) |
| Ingested at | `list.ingested_at` | **`ofac_list_versions.ingested_at`** |
| Ingestion lag | derived `ingested_at - published_at` | computed |
| FUZZY/CLEAN/OWN50/FRESH controls | per-control row | `control_tests` filtered by `rule_id LIKE 'OFAC-SCREEN-%'` |
| Customers screened | from `screening_events` | `screening_events` aggregate |
| Beneficial owners | snapshot | `screening_events` group by `party_role='beneficial_owner'` |
| SDN entries / variants / embedded | engine coverage | `ofac_sdn_vectors` aggregate |
| Algorithm | constant | env var |
| Blocked / rejected | counts | `ofac_screenings.action` aggregate |
| Independent validation date + by whom | metabox | **`ofac_validations`** (P2) |

### 6.3 Report 3 — `board_report`

| Template field | DB column |
|---|---|
| Readiness score | derived from `compliance_rules` + `test_executions` (existing readiness endpoint) |
| Coverage % trend | compare current snapshot vs prior `export_artifacts.snapshot_canonical_sha256` |
| OFAC SLA | `ofac_list_versions` lag |
| Changes signed off | `regulatory_change_records.signed_off_at` count |
| Findings by severity | `findings` group by severity |
| SAR / CTR metrics | aggregate from existing `validation_api` results |
| Approval block | `export_signoffs` for CRO + Audit Committee |

### 6.4 Report 4 — `mra_tracker`

Every column maps 1:1 to a `findings` field. CSV columns exactly match the
prompt §4 type 4 list. The 2-3 "top findings expanded" section uses the same
`findings` rows but renders the 5 Cs as a card layout.

### 6.5 Report 5 — `change_log`

| Template field | DB column |
|---|---|
| change_ref, source, citation | synced `regulatory_change_records.*` |
| published_date / effective_date | `regulatory_change_records.created_at` |
| detected_date | `regulatory_change_records.created_at` |
| summary | `change_summary` |
| impact (affected control/policy) | derived from `control_rule_mappings WHERE rule_id=...` |
| disposition | **`change_record_dispositions.disposition`** |
| owner | **`change_record_dispositions.owner_person_id`** |
| impl_link | **`change_record_dispositions.impl_link`** |
| status | **`change_record_dispositions.status`** |

### 6.6 Report 6 — `coverage_gap`

Inverse of report 1. Same tables, different selection criteria
(`mapping_strength != 'full' OR latest test_executions.result NOT IN ('PASS')`).

---

## 7. Business logic per stage

### 7.1 Snapshot (`src/exports/snapshot.py`)

- Open a single read-only transaction per export: `BEGIN ISOLATION LEVEL REPEATABLE READ`.
- For point-in-time `as_of < today`: use stored snapshot ID from a daily
  pg_export_snapshot job (`export_snapshots` table — every nightly cron exports
  one snapshot id and retains it for 7 years). For `as_of == today`: live read.
- Every gatherer returns a `dict` matching a `pydantic` model in
  `src/exports/models/{type}.py` — these models are the contract that the
  template, CSV writer, and hash all share.

### 7.2 Canonical hash (`src/exports/canonical.py`)

```python
def canonical_bytes(payload, *, template_version, vocab_version, app_commit):
    envelope = {
        "payload": payload,
        "template_version": template_version,
        "vocab_version": vocab_version,
        "app_commit": app_commit,
    }
    return json.dumps(envelope, sort_keys=True, default=_stable_default).encode()

def version_hash(bytes_) -> str:
    return hashlib.sha256(bytes_).hexdigest()[:7].upper()
```

`_stable_default` formats `Decimal` to 2dp, dates to `YYYY-MM-DD`, datetimes to
ISO-8601 Z. Same payload, same hash — guaranteed.

### 7.3 Vocabulary enforcement (`src/exports/vocabulary.py`)

```python
class Decision(str, Enum):  # §2.6
    PASS = "PASS"; REVIEW = "REVIEW"; HOLD = "HOLD"

class FindingStatus(str, Enum):
    OPEN = "open"; PAST_DUE = "past-due"
    PENDING_VALIDATION = "pending-validation"
    VALIDATED_CLOSED = "validated-closed"

class ChangeDisposition(str, Enum):
    ACKNOWLEDGED = "acknowledged"
    REQUIRES_ACTION = "requires_action"
    NOT_APPLICABLE = "not_applicable"

# Render-time guard:
def assert_vocab(payload):
    for row in payload.get("trace", []):
        Decision(row["result"])          # raises -> 500 before render
```

DB CHECK constraints in migrations 020p2-024p2 mirror these enums (triple-lock).

### 7.4 Render (`src/exports/renderer.py`)

```python
async def render_pdf(template_name, payload, footer_ctx) -> bytes:
    html = jinja_env.get_template(template_name).render(**payload)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        pdf = await page.pdf(
            format="Letter",
            print_background=True,
            margin={"top": "0.5in", "bottom": "0.7in", "left": "0.5in", "right": "0.5in"},
            display_header_footer=True,
            footer_template=footer_html(footer_ctx),   # institution · type · as_of · v.XXX · x/y
        )
        return pdf
```

Fonts vendored at `dashboard/public/fonts/` (Spectral, IBM Plex Sans, IBM Plex
Mono) and referenced via `@font-face` in `_base.html` so render is deterministic
offline (no Google Fonts dependency at render time).

### 7.5 S3 WORM PUT (`src/exports/storage.py`)

```python
def put_artifact(bytes_, *, bank_id, type_, as_of, hash_, fmt):
    key = f"sealed/{bank_id}/{type_}/{as_of}/{hash_}.{fmt}"
    s3.put_object(
        Bucket=f"fortress-p2-exports-{bank_id}-{env}",
        Key=key,
        Body=bytes_,
        ContentType={"pdf":"application/pdf","csv":"text/csv"}[fmt],
        SSEKMSKeyId=kms_key_arn(bank_id),
        ServerSideEncryption="aws:kms",
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=datetime.utcnow() + timedelta(days=1825),
        IfNoneMatch="*",   # idempotent: 412 PreconditionFailed if already exists
    )
```

### 7.6 Sign-off (`src/exports/signoff.py`)

- Required signers per type are defined in `src/exports/policy.yaml` (e.g.
  `examiner_package: [cco, internal_audit]`, `ofac_attestation: [bsa_officer, cro]`).
- Each `POST /sign` insert is checked against the artifact's required-signers
  list; status flips to `sealed` only when all required roles signed.
- A second-render pass re-stamps the PDF's signoff block with the actual names,
  dates, and IPs — but the **canonical hash is unchanged** because signatures
  live outside the canonical payload (they're metadata about the artifact, not
  part of the program-state being attested to).

### 7.7 Download (`src/exports/api.py`)

```python
@router.get("/exports/{id}/download")
async def download(id, user=Depends(require_approver)):
    art = await db.get(ExportArtifact, id)
    if not art or art.bank_id != user.bank_id: raise HTTPException(404)
    if art.status != "sealed": raise HTTPException(409, "awaiting signoff")
    body = s3.get_object(Bucket=art.s3_bucket, Key=art.s3_key)["Body"].read()
    await db.execute(insert(ExportAccessLog).values(
        artifact_id=id, accessed_by=user.email,
        ip_address=request.client.host, jwt_jti=user.jti, http_status=200,
    ))
    return StreamingResponse(BytesIO(body), media_type=art.content_type)
```

---

## 8. UI changes (`dashboard/src/pages/Reports.jsx`)

```mermaid
flowchart TD
  T[Tile grid - 6 types] --> G[Generate button]
  G --> POST["POST /exports"]
  POST --> POLL["Poll GET /exports/{id} until status='rendered'"]
  POLL --> ROW["New row appears in History table:<br/>type | as_of | v.hash | status | actions"]
  ROW --> SIGN["Sign button - opens modal asking signer role"]
  SIGN --> POSTSIGN["POST /exports/{id}/sign"]
  POSTSIGN --> POLL2["Status=sealed when all sigs in"]
  POLL2 --> DL[Download button enabled]
  DL --> GET["GET /exports/{id}/download<br/>binary PDF/CSV, true a.download"]
```

Key changes:
- Remove `window.open()` + `window.print()` (lines 53-55 of current `Reports.jsx`).
- New `<History>` sub-component showing rolling list of `export_artifacts` for
  the bank, with status chips that match the design-system chip palette.
- New `<SignoffModal>` shown when status is `rendered` but user is in
  `required_signers` and hasn't signed yet.
- Tile click no longer immediately downloads — it queues a render job and the
  user comes back when status is `sealed`.

---

## 9. Banking-compliance control map

| Control | Regulation / framework | Implementation |
|---|---|---|
| WORM 5-yr retention | FFIEC BSA, OFAC Reg §501.601, SEC 17a-4 (where applicable) | S3 Object Lock COMPLIANCE + 1825-day retention; `object_lock_retain_until` mirrored in `export_artifacts` |
| Tamper evidence | OCC 2011-12 SR 11-7 model risk | `snapshot_canonical_sha256` (pre-render) + PDF SHA-256 (post-render) + S3 versioning + source-data JSON stored under WORM |
| Tenant isolation | SOC 2 CC6 / FFIEC CAT | Per-bank S3 bucket, per-bank KMS CMK, `require_approver` enforces `bank_id` match |
| Audit trail | FFIEC IT Handbook §IV (audit), SOX 404 | `export_access_log` + S3 CloudTrail data events -> CW Logs 7-yr retention |
| Dual sign-off | OCC Heightened Standards Appendix D | `export_signoffs` rows for required roles; artifact `status='sealed'` gate before external download |
| Vocabulary lock | OCC 5 Cs (findings), OFAC PASS/REVIEW/HOLD | Triple lock: DB CHECK constraint + Python `Enum` + render-time validator |
| Point-in-time reproducibility | FFIEC IT Handbook §VI, OCC 2010-24 | `pg_export_snapshot()`-based gather + idempotent S3 key |
| Model risk governance | SR 11-7 | `template_version + vocab_version + app_commit` baked into hash envelope; same data + different template = different hash (visible to auditor) |
| Access governance | SOX 404, SOC 2 CC6 | `require_approver` (CCO/CRO); UI hides Sign / Download buttons by role |
| E-signature evidentiary value | ESIGN Act, UETA | `export_signoffs` captures JWT JTI + IP + UA + timestamp; PDF embeds same |

---

## 10. Phased migration plan

```mermaid
gantt
  title Examination Evidence delivery
  dateFormat YYYY-MM-DD
  section P1 (cloud)
  M038 ofac_list_versions          :p1a, 2026-06-23, 2d
  loader writes list version       :p1b, after p1a, 2d
  M039 threshold_default cols      :p1c, 2026-06-30, 3d
  seed thresholds from resolver    :p1d, after p1c, 2d
  M040 enforcement summary cols    :p1e, 2026-07-07, 2d
  M041 disposition vocab version   :p1f, 2026-07-09, 1d
  section P2 (per-bank)
  M016p2-017p2 bank profile        :p2a, 2026-07-14, 2d
  M018p2-021p2 controls + tests    :p2b, after p2a, 4d
  M022p2-023p2 findings            :p2c, after p2b, 3d
  M024p2 change dispositions       :p2d, after p2c, 2d
  M025p2 ofac validations          :p2e, after p2d, 1d
  M026p2-028p2 artifact tables     :p2f, after p2e, 3d
  section Code
  exports package scaffold         :c1, after p2f, 3d
  examiner_package end-to-end      :c2, after c1, 5d
  ofac_attestation                 :c3, after c2, 4d
  board_report                     :c4, after c3, 3d
  mra_tracker                      :c5, after c4, 3d
  change_log                       :c6, after c5, 3d
  coverage_gap                     :c7, after c6, 3d
  section UI
  Reports.jsx rewrite + history    :u1, after c1, 4d
  Signoff modal                    :u2, after u1, 2d
  section Infra
  Per-bank S3 + KMS CFN            :i1, after p2f, 2d
  exports-worker ECS task          :i2, after i1, 2d
  Playwright Dockerfile + image    :i3, after i2, 2d
```

Each `cN` task is a self-contained PR per `prompt.md §1` (one type per PR), with
the §5 tests (vocabulary, determinism, immutability, required sections).

### 10.1 Revised PR sequence (with blocker mapping)

| PR | Pipeline | Scope | Open-question blockers |
|---|---|---|---|
| #1 | P1 | M038 + `ofac_list_versions` loader change | Q1, Q15 |
| #2 | P1 | M039 threshold-default cols + seed | Q1, Q6 |
| #3 | P1 | M040 enforcement summary + M041 disposition vocab version | Q1, Q16, Q18 |
| #4 | P2 | M016p2–028p2 (atomic schema for exports) | Q2 |
| #5 | Infra | Per-bank S3 + KMS CFN, exports-worker ECS, Dockerfile | Q3, Q4, Q7, Q8, Q21 |
| #6 | Code | `src/exports/{snapshot, canonical, vocabulary, storage, signoff, renderer}` + `_base.html` | Q9, Q10, Q11, Q12, Q17, Q18, Q19 |
| #7 | Code | Type 1 `examiner_package` end-to-end (template + CSV + tests) | Q14 |
| #8 | Code | Type 2 `ofac_attestation` (template + CSV + tests) | — |
| #9–#12 | Code | Types 3–6 (`board_report`, `mra_tracker`, `change_log`, `coverage`) — one PR each | Q5 (mra_tracker) |
| #13 | UI | `Reports.jsx` rewrite + history list + signoff modal | — |
| #14 | Obs | CloudTrail data events + CW alarms + quarterly drill test | Q22, Q23, Q24 |
| #15 | Docs | Bank onboarding runbook additions + `CLAUDE.md` memory entries | Q20 |

---

## 11. Open questions — RESOLVED 2026-06-21

All 24 decisions are locked. The 6 genuine forks (Q1, Q2, Q5, Q9, Q12, Q16)
were answered by product/eng on 2026-06-21; the other 18 are adopted as the
recommended defaults below. Implementation proceeds against this set; any
deviation requires a doc amendment.

### 11.1 Locked decisions (Q1, Q2, Q5, Q9, Q12, Q16)

| # | Decision | Resolved | Notes |
|---|---|---|---|
| Q1 | **P1 migrations 038–041 — PROCEED.** Additive nullable cols + new tables. | 2026-06-21 | Each migration ships with a `python -m alembic stamp <prev_head>` rollback recipe per `feedback_p1_alembic_blocks_rollback`. |
| Q2 | **P2 migrations 016p2–028p2 — ONE ATOMIC PR.** Schema-only, no code. | 2026-06-21 | Rollback = single `alembic downgrade -13`. |
| Q5 | **Findings backfill — OPTION A (ship empty, accumulate forward).** | 2026-06-21 | Pilot bank only. Hybrid CSV-import path added when second bank onboards. |
| Q9 | **Signer matrix locked** (see §11.5 below). | 2026-06-21 | Codified in `src/exports/policy.yaml`. CCO may amend post-rollout. |
| Q12 | **`board_report` quarter = FISCAL** (driven by `bank_profile.fiscal_year_end`). | 2026-06-21 | Mid-quarter `as_of` renders as "FQ2 partial" with preliminary notice. |
| Q16 | **Bedrock enforcement summariser — APPROVED (all three).** P1-only backfill with $50/batch cap; inline summarisation on new rows during ingestion; pre-flight script `scripts/setup/verify_bedrock_ready.py` added to bank-onboarding runbook. | 2026-06-21 | Fallback: `summary_pretty` NULL → renderer uses raw `summary`. |

### 11.2 Adopted defaults (Q3, Q4, Q6–Q8, Q10, Q11, Q13–Q15, Q17–Q24)

The following 18 carry the §11 recommendations forward as accepted defaults:

| # | Adopted default | PR landing |
|---|---|---|
| Q3 | Separate `exports-worker` ECS task def (Chromium isolated from API image) | #5 |
| Q4 | Per-bank S3 bucket `fortress-p2-exports-{bank}-{env}` | #5 |
| Q6 | **Threshold seed (M039) — GREEN-LIGHT.** `compliance_rule_resolver.py` constants migrate to `compliance_rules.threshold_default`; resolver reads DB. | #2 |
| Q7 | S3 Object Lock = **COMPLIANCE** mode, 1825-day default retention | #5 |
| Q8 | Per-bank KMS CMK; bank can revoke on offboarding | #5 |
| Q10 | **Strong signing** (typed full name + acknowledgement checkbox + JWT JTI) for `examiner_package` + `ofac_attestation`; JTI-only for others | #6 |
| Q11 | Document ID = deterministic `{type}-{as_of}-{hash6}` (no counter table) | #6 |
| Q13 | Snapshot-history: daily `pg_export_snapshot()` cron + retention table; lands as PR #6.1 | #6.1 |
| Q14 | Audit `workflow_executions.artifacts` JSON for `commit_hash` / `pr_url` / `jira_key` before PR #7; wire agents to write them if missing | #7 |
| Q15 | Loader parses `Publish_Date` / `Version` from `SDN_ADVANCED.XML`; fallback to source-URL `Last-Modified` header | #1 |
| Q17 | Sample-note strip: `{% if not prod %}` block in `_base.html`; test asserts absence in prod renders | #6 |
| Q18 | Vocab-version registry: new table `vocab_versions(version_id, frozen_at, vocab_json)` as **M029p2** (appended to PR #4) | #4 |
| Q19 | Regulator → 5 Cs mapping in `src/exports/regulator_mapping.py`; one rule set per primary regulator; CCO review before PR #6 ships | #6 |
| Q20 | Bank-onboarding runbook updates: per-bank CMK, S3 bucket, `bank_profile` seed, officer roster, control catalogue, Bedrock pre-flight | #15 |
| Q21 | PDF streams through API (`GET /exports/{id}/download`); pre-signed S3 URLs are not exposed externally | #6 |
| Q22 | CloudTrail data events on exports bucket → CW Logs, 7-year retention | #14 |
| Q23 | Three CW alarms (render-failure rate, sign-off SLA breach, retention near-expiry), all with dimensioned + dimensionless variants per `feedback_cw_alarm_dimension_split` | #14 |
| Q24 | Quarterly re-attestation drill: cron tests past `as_of` returns stored artifact, not a new render; Slack post on drift | #14 |

### 11.3 Note: Q2 + Q18 combined

PR #4 lands **M016p2–029p2** (14 migrations, not 13) — the originally
scoped 13 plus `vocab_versions` from Q18. Schema is still atomic for
rollback (`alembic downgrade -14`).

### 11.4 Note: Q9 (signer matrix detail)

Locked matrix (`src/exports/policy.yaml`):

| Report type | Required signers |
|---|---|
| `examiner_package` | CCO + Internal Audit (dual) |
| `ofac_attestation` | BSA Officer + CRO (dual) |
| `board_report` | CCO (single) |
| `mra_tracker` | CCO (single) |
| `change_log` | CCO (single) |
| `coverage` | CCO (single) |

Dual-signer artifacts require 2 rows in `export_signoffs` before
`GET /download` unlocks the final (un-watermarked) PDF.

---

## 12. References

- `dashboard/prompt.md` — authoritative spec
- `dashboard/0[1-6]*.html` — gold templates (visual contract)
- `dashboard/src/pages/Reports.jsx` — current UI
- `src/api/dashboard_api.py:1037-1366` — current export code
- `src/models/database.py` — current ORM
- `CLAUDE.md` — migrations 007-037 history
- `docs/runbooks/agent-4-sync.md` — P1 -> P2 sync mechanics
- OFAC 2019 Framework, OCC Heightened Standards Appendix D, FFIEC BSA Manual,
  SR 11-7, ESIGN Act / UETA
