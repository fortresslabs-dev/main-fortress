# Fortress Labs — SME Reference

**Audience:** Subject Matter Experts (compliance officers, BSA officers, auditors) reviewing the scope, rules, and business logic implemented by the Fortress platform.
**Purpose:** One-stop inventory of every regulation scraped, every compliance rule enforced, every hardcoded threshold, and every obligation the system tracks — with source citations and file references so an SME can validate accuracy against authoritative sources.
**Last regenerated:** 2026-07-02
**Scope:** Pipeline 1 (regulatory ingestion / rule store) + Pipeline 2 (bank-side validators, CCO dashboard).

---

## 1. Regulations Scraped

### 1.1 Federal connectors (`src/connectors/regulatory/usa/`)

| Agency | Connector file | Rule IDs emitted | Citation / topic |
|---|---|---|---|
| **FFIEC — BSA/AML** | `ffiec_bsa.py` | `FFIEC-BSA-PROG-001`, `FFIEC-BSA-CIP-001`, `FFIEC-BSA-314-001` | 31 CFR §§ 1020.210, 1020.220, 1010.520–540 — 4-pillar BSA program, CIP, §314(a/b) sharing |
| **FFIEC — Cybersecurity** | `ffiec_cybersecurity.py` | `FFIEC-CAT-001`, `FFIEC-ITSEC-001`, `FFIEC-BCM-001` | Cyber Assessment Tool (5×5), IT Handbook Info Security, Business Continuity |
| **FFIEC — Call Report** | `ffiec_call_report.py` | `FFIEC-CALL-031`, `FFIEC-CALL-041`, `FFIEC-CALL-051` | 12 CFR Part 304 — Schedules RC, RI, RC-N; 30-day filing after quarter-end |
| **FinCEN — SAR** | `fincen_sar.py` | `FINCEN-SAR-001`, `FINCEN-SAR-002` (+ `-TRIG-001..003`, `-DEADLINE-001`, `-CONT-001`, `-FIELD-001`) | 31 CFR § 1020.320 — SAR thresholds ($0 insider / $5k identified / $25k unidentified), 30-day filing, 90-day continuing |
| **OCC — BSA/AML** | `occ_bsa.py` | `OCC-BSA-WIRE-001`, `OCC-BSA-CTR-001`, `OCC-BSA-CDD-001` | 31 CFR §§ 1010.410(f), 1010.311, 1010.230 — wire dual approval, CTR, CDD (20% BO effective 2026-04-11) |
| **OCC / FRB / FDIC — CRA** | `cra.py` | `CRA-OCC-001`, `CRA-FRB-001`, `CRA-FDIC-001` | 12 CFR Parts 25 / 228 / 345 — assessment area, lending/investment/service tests |
| **OCC — CECL / Model Risk** | `cecl.py` | `CECL-MODEL-001`, `CECL-VALIDATION-001`, `CECL-ACL-001`, `CECL-GOVERNANCE-001`, `CECL-BACKTESTING-001` | SR 11-7, OCC 2011-12, OCC 2020-51, FASB ASC 326 |
| **FDIC — Consumer/Deposit** | `fdic.py` | `FDIC-EXAM-001`, `FDIC-DEPOSIT-001`, `FDIC-CAPITAL-001` | 12 CFR §§ 337.6, 330.1, 324.10–12 — exam cycle, $250k SMDIA, Basel III capital ratios |
| **CFPB — Fair Lending** | `cfpb.py` | `CFPB-1071-001`, `CFPB-HMDA-001`, `CFPB-REGB-001`, `CFPB-REGE-001`, `CFPB-FAIRLEND-001`, `CFPB-HMDA-PEER-001` | 12 CFR §§ 1002, 1003, 1005; Section 1071 (compliance date **2026-07-01**) |
| **OFAC — SDN screening** | `ofac_sdn.py` | `OFAC-SDN-001` | SDN List + 50% rule; alias types 1403 canonical / AKA / FKA / NKA |
| **Liquidity Risk** | `liquidity_risk.py` | `LIQ-CFP-001`, `LIQ-BUFFER-001`, `LIQ-UNINSURED-001`, `LIQ-GOVERNANCE-001` | Interagency 2010 Policy Statement, OCC 2023-13 (post-SVB) |
| **Operational Risk** | `operational_risk.py` | 7 Basel II event categories + `OPRISK-RCSA-001`, `OPRISK-KRI-001`, `OPRISK-APPETITE-001` | OCC Op Risk Handbook, Basel II (BIS 2006) |

**Total federal citations tracked: ~45.**

### 1.2 State connectors (`src/connectors/regulatory/usa/states/`)

| State | File | Rule IDs | Topic |
|---|---|---|---|
| **New York** | `nydfs.py` | `NYDFS-500-001`, `NYDFS-504-001` | 23 NYCRR Part 500 (cyber, 72-hr breach), 3 NYCRR Part 116 (BSA/AML monitoring) |
| **California** | `ca_dfpi.py` | `CA-CFL-001`, `CA-CCPA-001`, `CA-DFAL-001` | CA Financing Law, CCPA/CPRA, Digital Financial Assets Law (**2025-07-01**) |
| **Texas** | `tx_dob.py` | `TX-BANK-001`, `TX-MSA-001` | TX Finance Code, Money Services Act (§ 151.301) |
| **Illinois** | `il_idfpr.py` | `IL-BANK-001`, `IL-TOMA-001` | IL Banking Act, Transmitters of Money Act (205 ILCS 657) |

### 1.3 Non-scrape hosts (hardcoded output authoritative)

`src/connectors/regulatory/usa/_scraper_utils.py`

- `_NO_SCRAPE_HOSTS`: `bsaaml.ffiec.gov`, `ithandbook.ffiec.gov` — Cloudflare JS challenge, no archive.org fallback; `_build_regulations()` is the sole truth.
- `_WAYBACK_FALLBACK_DOMAINS`: `www.ffiec.gov`, `cdr.ffiec.gov`, `ffiec.cfpb.gov`, `dfpi.ca.gov` — falls through to archive.org snapshots.
- `_BROWSER_UA_DOMAINS`: FFIEC family + `federalreserve.gov` + `dfpi.ca.gov` — full browser header set required.

---

## 2. Compliance Rules & Thresholds

### 2.1 Resolver architecture (`src/services/compliance_rule_resolver.py`)

Four-tier lookup for `(domain, rule_type)` → threshold:

1. **Tier 1 — Postgres** exact match on `compliance_rules` (authoritative).
2. **Tier 2 — Redis** 1-hour TTL cache (`_REDIS_TTL = 3600`).
3. **Tier 3 — pgvector** hybrid ANN + BM25: `score = 0.7·cosine + 0.3·ts_rank`, top-3 rerank.
4. **Tier 4 — hardcoded fallback** (`_FALLBACK_THRESHOLDS`).

### 2.2 Hardcoded Tier 4 thresholds

| Domain | Rule type | Value | Citation |
|---|---|---|---|
| wire_transfer | `ctr_threshold` | $10,000 | 31 CFR 1010.311 |
| wire_transfer | `dual_approval_threshold` | $10,000 | OCC 2017-7 §III(B) |
| wire_transfer | `travel_rule_threshold` | $3,000 | 31 CFR 1010.410(f) |
| sar | `suspicious_threshold` | $5,000 | 31 CFR 1020.320 |
| sar | `insider_threshold` | $0 (any amount) | FinCEN BSA/AML Manual |
| ctr | `cash_threshold` | $10,000 | 31 CFR 1010.311 |
| ofac | `fuzzy_match_threshold` | 85% | Internal calibration + Treasury guidance |
| ofac | `reporting_deadline_days` | 10 business days | 31 CFR 501.604 |
| reg_e | `reporting_window_days` | 60 days | 12 CFR 1005.11(b)(1) |
| reg_e | `provisional_credit_days` | 10 business days | 12 CFR 1005.11(c)(2)(i)(A) |
| reg_e | `investigation_days` | 45 days | 12 CFR 1005.11(c)(3)(ii) |
| reg_e | `new_account_investigation_days` | 90 days | 12 CFR 1005.11(c)(3)(ii) |
| kyc_cdd | `beneficial_ownership_pct` | **20%** (was 25%; amended FinCEN CHG-2026-0412, effective 2026-04-11) | 31 CFR 1010.230(d)(1) |

### 2.3 Bank-side validators (`src/services/validators.py`)

Every validator runs **locally inside the bank's VPC** — customer transaction data never leaves.

**WireTransferValidator** (lines 259–357)
- Dual approval: `amount ≥ threshold` AND `len(approvals) < 2` → **BLOCK**.
- CTR: `amount ≥ ctr_threshold` → **FLAG** (medium).
- International Travel Rule: `is_international` AND `amount ≥ $3,000` → **FLAG** (missing originator info).

**OFACScreener** (lines 380–896)
- `OFAC_SIMILARITY_THRESHOLD = 0.85` — screen match trigger.
- `OFAC_HOLD_THRESHOLD = 0.92` — auto-**BLOCK** ≥ 92%, else **REVIEW**.
- `_SDN_STALENESS_SOFT_HOURS = 24` — bump decision one level if list stale > 24h.
- `_SDN_STALENESS_HOURS = 36` — hard gate; refuse to screen if > 36h.
- Legacy mode: in-memory `difflib.SequenceMatcher` on primary names.
- Vector mode: pg_trgm + tsvector candidates → rapidfuzz token_sort_ratio; blend `0.5·trgm + 0.5·fuzz` (no embedding) or `0.4·trgm + 0.4·fuzz + 0.2·cosine` (with embedding).
- 50% Ownership Rule enforced via `OWN50` rule tag (priority over `FUZZY`).
- Every screen writes a `screening_event` and upserts a `screening_case` keyed on `(external_customer_id, sdn_number)` (or `criteria_hash` within 24h for anonymous flows).

**LoanApplicationValidator** (lines 903–986) — CFPB Section 1071
- Required fields: race, sex, ethnicity of principal owner; NAICS (2–6 digits); gross revenue; time in business; # workers; minority-owned; women-owned.
- Any missing/empty field → **FLAG** (high).

### 2.4 Validation API (`src/api/validation_api.py`)

- `POST /validate/wire-transfer`
- `POST /validate/ofac-screen`
- `POST /validate/loan-application`

Rate limit: `_VALIDATE_RPM = 60`/60s sliding window per client IP (override via `FORTRESS_P2_VALIDATE_RPM`). Metrics emitted to `Fortress/P2/Validation` (LatencyMs, Decisions, Calls).

### 2.5 Compliance deadline tracker (`src/services/deadline_tracker.py`)

Alert thresholds fire at `[90, 60, 30, 14, 7]` days remaining. Once per threshold per `deadline_key`.

| Deadline key | Regulator | Type | Due | Recurrence |
|---|---|---|---|---|
| `FFIEC-CALL-Q1..Q4` | FFIEC | quarterly filing | Apr 30 / Jul 30 / Oct 30 / Jan 30 | Yearly |
| `CFPB-HMDA-LAR-ANNUAL` | CFPB | annual filing | Mar 1 | Yearly |
| `CRA-ANNUAL-REVIEW` | OCC | internal | Dec 31 | Yearly |
| `CFPB-1071-COMPLIANCE` | CFPB | effective date | **2026-07-01** | One-time |
| `FINCEN-BO-AMENDMENT` | FinCEN | effective date | **2026-04-11** | One-time |
| `IRR-ANNUAL-REVIEW` | OCC | internal | Dec 31 | Yearly |
| `FFIEC-CAT-ANNUAL` | FFIEC | assessment | Dec 31 | Yearly |

### 2.6 Enforcement risk scoring (`src/services/enforcement_risk_scorer.py`)

```
freq_score    = log1p(action_count) / log1p(50)             # cap = 50 actions
penalty_score = log1p(avg_penalty_usd) / log1p(1_000_000_000)  # cap = $1B
trend_score   = 1.0 (increasing) | 0.5 (stable) | 0.0 (decreasing)
risk_score    = clamp(0.45·freq + 0.35·penalty + 0.20·trend, 0, 1)
```
Trend window: 12 months recent vs. 12–24 months prior. Increasing if `recent > 1.2·prior`; decreasing if `recent < 0.8·prior`. Default lookback = 24 months. Score is back-written to every matching `ComplianceRule.enforcement_risk_score`.

### 2.7 Test-code quality scoring (`src/agents/validation.py`)

- Syntax valid: +20
- Compliance keyword coverage: up to +30 (base 100; deductions: −15 audit trail / −15 CTR $10k / −15 OFAC / −10 SAR / −10 PII masking / −5 encryption)
- Coverage: `min(test_count / scenario_count, 1.0) · 20`
- Code quality: +8 docstrings / +7 type hints / +10 assertions / +5 fixtures
- Gate: `is_valid = syntax_valid AND compliance_score ≥ 50`
- `approved` at quality_score ≥ 80; else `needs_revision`

### 2.8 Seed thresholds script (`scripts/setup/seed_threshold_defaults.py`)

Populates `compliance_rules.threshold_default` (JSONB) with `threshold_rationale` and `threshold_source_citation` per row so every hardcoded value has an auditable provenance in Postgres.

---

## 3. Obligations

An **obligation** = one enforceable sentence (a "shall", "must", "prohibited" clause) extracted from a regulation. Obligations are the atomic unit of compliance coverage — a rule is considered tested only when every obligation on it has been covered by a passing test.

### 3.1 Schema — `obligations` table (`src/models/database.py:387`, migration `008`)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK, `gen_random_uuid()` |
| `obligation_id` | String(200) | **UNIQUE, deterministic** — `{rule_id}::OBL::{seq:03d}` |
| `regulation_id` | String(100) | FK-style link to `compliance_rules.rule_id` |
| `regulator` | String(50) | OCC / CFPB / FDIC / FRB / FINCEN / OFAC / FFIEC / NYDFS / … |
| `category` | String(100) | BSA_AML, Section_1071, wire_transfer, cybersecurity, … |
| `source_text` | Text | Verbatim sentence |
| `obligation_type` | String(20) | **enum:** `shall` / `must` / `required` / `prohibited` / `may_not` |
| `section_reference` | Text | Bare § ref (e.g. `1010.230(a)`) |
| `cfr_citation` | Text | Full citation (e.g. `31 C.F.R. § 1010.230`) |
| `doc_id` | String(100) | Source document ID |
| `has_test_coverage` | Bool | `TRUE` once a passing test writeback occurs (append-only) |
| `coverage_updated_at` | Timestamp | When coverage was set |
| `test_scenario_ids` | JSONB | Array of scenario IDs that cover this obligation |
| `extracted_at` | Timestamp | Extraction time |
| `is_active` | Bool | Soft-delete flag |

### 3.2 Obligation ID format

`{rule_id}::OBL::{sequential_index:03d}` — e.g. `occ-bsa-rule-001::OBL::001`, `cfpb-1071-123::OBL::042`.

Deterministic and rule-scoped so re-extraction is idempotent. **Not** a UUID.

### 3.3 Obligation types (verb classes)

| Type | Trigger phrases |
|---|---|
| `shall` | "shall" |
| `must` | "must" (but not "must not") |
| `required` | "required", "needs to", "has/have to" |
| `prohibited` | "prohibited", "may not", "must not", "will not" |
| `may_not` | (redundant; DB check permits it) |

Classification is syntactic only — the platform does **not** currently tag obligations semantically (recordkeeping / reporting / threshold / risk-based).

### 3.4 Extractor (`src/services/obligation_extractor.py`)

1. **Regex pass (always runs).** Splits `rule_text` on sentence boundaries, filters by `_OBLIGATION_TRIGGERS`, extracts CFR + § refs.
2. **LLM fallback (env-gated).** `LLM_EXTRACTION_MODE=off|fallback|shadow`. Bedrock Haiku 4.5 by default (`P1_OBLIGATION_EXTRACTION_MODEL`), Sonnet 4.5 as `BEDROCK_MODEL` fallback. Requires `confidence ≥ LLM_EXTRACTION_MIN_CONFIDENCE` (0.5). Only fills gaps the regex missed; regex wins on conflict.
3. **Rule mirror.** After extraction, `ComplianceRule.obligation_count` is updated in-line.

Invoked from `scraper_scheduler` after every successful scrape and from `scripts/backfill_rule_extractions.py` for backfill.

### 3.5 Seed obligations — **none**

There are no persisted seed obligations anywhere in the repo (searched `alembic/`, `scripts/setup/`, `tests/fixtures/`). Every obligation is runtime-extracted from a scraped regulation. **SME implication:** the obligation inventory shifts as connectors add/refine regulations — treat obligation counts as living.

### 3.6 Downstream significance

| Consumer | Behavior |
|---|---|
| **Agent 6 execution** (`src/agents/execution.py::_mark_obligations_covered`) | On successful PR + validation, sets `has_test_coverage=TRUE`, `coverage_updated_at=now()`, appends scenario IDs. |
| **CCO dashboard** | "Obligations" card = coverage % per regulator. "High-risk obligations untested" panel filters uncovered rows joined to `enforcement_risk_score`. Feeds the unified readiness score. |
| **P1→P2 sync** (`/sync/obligations` in `src/api/sync_api.py`) | Ships the full active obligation set to each bank's P2 instance nightly. |
| **Rule mirroring** | `ComplianceRule.obligation_count` + `ComplianceRule.changed_obligations` (JSONB list of ids changed at last diff). |
| **Change tracking** | `RegulatoryChangeRecord.change_delta` (migration 037) stores per-record field-level diff at detection. `ObligationDiffService` (structural + semantic) computes obligation-level deltas surfaced to the P2 drawer. |

**Known gap (SME callout).** Coverage is append-only — once `has_test_coverage=TRUE`, it does **not** downgrade if the obligation text later changes, and removed obligations are not hard-tombstoned. Coverage stats should be read alongside `RegulatoryChangeRecord.change_delta` to detect stale-green obligations.

---

## 4. Critical dates for the compliance calendar

| Date | Event | Where enforced |
|---|---|---|
| **2025-07-01** | CA DFAL operative — digital financial asset licensing | `CA-DFAL-001`, `states/ca_dfpi.py` |
| **2026-04-11** | FinCEN CHG-2026-0412 — beneficial ownership threshold **25% → 20%** | `_FALLBACK_THRESHOLDS["kyc_cdd"]`; `OCC-BSA-CDD-001`; `FINCEN-BO-AMENDMENT` deadline |
| **2026-07-01** | CFPB Section 1071 small-business lending compliance date | `LoanApplicationValidator`; `CFPB-1071-COMPLIANCE` deadline |
| Quarterly | FFIEC Call Report — 30 days after quarter-end (Apr 30 / Jul 30 / Oct 30 / Jan 30) | `deadline_tracker.py` |
| Annual (Mar 1) | HMDA LAR filing | `CFPB-HMDA-LAR-ANNUAL` |

---

## 5. SME validation checklist

Suggested walkthrough when reviewing this document. **The SME does not need code or system access** — each step is a manual comparison between what this document states and the original published regulation, agency FAQ, or the bank's own policy manual.

For each step: read the row in this document, open the original source (link provided), compare, and mark **Pass / Watch / Fail**. Watch = correct today but a public proposal/amendment may change it soon. Fail = drift; escalate to Fortress engineering with the row reference below.

### 5.1 Confirm every citation in §1 matches the current published CFR/USC section

Pick any rule ID from the §1 table. Look up the CFR/USC citation on the government source. Read the section header and the substantive requirement. Confirm both still exist and still say what §1 claims.

**Worked example — `OCC-BSA-CDD-001` (row in §1.1, OCC BSA/AML):**
- Citation shown in this doc: **31 CFR § 1010.230** — "customer due diligence, beneficial ownership".
- Open **eCFR** in a browser: https://www.ecfr.gov/current/title-31/section-1010.230
- Read §1010.230(d)(1) — the definition of "beneficial owner" and the ownership-percentage trigger.
- Confirm the current text reads **20 percent** (post FinCEN CHG-2026-0412, effective 2026-04-11). Prior text was 25 percent.
- **Pass:** eCFR text matches this doc.
- **Watch:** FinCEN has an open comment period on a related section.
- **Fail:** eCFR shows a different threshold, a different section number, or the section is reserved/removed → email Fortress with the rule_id (`OCC-BSA-CDD-001`), the eCFR URL, and a screenshot of the current text.

### 5.2 Confirm Tier 4 fallbacks in §2.2 match authoritative regulatory thresholds

Pick any dollar amount, percentage, or day count from the §2.2 table. Open the cited source (eCFR, FinCEN FAQ, FFIEC BSA/AML Examination Manual, CFPB regulation implementation guide). Confirm the published number equals the number in this document.

**Worked example — `wire_transfer.travel_rule_threshold = $3,000` (row in §2.2):**
- Citation shown: **31 CFR § 1010.410(f)** — Travel Rule.
- Open eCFR: https://www.ecfr.gov/current/title-31/section-1010.410#p-1010.410(f)
- Read paragraph (f)(1): the recordkeeping requirement applies to transmittals of funds "in the amount of $3,000 or more".
- Confirm "$3,000" is unchanged and applies to the same transaction type (transmittals of funds, all currencies).
- **Pass:** the threshold in the source equals the threshold in this doc.
- **Watch:** FinCEN has an open notice of proposed rulemaking to lower the threshold for certain convertible virtual currency transactions to $250. That is a **proposal, not adopted** — flag as a watch item but do not mark Fail.
- **Fail:** eCFR text shows a different dollar amount or the section is retired → email Fortress with the domain (`wire_transfer`), rule_type (`travel_rule_threshold`), and the eCFR paragraph.

### 5.3 Sample 10 obligations per regulator and confirm classification + citation

Ask a Fortress operator to export 10 obligations per regulator to CSV or PDF (columns: `obligation_id`, `regulator`, `source_text`, `obligation_type`, `cfr_citation`). Open each row against the original regulation and confirm two things: **(a)** the verb class matches the sentence, **(b)** the CFR citation actually contains the sentence.

**Worked example — one FinCEN row:**

| Field | Value in export |
|---|---|
| obligation_id | `FINCEN-SAR-001::OBL::003` |
| regulator | FINCEN |
| source_text | "A bank shall file a SAR no later than 30 calendar days after the date of initial detection…" |
| obligation_type | `shall` |
| cfr_citation | 31 C.F.R. § 1020.320 |

Manual check:
1. Open **eCFR §1020.320**: https://www.ecfr.gov/current/title-31/section-1020.320
2. Scroll to paragraph (b)(3) — the filing deadline.
3. Confirm the source text quoted above appears verbatim (or as a close paraphrase) in (b)(3), and that the sentence starts with "shall".
4. Classification: verb is **"shall file"** → `obligation_type = shall` is correct.
5. Citation: text lives in §1020.320(b)(3), which is inside §1020.320 → cited section correct.

Repeat for 9 more FinCEN rows, then 10 rows per each other regulator.

- **Pass:** ≥ 9 of 10 rows per regulator classify and cite correctly.
- **Watch:** the source text is a paraphrase (not verbatim) but the substantive obligation is faithful — note the paraphrase but do not fail.
- **Fail:** verb class wrong (e.g. `shall` labelled `must`), citation points to a section that does not contain the sentence, or the sentence is not actually an obligation (descriptive text mis-flagged) → send the offending `obligation_id`s to Fortress.

### 5.4 Confirm the deadline calendar in §2.5 and §4 is complete for the SME's institution profile

Take the bank's compliance-calendar spreadsheet (or the internal reg-ops runbook, whatever the CCO maintains today). Line-by-line, compare each filing due-date on that spreadsheet against §2.5 and §4 of this document. Anything on the bank's calendar that is missing from this doc is a gap.

**Worked example — mid-size national bank, $8B assets, NY-chartered:**

| Deadline on bank's calendar | Present in this doc? | Result |
|---|---|---|
| FFIEC Call Report — Apr 30 / Jul 30 / Oct 30 / Jan 30 | Yes (§2.5, `FFIEC-CALL-Q1..Q4`) | ✔ Pass |
| HMDA LAR — Mar 1 | Yes (§2.5, `CFPB-HMDA-LAR-ANNUAL`) | ✔ Pass |
| CFPB Section 1071 — Jul 1, 2026 | Yes (§4) | ✔ Pass |
| **NYDFS 23 NYCRR 500.17(b) CISO certification — Apr 15** | **No** | **✖ Fail — gap** |
| CRA public file update — annually | Partial (`CRA-ANNUAL-REVIEW` in §2.5 covers internal review, not the public-file update) | ⚠ Watch |
| FFIEC 041 Call Report form (asset tier > $5B so 051 not permitted) | §1.1 shows `FFIEC-CALL-041` is emitted, so form-selection matches. | ✔ Pass |

- **Fail action:** email Fortress with the missing `deadline_key` (e.g. `NYDFS-500-CISO-CERT`, statutory citation `23 NYCRR 500.17(b)`, due date, and recurrence). Fortress engineering will add it to the tracker.

### 5.5 Review the enforcement risk weights (45% / 35% / 20%) against internal risk appetite

The formula in §2.6 blends three signals: how *often* a regulation is enforced (45%), how *big* the penalties are (35%), and whether the *trend is rising* (20%). Compare that mix against the bank's own risk appetite statement.

**Worked example — hypothetical Reg E enforcement history over 24 months:**
- Action count: 12 enforcement actions.
- Average penalty: $2.5M.
- Trend: rising (more actions in the recent 12 months than the prior 12).

Applying the formula in §2.6:
- Frequency score ≈ **0.65** (12 out of a cap of 50 actions, log-scaled).
- Penalty score ≈ **0.71** ($2.5M against a cap of $1B, log-scaled).
- Trend score = **1.0** (rising).
- Composite ≈ `0.45 × 0.65 + 0.35 × 0.71 + 0.20 × 1.0` ≈ **0.74** → the platform labels Reg E "high risk".

Question for the SME / CCO:
- Does a portfolio with 12 actions averaging $2.5M and rising deserve a **0.74** score in your framework?
- If the bank's own risk appetite says "penalty magnitude matters more than count", you would tell Fortress to shift the weights (e.g. **30% / 50% / 20%**).
- **Pass:** the weights match the bank's stated appetite.
- **Watch:** the composite is directionally right but the CCO would tune 5–10 points either way.
- **Fail:** the CCO would ranks Reg E medium (or low) risk here → email Fortress with the proposed reweighting and the rationale.

### 5.6 Confirm OFAC thresholds are acceptable — 85% screen / 92% auto-block / 36h staleness

Compare the platform's thresholds against **(a)** OFAC's own compliance guidance and **(b)** the bank's OFAC screening policy manual.

**Worked example — check each of the three thresholds:**

1. **85% screen (fuzzy match trigger for review queue).**
   - Source: OFAC's "A Framework for OFAC Compliance Commitments" (May 2019) — recommends risk-based screening with meaningful thresholds. No hard number is mandated; industry range is 80–90%.
   - Bank's own policy manual: check what the CCO signed off on. If policy says "85%", **Pass**. If policy says "80%", the platform will **miss matches between 80% and 85%** — **Fail**, raise with Fortress.

2. **92% auto-block.**
   - Source: no OFAC mandate on auto-block vs. review — this is a bank policy choice.
   - Question for the SME: at 92% is the bank comfortable auto-blocking without human review? If policy says "all above 85% must go to human queue, no auto-block", the 92% auto-block violates policy → **Fail**.

3. **36-hour staleness hard-gate.**
   - Source: OFAC guidance requires screening against the "current" SDN list, and OFAC updates the list on an as-needed basis (often multiple times per week).
   - Bank's own policy: many banks require SDN list refresh within 24 hours of publication. If bank policy says 24h and the platform allows 36h, that's a policy-vs-platform gap — **Watch** if refresh actually happens in <24h in practice; **Fail** if refreshes routinely land in the 24–36h window.

Illustrative test names to run through the screener (an operator would execute; SME reviews the resulting alerts and decisions):
- Legitimate common name — e.g. `Ali Hassan` — should land in **REVIEW**, not auto-block. Confirms 85–92% band is being used as a queue, not a block.
- Exact SDN alias — e.g. any name copied from Treasury's public SDN search page — should score at or near 100% and be **BLOCKED**. Confirms the block path fires.
- 50%-owned entity — a shell company 51%-owned by an SDN'd party — should also be flagged (the 50% Ownership Rule). Confirms OFAC's 50% rule is enforced, not just direct-name matches.

- **Pass:** all three thresholds match written bank OFAC policy, and calibration runbook (`docs/runbooks/ofac-calibration-gold-set.md`) has been reviewed and signed off.
- **Fail action:** email Fortress with (a) the bank's written OFAC policy excerpt, (b) which of the three thresholds diverges, and (c) the target values. Fortress engineering will recalibrate against the bank's own gold set of past screening decisions.

---

## 6. End-to-end worked example

To make the pipeline concrete, this section traces **one regulation** — the OCC wire-transfer dual-approval requirement — from its published source all the way to the moment it blocks a real bank wire. Every stage is shown as data an SME can inspect and validate.

**Regulation followed through the pipeline:** OCC BSA/AML Wire Transfer Dual Approval (`OCC-BSA-WIRE-001`), citing 31 C.F.R. § 1010.410(f).

### Stage 1 — Source (what the regulator publishes)

- **Regulator:** Office of the Comptroller of the Currency (OCC).
- **Primary source page:** OCC BSA/AML Examination Manual (the "Manual" section on wire transfers and funds-transfer recordkeeping).
- **Underlying statute/regulation:** 31 C.F.R. § 1010.410(f) (FinCEN Travel Rule + funds transfer recordkeeping). eCFR: `https://www.ecfr.gov/current/title-31/section-1010.410`.
- **What the source says (paraphrased from the OCC manual):**
  > *Banks should establish dual-approval controls for wire transfers meeting BSA thresholds. Approvals must be recorded and separated by role, and records retained for five years under 31 CFR §1010.410(f).*

**SME check at Stage 1:** open the eCFR link and read §1010.410(f). Confirm the section title still says "recordkeeping for funds transmittals" and the five-year retention still appears in (f)(1).

### Stage 2 — Scrape (what the platform pulls back)

The OCC BSA connector fetches the manual URL, parses the HTML, and produces a raw record. Illustrative JSON of what the scraper stores:

```json
{
  "source": "OCC",
  "url": "https://www.occ.treas.gov/publications-and-resources/publications/comptrollers-handbook/files/bank-supervision-process/pub-ch-bank-supervision-process.pdf",
  "fetched_at": "2026-06-30T04:12:03Z",
  "content_type": "text/html",
  "http_status": 200,
  "raw_text": "…Wire transfers of $10,000 or more require approval from two separate authorized employees. The initiator and approver must be different individuals. Records must be retained for five years. 31 C.F.R. § 1010.410(f); OCC BSA/AML Examination Manual.…",
  "sha256": "b1e5…c7a3"
}
```

Every scrape run is also logged to `scrape_runs` (migration 018) with the connector name, URL, byte count, and status, so the SME can audit *when* the platform last saw the source.

**SME check at Stage 2:** ask a Fortress operator to show the most recent `scrape_runs` row for `occ_bsa`. Confirm the `fetched_at` is within the last 24 hours and `http_status = 200`.

### Stage 3 — Filter / normalize (what the connector emits into the rule store)

The connector's `_build_regulations()` method turns the raw text into a **structured ComplianceRule row**. This is the record the SME will see everywhere downstream:

| Column | Value |
|---|---|
| `rule_id` | `OCC-BSA-WIRE-001` |
| `regulator` | `OCC` |
| `category` | `BSA/AML` |
| `domain` | `wire_transfer` |
| `rule_type` | `dual_approval_threshold` |
| `rule_text` | *"Wire transfers of $10,000 or more require approval from two separate authorized employees. The initiator and approver must be different individuals. Records must be retained for five years. 31 C.F.R. § 1010.410(f); OCC BSA/AML Examination Manual."* |
| `threshold_amount` | `10000.00` |
| `threshold_currency` | `USD` |
| `cfr_citation` | `31 C.F.R. § 1010.410(f)` |
| `jurisdiction_level` | `federal` |
| `effective_date` | `2023-01-01` |
| `source_url` | (OCC manual URL from Stage 2) |
| `hash` | SHA-256 of `rule_text` (for change detection) |
| `is_active` | `TRUE` |

Notice what the filter step accomplishes:
1. Free-text prose becomes a **queryable row** with a specific `domain + rule_type`.
2. The dollar amount is lifted out of the sentence into `threshold_amount` — that is the number Pipeline 2 will compare against every wire transfer.
3. The citation is captured separately so an SME can trace every rule back to its source.

**SME check at Stage 3:** ask for an export of this row. Confirm `threshold_amount = 10000.00`, `cfr_citation` matches the source, and `is_active = TRUE`.

### Stage 4 — Obligation extraction (turning the rule into testable sentences)

The obligation extractor splits the `rule_text` into individual enforceable sentences. For this rule it produces three obligations:

| obligation_id | source_text | obligation_type | cfr_citation |
|---|---|---|---|
| `OCC-BSA-WIRE-001::OBL::001` | "Wire transfers of $10,000 or more require approval from two separate authorized employees." | `required` | 31 C.F.R. § 1010.410(f) |
| `OCC-BSA-WIRE-001::OBL::002` | "The initiator and approver must be different individuals." | `must` | 31 C.F.R. § 1010.410(f) |
| `OCC-BSA-WIRE-001::OBL::003` | "Records must be retained for five years." | `must` | 31 C.F.R. § 1010.410(f) |

Each obligation becomes an atomic unit that the platform must be able to prove is tested. When a passing test covers, say, OBL::002 (separation-of-duties check), the row is stamped `has_test_coverage = TRUE` with the test's scenario ID stored in `test_scenario_ids`.

**SME check at Stage 4:** confirm every "shall/must/required" clause in the source text appears as its own obligation row, and that the verb-class matches the sentence.

### Stage 5 — Rule resolution (finding the threshold at validation time)

When a wire transfer arrives at Pipeline 2, the code asks the resolver: *"What's the dual-approval threshold for wire_transfer?"* The resolver walks the four tiers in order:

1. **Postgres (Tier 1):** row above found — returns `10000.00`, source: `postgres`.
2. Redis / pgvector / hardcoded fallback are **not** consulted because Tier 1 hit.

If the row were ever missing (e.g. a fresh install with no sync), Tier 4 would supply the same `10000.00` from `_FALLBACK_THRESHOLDS["wire_transfer"]["dual_approval_threshold"]`, so the bank is never left with an "unknown" threshold.

**SME check at Stage 5:** ask for the `source` field returned by the resolver in production logs. In steady-state it should be `postgres` for every wire — a spike in `fallback` means Pipeline 1→Pipeline 2 sync is stale.

### Stage 6 — Validation on an incoming wire transfer

The bank's core system submits a wire to Pipeline 2's `/validate/wire-transfer` endpoint. Two illustrative wires, side by side:

**Wire A — should be BLOCKED (single approver, above threshold):**

Request:
```json
{
  "amount": 15000.00,
  "currency": "USD",
  "sender_name": "Acme Trading LLC",
  "beneficiary_name": "Global Supply Co",
  "beneficiary_country": "US",
  "is_international": false,
  "approvals": ["jsmith@bank.com"]
}
```

WireTransferValidator logic:
- `amount ($15,000) ≥ dual_approval_threshold ($10,000)` **AND** `len(approvals) < 2` → **BLOCK**.
- Also `amount ≥ ctr_threshold ($10,000)` → attach FLAG for CTR filing follow-up.

Response:
```json
{
  "action": "block",
  "decision": "BLOCK",
  "violations": [
    {"rule_id": "OCC-BSA-WIRE-001", "severity": "high", "reason": "dual approval required; only 1 approver present"},
    {"rule_id": "OCC-BSA-CTR-001", "severity": "medium", "reason": "CTR filing threshold met; ensure FinCEN Form 112 filed within 15 days"}
  ],
  "screening_id": "wire-2026-07-02-000123",
  "latency_ms": 42
}
```

**Wire B — should be ALLOWED (two approvers, above threshold, domestic):**

Request:
```json
{
  "amount": 15000.00,
  "approvals": ["jsmith@bank.com", "mwong@bank.com"],
  "beneficiary_country": "US",
  "is_international": false
}
```

WireTransferValidator logic:
- Dual-approval check satisfied (2 approvers).
- CTR check still flags for filing follow-up (≥ $10,000).
- Travel Rule check skipped (not international).

Response:
```json
{
  "action": "allow",
  "decision": "ALLOW_WITH_FLAG",
  "violations": [
    {"rule_id": "OCC-BSA-CTR-001", "severity": "medium", "reason": "CTR filing threshold met"}
  ],
  "screening_id": "wire-2026-07-02-000124"
}
```

**SME check at Stage 6:** ask for the audit log of the last week's wire-transfer validations. Confirm that (a) every wire ≥ $10,000 with < 2 approvers was BLOCKED, and (b) every wire ≥ $10,000 that was allowed generated a CTR follow-up task in ops.

### Stage 7 — Feedback into the ecosystem

- The block/allow decision is written to `screening_events` with the full request + decision for the OCC 5-year retention requirement (OBL::003).
- If a compliance test in the bank's test suite covers "wire ≥ $10k with 1 approver → BLOCK", Agent 6 writes `has_test_coverage = TRUE` on OBL::001, and it turns green on the CCO dashboard.
- If the OCC updates the manual (e.g. threshold changes to $8,000), the next scrape produces a new `hash`, and a `regulatory_change_records` row is written with the `change_delta`. All existing tests and validators still work — only the `threshold_amount` in the ComplianceRule row updates, so the dual-approval trigger automatically follows the source of truth.

**SME check at Stage 7:** open the CCO dashboard, filter to OCC → wire transfer, and confirm all three obligations show `has_test_coverage = TRUE` and no open `regulatory_change_records` for `OCC-BSA-WIRE-001` in the last 30 days.

---

*Regenerate this document after any change to `_FALLBACK_THRESHOLDS`, a new regulator connector, or a new alembic migration touching `compliance_rules` / `obligations` / `enforcement_*`.*
