# Fortress Compliance Platform — Business Analyst Validation Guide

**Audience:** Business Analyst validating data, rule extraction, and workflow consistency
against domain understanding (BSA/AML, OFAC, CFPB, OCC, FFIEC).
**Last refreshed:** 2026-06-29 (commit on branch `feat/ftr-readiness-phase1`).
**How to use:** Each section gives the business *why*, the system *how*, the **example
data** you can paste into the API / inspect in the DB, and a **BA validation
checklist** so you can sign off that what the system claims matches what the
regulator actually requires.

> Every claim below is backed by a `file:line` citation. If you find a discrepancy
> between this guide and the running system, file an item against `docs/ftr_tracker.md`
> and flag the citation — the citation is the contract.

---

## Table of contents

1. [Business context — who uses Fortress and why](#1-business-context)
2. [Source layer — where regulatory data comes from](#2-source-layer)
3. [Scrape orchestration — how content lands in P2 Postgres](#3-scrape-orchestration)
4. [Rule extraction — how prose becomes a `compliance_rules` row](#4-rule-extraction)
5. [Obligations — sentence-level "shall / must / prohibited"](#5-obligations)
6. [Compliance Rule Resolver — keyword → rule → threshold path](#6-rule-resolver)
7. [Regulatory Change Inbox — detection through CCO sign-off](#7-change-inbox)
8. [OFAC SDN — vector DB, API integration, screening evidence](#8-ofac-sdn)
9. [Validators (Wire + Loan) — end-to-end decision traces](#9-validators)
10. [Examination & audit — what examiners ask for, what Fortress produces](#10-examination)
11. [Agent pipeline (JIRA → PR) — how new tests get written](#11-agent-pipeline)
12. [BA daily-validation cheat-sheet](#12-cheatsheet)

---

<a id="1-business-context"></a>
## 1. Business context — who uses Fortress and why

Fortress runs as **two pipelines in one repo** (see `CLAUDE.md`):

| Pipeline | Persona | Surface | Job-to-be-done |
|---|---|---|---|
| **P1 — Fortress Cloud** | Fortress ops | Scraper + sync API (`src/main_pipeline1.py`) | Ingest US federal + state regulation daily, publish rule deltas to subscribed banks |
| **P2 — Bank instance** | Bank CCO / Compliance Analyst / BSA Officer | Validators + CCO dashboard (`src/main_pipeline2.py`) | Block bad wires, screen OFAC, run controls, prove to examiners that the bank is compliant |

The five real business jobs the BA will validate:

1. **Did we ingest the right regulation?** (§2–§4)
2. **Is the rule extracted correctly into a machine-actionable shape?** (§4–§6)
3. **When a regulation changes, does the CCO know and can they sign off?** (§7)
4. **Can a bank block a sanctioned counterparty in real time and prove it?** (§8–§9)
5. **When OCC/FDIC/state examiners show up, can the bank produce evidence?** (§10)

---

<a id="2-source-layer"></a>
## 2. Source layer — where regulatory data comes from

### 2.1 Connectors (live in `src/connectors/regulatory/usa/`)

**Federal — 12 connectors** (live-scraped unless noted)

| Connector | URL / source | Document type | Cadence | Notes |
|---|---|---|---|---|
| OCC BSA | occ.gov/publications-and-resources/publications/comptrollers-handbook/ | CFR 31 §1020.210, dual-approval rules | 24 h | `src/connectors/regulatory/usa/occ_bsa.py:51` |
| FFIEC BSA | bsaaml.ffiec.gov (CF-bot gated) | CFR 31 §1020.210, §314(a/b) | hardcoded | Cloudflare blocks scraping — hardcoded in `_build_regulations()` (memory `project_ffiec_cf_gated`) |
| FFIEC Call Report | ffiec.gov | Quarterly reporting schema | 24 h | `ffiec_call_report.py` |
| FFIEC Cybersecurity | ffiec.gov/cybersecurity.htm | Examination guidance | 24 h | `ffiec_cybersecurity.py` |
| OCC IRR | occ.gov/bulletins/ | CFR 12 §30 | 24 h | `irr.py` |
| OCC Liquidity / Op Risk / CRE | occ.gov | LCR, op-risk, CRE concentration | 24 h | three separate connectors |
| FDIC Consumer Compliance | fdic.gov/consumer-compliance-examination-manual/ | 31 CFR §§1000-1090, Reg DD | 24 h | `fdic.py:25` |
| CFPB | consumerfinance.gov/rules-policy/ | Section 1071, Reg E (60–90 d dispute) | 24 h | `cfpb.py` |
| FinCEN SAR + CTR | fincen.gov/resources/filing-information | 31 USC §§5316, 5318 — CTR $10k, SAR $5k, 30-day filing | 24 h | `fincen_sar.py:21` |
| OFAC SDN | sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML | SDN XML feed | 24 h | `ofac_sdn.py` (raw XML retained in S3 `scraper-chunks/sdn_xml_raw/`) |
| CRA | occ.gov/topics/supervision-and-examination/cra/ | 12 CFR Part 25 | 24 h | `cra.py` |
| FFIEC Peer Benchmarks | banks.data.fdic.gov/api/ | Call-report peer percentiles | 24 h | `peer_benchmarks.py` |

**State — 5 connectors** (`src/connectors/regulatory/usa/states/`)

| Connector | Authority | Document type |
|---|---|---|
| CA DFPI | `ca_dfpi.py` | CA Finance Code, DFI exam procedures |
| NY DFS | `nydfs.py` | 23 NYCRR §500 (cyber), NYDFS BSA-AML manual |
| TX DOB | `tx_dob.py` | TX Finance Code §59.001+ |
| FL OFR | `fl_ofr.py` | FL Statutes §655 |
| IL IDFPR | `il_idfpr.py` | IL Consumer Fraud Act + lending |

### 2.2 BA validation — source layer

- [ ] Open `src/connectors/regulatory/usa/fincen_sar.py:21` — confirm CTR threshold is `$10,000` and SAR `$5,000`. These must match 31 USC §5318(g) / §5316.
- [ ] Open `src/connectors/regulatory/usa/states/nydfs.py` — confirm 23 NYCRR §500 cybersecurity sections are referenced (the bank's NYDFS examiner will check this).
- [ ] In **P2 Postgres** (the only DB you have access to — see §12.0 for connection steps): `SELECT regulator, COUNT(*) FROM compliance_rules GROUP BY regulator;` — expect rows for `occ`, `cfpb`, `fincen`, `ofac`, `fdic`, `nydfs`, `ca-dfpi`, `tx-dob`, `fl-ofr`, `il-idfpr`, plus FFIEC. (The rules are scraped into Fortress Cloud's database upstream, then daily-synced into your P2 Postgres by `UpdateService` — you validate the post-sync state.)
- [ ] Cross-check the **FFIEC** rows are populated even though the site is CF-gated — they come from `_build_regulations()` and should still be present.

---

<a id="3-scrape-orchestration"></a>
## 3. Scrape orchestration — how content lands in P2 Postgres

`src/services/scraper_scheduler.py` is the orchestrator. The flow (lines 104–359, 652–908):

```
Connectors (n=17)
    │
    ▼
Raw payload → S3   s3://fortress-p1-data/raw-scrapes/{regulator}/{date}/  (+manifest.json, SHA-256)
    │
    ▼
SHA-256 hash compare on rule_text (excluding timestamps)   scraper_scheduler.py:652
    │     ┌──────────── unchanged ───────────┐
    │     │                                  │  skip (no DB write)
    │     ▼
    │  changed → upsert ComplianceRule       (scraper_scheduler.py:663–717)
    │           append to connector_changes_log
    │
    ▼
ChangeManager.bulk_record_changes(...)      (scraper_scheduler.py:900–908)
    │           writes regulatory_change_records row per material change
    ▼
_embed_and_store_rules()                    (scraper_scheduler.py:911–975)
    │           Bedrock Titan Embed v2 (1024-dim) for rules where embedding IS NULL
    │           writes pgvector embedding + tsvector tsv (BM25)
    ▼
MCP scrape_runs row + CloudWatch metric    (migration 018)
```

**S3-first storage** keeps the raw scrape forever (gzip + KMS), so any later "did we
parse this correctly?" question can be re-litigated against the original bytes.

**`scrape_runs` table (migration 018)** is the BA's flight recorder:

```sql
SELECT connector, status, rules_changed, error_message, started_at
FROM   scrape_runs
WHERE  started_at >= NOW() - INTERVAL '24 hours'
ORDER  BY started_at DESC;
```

A healthy day looks like all 17 federal + 5 state connectors with `status='success'`
and `rules_changed >= 0`. Failure rows should always include `error_message`.

### 3.1 BA validation — orchestration

- [ ] **Health:** `make logs-p1 | grep "scrape_complete"` — should fire once/day per connector.
- [ ] **S3 retention:** `aws s3 ls s3://fortress-p1-data/raw-scrapes/ofac/ --recursive | tail -7` — last 7 days of OFAC dumps must be present.
- [ ] **Idempotence:** Re-run the same scrape twice; `compliance_rules.hash` must not change for unchanged content (the second run produces zero `regulatory_change_records`).

---

<a id="4-rule-extraction"></a>
## 4. Rule extraction — how prose becomes a `compliance_rules` row

### 4.1 Two-tier extraction

Documented in `docs/runbooks/scraper-extraction-architecture.md`.

**Tier A — Deterministic** (preferred, no LLM cost):
- CFR regex (e.g., `12 C\.F\.R\. § \d+\.\d+`)
- OFAC XML struct walk
- pdfplumber text + heading regex
- pandas + Pydantic for XLSX call-report schedules

**Tier B — LLM + Pydantic tool_use** (only when deterministic fails):
- `src/extraction/bedrock_extractor.py` returns a `ExtractionResult`:

```python
class ExtractionResult(BaseModel):
    confidence: float                    # 0..1; caller gates at <0.5
    extraction_method: Literal["llm", "regex", "hardcoded", "mock"]
    source_span: str                     # verbatim quote, ≤1000 chars
    reasoning: str                       # one-line explanation, ≤2000 chars
    class Config: extra = "forbid"       # forces JSONSchema additionalProperties:false
```

We use **Bedrock `tool_use`** (not free-form JSON) so the LLM cannot leak markdown
or hallucinate fields — `extra="forbid"` flips `additionalProperties:false` on the
generated JSON-schema, and Bedrock enforces it before the model can answer.

### 4.2 The `compliance_rules` schema you'll inspect

`src/models/database.py:82–120` (`ComplianceRule`):

| Column | Type | Where it comes from | BA-visible meaning |
|---|---|---|---|
| `rule_id` | str(100) | connector | Unique per (regulator, geography) |
| `regulator` | str(50) | connector | `occ`, `cfpb`, `fincen`, `ofac`, `nydfs`, ... |
| `category` | str(100) | connector | `bsa_aml`, `section_1071`, `wire_transfer`, `kyc_cdd` |
| `domain` | str(100) | connector | `wire_transfer`, `loan`, `account_opening` |
| `rule_type` | str(50) | connector | `dual_approval`, `ctr_threshold`, `sar_filing` |
| `rule_text` | text | scrape | Full prose — the source-of-truth for human review |
| `cfr_citation` | text | extractor (mig 013) | e.g. `31 C.F.R. § 1020.210` |
| `state_code` | str(20) | connector (mig 014) | `CA`, `NY-DFS`, `TX` |
| `jurisdiction_level` | str(20) | connector (mig 013) | `federal` / `state` / `internal` |
| `enforcement_risk_score` | float | EnforcementRiskScore join (mig 009) | 0..1 — derived from past CMPs |
| `obligation_count` | int | denorm of `obligations` (mig 013) | Number of "shall / must / prohibited" clauses |
| `threshold_amount` | numeric | extractor | Dollar threshold if rule is quantitative |
| `effective_date` | timestamp | connector | When the rule went live |
| `source_url` | text | connector | Original page/PDF |
| `hash` | str(64) | scheduler | SHA-256 over `rule_text` for change detection |
| `version` | bigint | scheduler | Global sync version (P2 uses for delta pull) |
| `is_active` | bool | scheduler | `false` = tombstoned (soft delete) |
| `embedding` | `vector(1024)` | embed worker (mig 023) | Titan v2 dense vector for pgvector ANN |
| `tsv` | tsvector | trigger (mig 023) | BM25-style full-text index |
| `change_delta` | jsonb | ChangeManager (mig 037) | Per-record diff at last change — see §7 |
| `previous_content` | text | scheduler | Old `rule_text` for audit |

### 4.3 Worked example — a CTR rule

Source: FinCEN — "Each financial institution shall file a CTR for every cash
transaction equal to or greater than $10,000."

After scrape + extraction:

```jsonc
{
  "rule_id": "FINCEN-CTR-001",
  "regulator": "fincen",
  "category": "bsa_aml",
  "domain": "wire_transfer",
  "rule_type": "ctr_threshold",
  "rule_text": "Each financial institution shall file a CTR for every cash transaction equal to or greater than $10,000.",
  "cfr_citation": "31 C.F.R. § 1010.311",
  "jurisdiction_level": "federal",
  "threshold_amount": 10000.00,
  "effective_date": "1987-10-27T00:00:00Z",
  "source_url": "https://www.fincen.gov/resources/filing-information",
  "hash": "<sha256 of rule_text>",
  "version": 142,
  "is_active": true,
  "obligation_count": 1
}
```

### 4.4 BA validation — extraction

- [ ] `SELECT rule_id, threshold_amount, cfr_citation FROM compliance_rules WHERE rule_type='ctr_threshold';` — expect `$10,000` and citation `31 C.F.R. § 1010.311`.
- [ ] `SELECT rule_id, rule_text FROM compliance_rules WHERE rule_type='dual_approval';` — expect prose with "dual approval" and threshold `$10,000`.
- [ ] `SELECT rule_id, threshold_amount, cfr_citation FROM compliance_rules WHERE category='kyc_cdd' AND rule_type LIKE '%beneficial%';` — expect **20%** (per FinCEN CHG-2026-0412 of April 11 2026, memory'd; was 25% pre-rule).
- [ ] Spot-check 10 random rows: does the `cfr_citation` actually appear inside `rule_text`? If not, the extractor missed it.
- [ ] **Golden set:** `tests/golden/extraction/enforcement_penalty.jsonl` contains 12 sealed cases (e.g., Capital One $480B *assets* must NOT become a $480B penalty; OCC $1.5M CMP + $300K restitution must sum to $1.8M). Run `pytest tests/golden -k extraction` and inspect failures — these are the regression tripwires.

---

<a id="5-obligations"></a>
## 5. Obligations — sentence-level "shall / must / prohibited"

A compliance rule is paragraph-level; an **obligation** is a single actionable
sentence the bank can be tested against. Migration 008 introduced the table.

### 5.1 Schema (`src/models/database.py:385–404`)

| Column | Meaning | Example |
|---|---|---|
| `obligation_id` | unique key | `OBL-FINCEN-CIP-001` |
| `regulation_id` | FK to source regulation | `FINCEN-BSA-CIP` |
| `regulator` | originating body | `fincen` |
| `category` | grouping | `bsa_aml` |
| `source_text` | verbatim sentence | "Institutions shall establish customer identification procedures." |
| `obligation_type` | classifier | `shall` / `must` / `required` / `prohibited` / `may_not` |
| `section_reference` | regulation section | `§1020.220(a)` |
| `cfr_citation` | full citation | `31 C.F.R. § 1020.220` |
| `has_test_coverage` | bool | `false` until ExecutionAgent marks it `true` |
| `test_scenario_ids` | list[str] | `["WIRE-DUALAPPR-001","WIRE-DUALAPPR-002"]` |
| `is_active` | bool | soft-delete |

### 5.2 Extraction logic — `src/services/obligation_extractor.py:210–250`

1. Regex first: `_OBLIGATION_TRIGGERS` splits `rule_text` on sentence boundaries and matches verbs (`shall`, `must`, `required`, `prohibited`, `may not`).
2. CFR cite via `_CFR_RE`.
3. **LLM fallback** is opt-in via `LLM_EXTRACTION_MODE = off | fallback | shadow`. In `fallback`, the LLM only fills slots regex missed — never overwrites.

### 5.3 Three example obligations the BA should be able to find in P2 Postgres

| obligation_type | source_text | cfr_citation |
|---|---|---|
| `shall` | "Institutions shall establish customer identification procedures." | 31 C.F.R. § 1020.220 |
| `must` | "All banks must file a CTR for cash transactions ≥ $10,000." | 31 C.F.R. § 1010.311 |
| `prohibited` | "Banks may not knowingly enter into SAR-reportable activity." | 31 U.S.C. § 5318(g) |

### 5.4 BA validation — obligations

- [ ] `SELECT obligation_type, COUNT(*) FROM obligations GROUP BY obligation_type;` — `shall` should dominate, with non-zero `must`, `prohibited`, `may_not`.
- [ ] `SELECT cr.rule_id, cr.obligation_count, COUNT(o.id) FROM compliance_rules cr LEFT JOIN obligations o ON o.regulation_id = cr.rule_id GROUP BY cr.rule_id, cr.obligation_count HAVING cr.obligation_count <> COUNT(o.id);` — should be empty (denorm in sync).
- [ ] `SELECT obligation_id, has_test_coverage FROM obligations WHERE has_test_coverage = TRUE;` — non-zero means the agent pipeline (§11) has actually shipped a test.
  > **Known gap** (`docs/examination_readiness_fix_plan.md` Fix 1): `has_test_coverage` is currently never flipped to TRUE by ExecutionAgent. Readiness "testing" dimension stays at 0%. Confirm with the engineer that the fix is in-flight before signing off on this column.

---

<a id="6-rule-resolver"></a>
## 6. Compliance Rule Resolver — keyword → rule → threshold path

`src/services/compliance_rule_resolver.py:136–185` is the runtime entry point.
Every validator (wire, OFAC, loan) calls it to ask **"for this domain, what's the rule?"**

```
ComplianceRuleResolver.resolve(domain="wire_transfer", rule_type="ctr_threshold")
    │
    ▼  Tier 1 — Postgres exact match
    │   SELECT * FROM compliance_rules
    │   WHERE  domain='wire_transfer' AND rule_type='ctr_threshold'
    │     AND  is_active = TRUE
    │   threshold_amount IS NOT NULL  → authoritative; return.
    │
    ▼  Tier 2 — Redis cache (1 h TTL)
    │   Key: compliance:resolved:wire_transfer:ctr_threshold
    │
    ▼  Tier 3 — pgvector hybrid (semantic + BM25)
    │   SELECT rule_id, rule_text, threshold_amount,
    │     0.7 * (1 - (embedding <=> :query_vec))         -- 70% cosine
    │   + 0.3 * COALESCE(ts_rank(tsv, plainto_tsquery('english', :q)), 0)  -- 30% BM25
    │   FROM   compliance_rules
    │   ORDER BY score DESC
    │   LIMIT 3;
    │
    ▼  Tier 4 — Hardcoded fallback constants
        _FALLBACK_THRESHOLDS[domain][rule_type]
```

### 6.1 The hardcoded fallback table (must match DB)

`compliance_rule_resolver.py:48–74` — the BA must verify these values *in DB*
match this constants block. If they drift the wrong way, real wires get
mis-validated.

```python
"wire_transfer": {
  "ctr_threshold":           10000,   # FinCEN CTR
  "dual_approval_threshold": 10000,   # OCC dual approval
  "travel_rule_threshold":   3000,    # FinCEN Travel Rule
},
"sar": {
  "suspicious_threshold":    5000,    # FinCEN SAR identified
  "insider_threshold":       0,       # SAR for any insider abuse
},
"ctr": {"cash_threshold":    10000},
"ofac": {
  "fuzzy_match_threshold":   85,      # 85% similarity → block
  "reporting_deadline_days": 10,
},
"kyc_cdd": {
  "beneficial_ownership_pct": 20,     # FinCEN CHG-2026-0412 (April 11 2026); was 25%
},
```

### 6.2 BA validation — resolver

Run these queries and compare to the constants table above:

- [ ] `SELECT rule_id, threshold_amount FROM compliance_rules WHERE rule_type='dual_approval' AND is_active;` → must include a row with `threshold_amount = 10000`.
- [ ] `SELECT rule_id, threshold_amount FROM compliance_rules WHERE rule_type='travel_rule_threshold' AND is_active;` → expect `3000`.
- [ ] `SELECT rule_id, threshold_amount FROM compliance_rules WHERE rule_type LIKE '%beneficial%' AND is_active;` → expect `20` (NOT 25 — that's the pre-2026 rule).
- [ ] **Keyword search test:** hit `POST /resolve` (if exposed) or invoke `ComplianceRuleResolver.resolve_by_keyword("dual approval")` in a Python shell. Top-3 must include the OCC dual-approval rule.

---

<a id="7-change-inbox"></a>
## 7. Regulatory Change Inbox — detection through CCO sign-off

This is the workflow the CCO uses every morning. The BA must confirm each step
matches how the CCO actually does the job today.

### 7.1 End-to-end flow

```
Daily scrape
    │
    ▼ SHA-256(rule_text) differs from existing ComplianceRule.hash
    │
    ▼ ComplianceRule row UPDATED in place (new rule_text, new hash, previous_content saved)
    │   scraper_scheduler.py:663–717
    │
    ▼ ChangeManager.bulk_record_changes(...)
    │   src/services/change_manager.py:35–87
    │   • computes _compute_text_diff() → {"added":[...], "removed":[...], "change_pct": n}
    │   • impact_rating = "high" if change_pct≥50%, "medium" ≥15%, else "low"
    │
    ▼ INSERT INTO regulatory_change_records (change_ref, status='detected', change_delta=<JSONB>, …)
    │
    ▼ CCO opens dashboard → Changes tab
    │   GET /api/v1/dashboard/changes              src/api/dashboard_api.py:182–217
    │   GET /api/v1/dashboard/changes/{change_ref} src/api/dashboard_api.py:458–531
    │
    ▼ CCO clicks "Sign off"
    │   POST /api/v1/dashboard/changes/{change_ref}/signoff
    │       body: {"decision": "acknowledged"|"requires_action"|"not_applicable", "note": "..."}
    │   src/api/dashboard_api.py:548–581
    │
    ▼ UPDATE regulatory_change_records
    │   SET status = signed_off | remediation_planned | closed,
    │       signed_off_at = NOW()   (immutable; trigger from migration 011)
    │
    ▼ Downstream — what happens NEXT?
        • P2 banks pull new records via /api/sync/change-records   (UpdateService._sync_change_records)
        • No compliance_rules row is created or mutated by sign-off
        • No JIRA story auto-created  ← see §7.5 GAP
        • No embedding re-compute     ← embeddings already refreshed during the scrape
```

### 7.2 `change_delta` shape

`src/services/change_manager.py:35–65`:

```json
{
  "added":   ["First added sentence.", "Second added sentence."],
  "removed": ["Old threshold sentence."],
  "change_pct": 15.2
}
```

Capped at 20 add + 20 remove so very large diffs don't bloat the JSONB.

### 7.3 What the CCO sees in the inbox (dashboard)

`dashboard/src/pages/Changes.jsx`:

- List columns: **severity badge**, regulator, rule_id, status badge, summary (≤120 chars), diff preview (≤240 chars), change_ref + detected_date.
- Detail drawer: full `change_delta` (added sentences in green, removed in red strikethrough), `rule_text` before/after side-by-side, comments thread (read-only), Sign-off button.
- Sign-off modal dropdown: **acknowledged / requires action / not applicable** + optional note.

### 7.4 Example change record (worked example)

```jsonc
// GET /api/v1/dashboard/changes/CHG-A1B2C3D4
{
  "change_ref": "CHG-A1B2C3D4",
  "rule_id": "FINCEN-CDD-BENEFICIAL-OWNERSHIP",
  "regulator": "FINCEN",
  "change_summary": "Beneficial-ownership reporting threshold reduced from 25% to 20%.",
  "semantic_diff": "ADDED: 20% ownership threshold. | REMOVED: 25% ownership threshold.",
  "change_delta": {
    "added":   ["A legal entity customer with one or more individuals owning 20% or more of the equity interests."],
    "removed": ["A legal entity customer with one or more individuals owning 25% or more of the equity interests."],
    "change_pct": 18.7
  },
  "impact_rating": "high",
  "change_type": "modified",
  "status": "detected",
  "signed_off_at": null,
  "rule": {
    "rule_text": "<full new text>",
    "previous_content": "<full old text>",
    "cfr_citation": "31 C.F.R. § 1010.230",
    "effective_date": "2026-04-11"
  },
  "comments": [],
  "can_signoff": true
}
```

### 7.5 What happens AFTER CCO sign-off — and what does NOT

**What DOES happen** (verified in code):
1. `regulatory_change_records.status` flips to `signed_off | remediation_planned | closed`.
2. `signed_off_at` timestamp set (immutable — the trigger from migration 011 raises on any later UPDATE).
3. P2 banks pull the updated record on their next sync cycle (`UpdateService._sync_change_records`).

**What does NOT happen automatically** (BA must know this):
1. **No new `compliance_rules` row is created.** The new rule text was already
   written to `compliance_rules` *during the scrape* — sign-off is governance
   attestation, not rule publication. The "Tier 1" resolver immediately sees
   the new threshold the moment the scrape completes, regardless of CCO action.
2. **No JIRA story is auto-created** even when CCO picks "requires action".
   The decision is logged in the response body but no agent pipeline kickoff.
   See `examination_readiness_fix_plan.md` for the planned remediation.
3. **The CCO's `note` is not persisted** (a `SignOffBody.note` field exists at
   `dashboard_api.py:538` but the UPDATE clause does not write it to
   `change_record_comments`).
4. **`reviewer_name` and `reviewer_role` columns exist but are not populated**
   by the sign-off endpoint.

**BA must convey to stakeholders:** today, sign-off is an **attestation
checkbox**, not a workflow trigger. Items 2–4 are the next integration sprint.

### 7.6 BA validation — change inbox

- [ ] Force a change: re-run a connector with a small `rule_text` edit and confirm exactly one new `regulatory_change_records` row appears.
- [ ] `SELECT change_ref, impact_rating, change_pct FROM regulatory_change_records ORDER BY created_at DESC LIMIT 5;` — `impact_rating` must reflect the 50/15/0 thresholds.
- [ ] Hit `POST /api/v1/dashboard/changes/{ref}/signoff` with `{"decision":"acknowledged","note":"reviewed by CCO"}`. Verify: `status='signed_off'`, `signed_off_at` set, **and confirm the `note` is currently dropped** (known gap).
- [ ] Confirm a second sign-off attempt fails or no-ops due to the immutable-trigger from migration 011.

---

<a id="8-ofac-sdn"></a>
## 8. OFAC SDN — vector DB, API integration, screening evidence

### 8.1 Why a bank cares

Sanctioned-party screening is **the** non-negotiable BSA/AML control. A miss
means OCC/OFAC enforcement plus criminal exposure. Banks need:
1. **Coverage:** every SDN name + every alias screened, every wire/loan applicant.
2. **Speed:** <100 ms p95 so the bank's wire UI doesn't time out.
3. **Recall:** fuzzy/transliterated names ("Ahmed Al-Rashid" vs "Al Rasheed, A.") must hit.
4. **Provable evidence:** the bank must be able to show OFAC examiners *why* it cleared or held each wire.

### 8.2 Ingest pipeline

`src/services/ofac_sdn_loader.py`:

1. Daily fetch of `SDN_ADVANCED.XML` from sanctionslistservice.ofac.treas.gov, raw copy to S3.
2. Single normalize function (`normalize_name`, line 53–65): NFD → strip combining marks → uppercase → strip punctuation → collapse whitespace. **Example: `"José García-López" → "JOSE GARCIA LOPEZ"`.**
3. Integrity gate (lines 132–154): refuse pull if >2% of entries disappear vs yesterday (`_MAX_DROP_FRACTION = 0.02`) — prevents a corrupt feed from silently un-blocking sanctioned parties.
4. **N-strikes tombstone** (lines 266–346, `_TOMBSTONE_AFTER_MISSES = 2`): an entry must be missing for **2 consecutive pulls** before it's soft-deactivated. Tombstones are logged to `regulatory_change_records` so the CCO sees them.

### 8.3 Vector store — one row per name variant (migrations 021/022)

```
SDN entry 31526 = "ACME HOLDINGS LLC"
  aliases: ["ACME CORP", "A.C.M.E."]
              │
              ▼  expand to ofac_sdn_vectors:
Row 1: sdn_number=31526, name_variant="ACME HOLDINGS LLC", is_primary=TRUE
Row 2: sdn_number=31526, name_variant="ACME CORP",         is_primary=FALSE
Row 3: sdn_number=31526, name_variant="A.C.M.E.",          is_primary=FALSE
```

Each row gets:
- `name_normalized` (the normalize_name output)
- `embedding vector(1024)` (Amazon Titan Embed Text v2 — `amazon.titan-embed-text-v2:0`)
- GIN `pg_trgm` index for fuzzy substring
- GIN `tsv` tsvector index for BM25
- HNSW vector index (migration 022, built CONCURRENTLY *after* embed backfill)

Embed worker `src/jobs/embed_sdn_variants.py`: idempotent — only rows with
`embedding IS NULL AND is_active = TRUE` are processed, 50-row commit chunks,
crash-safe resume.

### 8.4 Screening modes (`OFAC_SCREENER_MODE`)

`src/services/validators.py:381–637`.

**Legacy mode** (default):
- Pulls SDN list, in-memory `difflib.SequenceMatcher`
- Compares primary names only
- HOLD ≥ 0.92, REVIEW 0.85–0.92

**Vector mode** (the strategic path):
- Hybrid SQL on `ofac_sdn_vectors`:
  1. `pg_trgm` trigram top-50 candidates
  2. `tsvector` BM25 boost
  3. Optional Titan v2 query embedding → cosine rerank
- Python re-rank: `0.40 * trgm + 0.40 * fuzz + 0.20 * cosine`
- Dedup by `sdn_number` (multiple alias hits collapse to highest)
- Screens **primary names AND aliases** (legacy does primaries only)

**Staleness gate** (lines 625–637): if `now - last_sdn_load > 24 h`, bump
PASS→REVIEW; >36 h, escalate REVIEW→HOLD. Stops a stale feed from giving
banks false comfort.

### 8.5 Calibration

`scripts/setup/calibrate_ofac_thresholds.py` + `docs/runbooks/ofac-calibration-gold-set.md`.

| Class | Why thresholds differ | Cosine target | Notes |
|---|---|---|---|
| `individual` | 2-token names cluster tightly | high (~0.86) | recall ≥ 0.95 required |
| `entity` | boilerplate ("Holdings", "LLC") inflates baseline | moderate (~0.81) | strong fuzz weight |
| `vessel` | IMO numbers, flag noise → cosine unreliable | low (~0.70) | high trgm weight |
| `alias` | truncated/abbreviated → recall over precision | low | recall ≥ 0.95 required |

Acceptance: per-class F1 ≥ 0.90; thresholds stored in SSM at
`/fortress/p2/ofac/threshold/{class}`.

### 8.6 Bank integration — two paths

**Path A — REST API** (`src/api/validation_api.py:228–248`, `/validate/ofac-screen`)

Request:
```json
{ "party_name": "Ahmed Al-Rashid", "party_country": "AE" }
```

Response (worked example — REVIEW decision):
```json
{
  "decision": "REVIEW",
  "action":   "review",
  "final_score": 0.89,
  "violations": [{
    "regulation": "OFAC Sanctions",
    "rule_id": "OFAC-SDN-31526",
    "description": "Potential SDN match: 'ACME HOLDINGS' (score 0.89, alias=false)",
    "match_detail": {
      "sdn_number": "31526",
      "matched_name": "ACME HOLDINGS LLC",
      "is_primary": true,
      "entity_type": "entity",
      "programs": ["IFSR"],
      "country": "AE",
      "scores": {"trgm": 0.92, "fuzz": 0.87, "vector": 0.88, "final": 0.89}
    }
  }],
  "screening_id": "abc123...",
  "sdn_data_age_hours": 18.5,
  "staleness_bumped": false,
  "latency_ms": 45.2
}
```

Rate limit: 60 req/min/IP (env `FORTRESS_P2_VALIDATE_RPM`), 429 + `Retry-After` header.

**Path B — Pre-computed vector sync** (`POST /api/sync/sdn-vectors`)

A bank running its own pgvector instance can pull per-variant rows + 1024-dim
embeddings and screen locally — useful for offline / sovereign deployments.
See `src/api/sync_api.py` for shape.

### 8.7 Evidence audit — `screening_events` table (migration 028)

Every `OFACScreener.screen()` call writes one append-only row in
`screening_events`. Examiners reach into this table to ask *"show me why
this wire was held"*.

Columns the BA should know:
- `screening_id`, `party_name`, `party_country`, `criteria` (JSONB input)
- `decision` (PASS/REVIEW/HOLD), `final_score`, `engine_mode` (legacy/vector)
- `engine_response` (full ValidationResult JSON — the evidence packet)
- `sdn_data_age_hours`, `latency_ms`
- `actor_kind` (api / dashboard / bulk / monitoring)
- Indexes on `customer_id`, `created_at`, `decision`, `criteria_hash` (dedup)
- Retention: 7 years

A REVIEW or HOLD additionally opens a `screening_cases` row tracked by status
(`open → in_review → cleared / escalated / blocked / closed`), with events
joined via `screening_case_events`.

### 8.8 BA validation — OFAC SDN

- [ ] `SELECT COUNT(*) FROM ofac_sdn_vectors;` — should be **many more** than `SELECT COUNT(*) FROM ofac_sdn_entries;` (1 row per variant, not per SDN).
- [ ] Pick one entry: `SELECT sdn_number, name_variant, is_primary FROM ofac_sdn_vectors WHERE sdn_number = '<pick one>';` — confirm 1 primary + N aliases.
- [ ] `SELECT MAX(embedded_at) FROM ofac_sdn_vectors;` — should be within the last 24 h. If stale, the embed worker is broken.
- [ ] Call `POST /validate/ofac-screen` with a name you know is on SDN (e.g., a stub from the test gold-set). Decision must be HOLD or REVIEW with `violations[].match_detail.sdn_number` present.
- [ ] Re-call the same request — `screening_events` rows must accumulate (one per call), `criteria_hash` identical between calls.
- [ ] Force the staleness path: temporarily set `sdn_data_age_hours = 25` and verify a PASS decision gets bumped to REVIEW.

---

<a id="9-validators"></a>
## 9. Validators (Wire + Loan) — end-to-end decision traces

### 9.1 Wire transfer (`src/services/validators.py:259–350`)

**Request:**
```json
{
  "amount": 15000.00, "currency": "USD",
  "sender_name": "Acme Co",
  "beneficiary_name": "Sergei Ivanovich",
  "beneficiary_country": "RU",
  "approvals": [{"approver": "alice@bank.com", "timestamp": "2026-06-29T10:00:00Z"}],
  "is_international": true
}
```

**Decision logic:** fetches rules by `rule_type IN ('dual_approval','ctr_threshold','international_wire')`, evaluates each against the payload.

**Response (blocked):**
```json
{
  "action": "block",
  "violations": [
    {"regulation": "OCC BSA",
     "rule_id": "OCC-WIRE-DUALAPPR-001",
     "description": "Wire $15,000 ≥ $10,000 requires dual approval. Only 1 approval found.",
     "severity": "high",
     "recommendation": "Require second approver before release."},
    {"regulation": "FinCEN CTR",
     "rule_id": "FINCEN-CTR-001",
     "description": "Cash-equivalent wire ≥ $10,000 triggers CTR filing.",
     "severity": "medium",
     "recommendation": "Auto-file CTR within 15 days."}
  ],
  "latency_ms": 23.1,
  "screening_id": "wire-abc..."
}
```

**Happy path** (amount $5k, 1 approval, domestic): `action="allow"`, `violations=[]`.

### 9.2 Loan application — CFPB Section 1071 (`validators.py:903–987`)

**Request** (small-business loan, demographic fields stay LOCAL — never leave the bank):
```json
{
  "applicant_id": "L-2026-0012",
  "race_of_principal_owner": "Asian",
  "sex_of_principal_owner": "Female",
  "ethnicity_of_principal_owner": "Not Hispanic or Latino",
  "naics_code": "541511",
  "business_gross_revenue": 1200000,
  "time_in_business": 5,
  "number_of_workers": 12,
  "minority_owned_business": true,
  "women_owned_business": true
}
```

**Blocked example** — missing `race_of_principal_owner`:
```json
{
  "action": "block",
  "violations": [{
    "regulation": "CFPB Section 1071",
    "rule_id": "CFPB-1071-DEMO-RACE",
    "description": "Required field 'race_of_principal_owner' is missing or empty.",
    "severity": "high",
    "recommendation": "Collect demographic field before submission."
  }],
  "latency_ms": 4.2
}
```

### 9.3 BA validation — validators

- [ ] Wire happy path returns `action="allow"` in <50 ms.
- [ ] Wire blocked-by-dual-approval names the OCC rule_id and includes severity.
- [ ] Loan blocked-by-missing-demographic cites CFPB 1071 rule_id.
- [ ] For every decision, the response contains `screening_id` and a row exists in `screening_events` (audit trail).

---

<a id="10-examination"></a>
## 10. Examination & audit — what examiners ask for, what Fortress produces

`docs/examination_guidelines.md` is the long-form playbook. This section
distills the examiner-ask → Fortress-artifact mapping the BA must validate.

| Examiner ask | Fortress artifact | Source / endpoint |
|---|---|---|
| "Show me your complete compliance rule inventory" | `compliance_rules` table (rule_text, cfr_citation, enforcement_risk_score, effective_date) | Daily P1 scrape |
| "Which controls address which rules?" | `control_rule_mappings` (control_id, rule_id, mapping_strength, reviewer_name, date) | CCO authored, examination_guidelines §3.1 |
| "Prove you tested your controls" | `control_tests` + `test_executions` (pytest code, threshold source rule_id, evidence S3 URI) | CIO authored, §3.3 |
| "What MRAs are open from prior cycles?" | `findings` table — OCC 5 Cs (concern, cause, consequence, corrective_action, commitment), status, due date, S3 evidence | CCO authored, §3.1 |
| "What regulatory changes happened this quarter?" | `regulatory_change_records` + dispositions (acknowledged / requires_action / not_applicable) | Daily scrape + CCO sign-off (§7) |
| "Show OFAC compliance evidence" | `ofac_validations` quarterly attestation (methodology, sample size, findings, signed PDF WORM-locked in S3) | BSA Officer quarterly, §3.1 |
| "Prove your audit trail is immutable" | S3 Object Lock + exports WORM bucket, retention ≥ 7 yr | CIO verification |

**Exports the dashboard produces** (`src/api/dashboard_api.py:1044–1047`):
`examiner_package`, `ofac_attestation`, `board_report`, `mra_tracker`,
`change_log`, `coverage_gap`.

**Readiness score** (`dashboard_api.py:488–505`) — 10 dimensions, weighted:
testing (1.5×), change_mgmt (1.3×), control_mapping (1.3×), inventory (1.2×),
governance (1.2×), findings, independent, ra_currency, reporting, engine.

### 10.1 Worked example — examiner Q&A

> **Examiner:** "Show me you blocked the December 12 $50k wire to a Russian
> beneficiary and prove the rule citation."
>
> **Fortress trace:**
> 1. `SELECT screening_id, decision, engine_response FROM screening_events WHERE party_country='RU' AND created_at::date='2026-12-12' AND final_score >= 0.85;`
> 2. `engine_response.violations[*].rule_id` → look up in `compliance_rules` to show the OCC/FinCEN citation and the `rule_text` in force on that date.
> 3. `screening_cases.status` shows the workflow disposition.
> 4. The raw OFAC XML for that date is in S3 `scraper-chunks/sdn_xml_raw/2026-12-12/` — re-runnable if the examiner challenges the input.

### 10.2 BA validation — examination

- [ ] Generate each of the 6 exports (`POST /api/v1/dashboard/exports?type=examiner_package`) and confirm a downloadable artifact.
- [ ] Open `examiner_package` — must contain rule inventory + tested/untested status per rule.
- [ ] `SELECT COUNT(*) FROM findings WHERE status='open' AND type='MRA';` — should match the CCO's external MRA tracker.
- [ ] Confirm `ofac_attestation` export has the last quarterly attestation date and methodology section.

---

<a id="11-agent-pipeline"></a>
## 11. Agent pipeline (JIRA → PR) — how new tests get written

Six agents transform a JIRA story into a runnable compliance test that lands as
a GitHub PR + JIRA comment.

| Agent | File | Input | Output |
|---|---|---|---|
| Discovery | `src/agents/discovery.py:42–108` | JIRA story text | Detected banking patterns (wire, ACH, SAR, OFAC, CTR, …), discovered APIs, dependencies |
| Understanding | `src/agents/understanding.py:47–137` | Discovery + JIRA context | Requirements, test scenarios outline, banking rules, obligation_ids (joined via `obligations` table) |
| Framework | `src/agents/framework.py:13–243` | Understanding output | BDD scenarios — 20–40 Given/When/Then per story |
| Generation | `src/agents/generation_bedrock.py:23–116` | Scenarios + rules | Python pytest test code (Bedrock Claude Sonnet) |
| Validation | `src/agents/validation.py:15–156` | Generated test code | Quality score 0–100 (AST validity + compliance keyword presence). ≥50 gates approval |
| Execution | `src/agents/execution.py:15–102` | Validated tests | GitHub PR + JIRA comment + flips `obligations.has_test_coverage=TRUE` for resolved obligation_ids |

State tracked in `workflow_executions` (`status`, `artifacts`, `rule_id`) and
`checkpoint_approvals` (per-gate CCO approvals). Migration 016.

### 11.1 BA validation — agent pipeline

- [ ] Trigger a JIRA webhook on a test story → expect a `workflow_executions` row that transitions `pending → running → completed`.
- [ ] PR URL appears in the JIRA comment within ~90 s.
- [ ] **Known gap (Fix 1):** `obligations.has_test_coverage` does NOT yet flip to TRUE in production. Verify with the engineer whether the fix has shipped before validating this column.

---

<a id="12-cheatsheet"></a>
## 12. BA daily-validation cheat-sheet

### 12.0 How to actually run these queries (read-only SQL on P2)

The P2 Postgres instance lives in the bank account's private subnet. There
is no public endpoint, no bastion, and the RDS Data API / Query Editor is
unavailable (P2 runs `AWS::RDS::DBInstance` for Postgres, not Aurora — the
Query Editor only supports Aurora clusters). The BA path is a per-session
SSM port-forwarding tunnel through a short-lived Fargate jump task. The
Postgres role itself (`fortress_ba_ro`) is **SELECT-only**, so write
attempts fail even if accidentally typed.

**One-time setup (per bank, done by the engineer who deploys P2):**

1. `aws cloudformation deploy --stack-name fortress-p2-ba-jump-${BANK_ID} \
   --template-file infra/cloudformation/pipeline2-ba-jump.yml \
   --parameter-overrides BankId=${BANK_ID} \
   --capabilities CAPABILITY_NAMED_IAM`
2. Bootstrap the Postgres role from the read-only secret. (`AURORA_HOST`
   is the legacy SSM-key name for the P2 RDS Postgres endpoint — see the
   comment at `pipeline2-data.yml:270-272`; the underlying resource is
   `AWS::RDS::DBInstance` running engine `postgres`, not Aurora.)
   ```bash
   DB_HOST=$(aws ssm get-parameter \
     --name /fortress/${BANK_ID}/AURORA_HOST \
     --query Parameter.Value --output text)
   BA_PW=$(aws secretsmanager get-secret-value \
     --secret-id fortress/${BANK_ID}/db-password-readonly \
     --query SecretString --output text | jq -r .password)
   psql "host=$DB_HOST user=fortress_${BANK_ID} dbname=fortress_p2" \
     -v ba_password="$BA_PW" \
     -f scripts/setup/init_ba_readonly_role.sql
   ```
3. Bank IAM admin attaches the managed policy
   `fortress-p2-ba-query-readonly-${BANK_ID}` (exported by the BA-jump
   stack) to the BA's IAM group or role.

**Laptop prereqs (one-time, BA installs):**

- `aws` CLI v2.13+ — [install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- AWS Session Manager plugin — [install guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- `psql` client (PostgreSQL 15.x — `brew install libpq` on macOS, `apt-get install postgresql-client-15` on Debian/Ubuntu)
- `jq`
- AWS profile (`~/.aws/credentials` or SSO) whose IAM principal has the
  managed policy `fortress-p2-ba-query-readonly-${BANK_ID}` attached
  (the bank's IAM admin attaches this once, per step 3 above).

**Per-session (BA runs this in two terminals):**

```bash
# Terminal 1 — open the tunnel (blocks until Ctrl-C)
./scripts/start_ba_session.sh ${BANK_ID}

# Terminal 2 — connect with psql
BA_PW=$(aws secretsmanager get-secret-value \
         --secret-id fortress/${BANK_ID}/db-password-readonly \
         --query SecretString --output text | jq -r .password)
PGPASSWORD="$BA_PW" psql \
  "host=localhost port=55432 user=fortress_ba_ro dbname=fortress_p2 sslmode=require"

# At the psql prompt — paste any §12.1 query.
# Ctrl-D to exit. Ctrl-C terminal 1 to close the tunnel + auto-stop the task.
```

**What happens under the hood (so you can troubleshoot):**

1. `start_ba_session.sh` calls `aws ecs run-task --enable-execute-command`
   to launch a 256 CPU / 512 MB Fargate jump task tagged
   `Purpose=ba-session` inside the bank VPC's private subnets.
2. It waits ~45 s for the task and its ECS Exec agent to both report
   `RUNNING`.
3. It then calls `aws ssm start-session --document-name
   AWS-StartPortForwardingSessionToRemoteHost`, mapping `localhost:55432`
   on the BA's laptop to the RDS Postgres endpoint:5432 inside the VPC.
   Traffic exits the laptop through the SSM data channel — no VPN, no
   public IP, no bastion EC2.
4. `psql` connects to `localhost:55432`, which is really the bank's
   private-subnet RDS instance, authenticating as `fortress_ba_ro`
   (SELECT-only).
5. On `Ctrl-C`, the script's `trap` calls `aws ecs stop-task`, ending
   the session.

**What you can and cannot do at the prompt:**

| Action | Result |
|---|---|
| `SELECT ... FROM compliance_rules;` | ✅ works |
| `\dt` (list tables) | ✅ works |
| `EXPLAIN ANALYZE SELECT ...;` | ✅ works (read-only, no row locks) |
| `INSERT INTO compliance_rules ...` | ❌ `ERROR: permission denied for table compliance_rules` |
| `UPDATE / DELETE / TRUNCATE / DROP / ALTER` | ❌ permission denied — `fortress_ba_ro` has SELECT only |
| `CREATE TABLE ...` | ❌ no CREATE on schema public for this role |
| `SET ROLE postgres / fortress_${BANK_ID};` | ❌ `NOINHERIT` + `NOSUPERUSER` blocks privilege escalation |

**Caps and audit trail:**

- Jump task auto-self-destructs after 4h (`SessionTimeoutSeconds` in
  `pipeline2-ba-jump.yml`); restart the script if you need longer.
- Connection limit: 4 concurrent sessions per BA role.
- Every SSM session is logged in CloudTrail (`StartSession`,
  `TerminateSession`); every Postgres login is logged in the RDS
  `postgresql` log group (`/aws/rds/instance/fortress-p2-pg-${BANK_ID}-${ENV}/postgresql`).

**Common failures and fixes:**

| Symptom | Cause | Fix |
|---|---|---|
| `command not found: session-manager-plugin` | AWS Session Manager plugin missing on laptop | Install per [AWS docs — Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) |
| `AccessDeniedException` on `ecs:RunTask` | Managed policy not attached to BA's IAM identity | Ask bank IAM admin to attach `fortress-p2-ba-query-readonly-${BANK_ID}` |
| `start_ba_session.sh` errors "could not resolve subnets / SgEcs" | `pipeline2-vpc` or `pipeline2-data` stack not in `*_COMPLETE` | Engineer confirms both stacks healthy, then BA re-runs |
| Task stuck in `PROVISIONING` >2 min | Image-pull throttling against `public.ecr.aws` (jump image) | Wait 60 s, re-run; persistent issue → engineer pins the image into the bank's private ECR |
| `psql: FATAL: password authentication failed for user "fortress_ba_ro"` | Bootstrap SQL not run yet for this bank | Engineer runs `scripts/setup/init_ba_readonly_role.sql` once per bank (idempotent) |
| `psql: connection to server at "localhost", port 55432 failed: SSL connection has been closed` | RDS requires SSL; `sslmode=require` missing or wrong | Keep `sslmode=require` exactly as shown — do not change to `disable`/`prefer` |
| `ERROR: permission denied for table compliance_rules` on a SELECT | New table added by a recent migration; default-privileges block didn't catch it | Engineer re-runs the bootstrap SQL (idempotent, refreshes grants on all existing tables) |
| Tunnel opens but `psql` hangs at "Password:" | Local port 55432 already in use (another tunnel still alive) | `LOCAL_PORT=55433 ./scripts/start_ba_session.sh ${BANK_ID}` and use the new port in the `psql` host arg |
| `ERROR: permission denied for schema public` | `GRANT USAGE ON SCHEMA public` not applied (very stale role) | Engineer re-runs the bootstrap SQL |
| Session disconnects after 4 hours mid-query | Jump container hit `SessionTimeoutSeconds=14400` | Re-run `start_ba_session.sh`; for genuinely long-running analytics, engineer raises the cap or runs the query as an engineer |

### 12.1 The queries

A 15-minute morning sweep that catches 90% of regressions.

```sql
-- 1. Are connectors healthy?
SELECT connector, status, rules_changed, error_message, started_at
FROM   scrape_runs
WHERE  started_at >= NOW() - INTERVAL '24 hours'
ORDER  BY started_at DESC;

-- 2. Did any thresholds drift away from the resolver constants?
SELECT rule_id, rule_type, threshold_amount
FROM   compliance_rules
WHERE  rule_type IN ('ctr_threshold','dual_approval','travel_rule_threshold')
  AND  is_active = TRUE;
-- Expect: 10000 / 10000 / 3000  (must match compliance_rule_resolver.py:48-74)

-- 3. Beneficial-ownership threshold = 20% (post April-11-2026 rule)
SELECT rule_id, threshold_amount, effective_date
FROM   compliance_rules
WHERE  rule_type LIKE '%beneficial_ownership%' AND is_active;

-- 4. OFAC freshness
SELECT MAX(embedded_at) AS last_embed, COUNT(*) AS variant_count
FROM   ofac_sdn_vectors WHERE is_active;
-- last_embed should be within 24h; variant_count should grow with each new SDN entry

-- 5. Open change records pending CCO sign-off
SELECT change_ref, regulator, impact_rating, status, created_at
FROM   regulatory_change_records
WHERE  status = 'detected' AND is_active
ORDER  BY impact_rating DESC, created_at ASC;

-- 6. Today's wire decisions
SELECT decision, COUNT(*)
FROM   screening_events
WHERE  created_at >= NOW() - INTERVAL '24 hours'
GROUP  BY decision;

-- 7. Open MRAs
SELECT id, type, status, due_date
FROM   findings
WHERE  status = 'open' AND type IN ('MRA','MRIA');
```

Hit `/api/v1/dashboard/readiness` and confirm the score hasn't dropped > 5
points overnight without an explanatory change record.

If any row above looks wrong, file an issue against `docs/ftr_tracker.md`
with the citation and a copy of the offending row.

---

## Appendix A — Document → ground-truth map

For each ingested document, this is what the BA should validate against
the regulator's published source.

| Document | Regulator's published source | Ground-truth field | Auditor expects to see |
|---|---|---|---|
| OCC Comptroller's Handbook — BSA | occ.gov/publications-and-resources/publications/comptrollers-handbook/ | Each section heading → one `compliance_rules.rule_id` | A rule per examination procedure; CFR cite present |
| FinCEN — CTR | fincen.gov/resources/filing-information | $10,000 cash threshold | `rule_type='ctr_threshold'`, `threshold_amount=10000`, citation `31 C.F.R. § 1010.311` |
| FinCEN — SAR | fincen.gov | $5,000 identified, 30-day filing | `rule_type='sar_filing'`, `threshold_amount=5000` |
| FinCEN — CDD beneficial ownership | fincen.gov (CHG-2026-0412, April 11 2026) | 20% threshold | `threshold_amount=20`, `effective_date >= 2026-04-11` |
| OFAC SDN | sanctionslistservice.ofac.treas.gov | XML feed | `ofac_sdn_vectors` row per name variant; embed worker ≤24 h old |
| CFPB — Section 1071 | consumerfinance.gov/rules-policy/ | Required demographic fields list | Validator returns `block` if any field missing |
| 23 NYCRR §500 (NY DFS) | dfs.ny.gov | Cybersecurity program required | `state_code='NY-DFS'`, `category='cybersecurity'` |
| FFIEC BSA Manual | bsaaml.ffiec.gov (CF-gated → hardcoded) | §314(a/b) info-sharing | Rules present despite no live scrape |

---

## Appendix B — Known gaps the BA should track (not block ship)

| # | Gap | Where | Impact |
|---|---|---|---|
| 1 | `obligations.has_test_coverage` never flipped to TRUE | `examination_readiness_fix_plan.md` Fix 1 | Testing readiness dim. stuck at 0%, caps score ~85 |
| 2 | Sign-off `note` parameter parsed but not persisted | `dashboard_api.py:538` | CCO comments lost on sign-off |
| 3 | `reviewer_name` / `reviewer_role` not captured at sign-off | `dashboard_api.py:560-571` | Cannot attribute approvals to specific CCO |
| 4 | "requires_action" sign-off does not auto-create JIRA story | dashboard_api.py:548-581 | Manual hand-off still required |
| 5 | Legacy rows synced before mig 037 have NULL `change_delta` | Changes.jsx:209 | Drawer shows fallback text instead of structured diff |

Each gap has a ticket in `docs/ftr_tracker.md` — verify before sign-off.
