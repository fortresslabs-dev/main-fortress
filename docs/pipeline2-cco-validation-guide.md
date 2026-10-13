# Pipeline 2 — CCO Validation Guide

**Audience:** Chief Compliance Officer + compliance engineering
**Scope:** Everything running inside customer (bank) infrastructure — validators, agents, dashboard, exports
**Companion docs:** `docs/runbooks/agent-4-sync.md`, `docs/runbooks/ofac-calibration-gold-set.md`, `docs/setup/operations.md`

---

## 1. Use case coverage (dashboard + API)

### 1.1 Customer-data use cases (transactional / operational)

| Use case | Regulation | Dashboard entry | API endpoint | Data classification |
|---|---|---|---|---|
| Wire transfer BSA/AML | OCC BSA/AML | (invoked by bank core banking) | `POST /validate/wire-transfer` | Customer PII (stays local) |
| OFAC sanctions screening | OFAC SDN | `/ofac`, `/dashboard/search`, `/dashboard/cases` | `POST /validate/ofac-screen`, `POST /api/v1/screen/sdn` | Customer PII (stays local) |
| Small-business loan (1071) | CFPB Section 1071 | (invoked by loan origination) | `POST /validate/loan-application` | Applicant demographics (stays local) |
| Screening case management | OFAC / BSA | `/dashboard/cases`, `/dashboard/cases/:id` | `GET /api/v1/dashboard/screening/cases`, `POST /.../decision` | Case audit trail |
| Analyst screening workbench | OFAC | `/dashboard/search` | `POST /api/v1/dashboard/screening/search` | Live screening |
| Rules performance / TP-FP | OFAC / BSA | `/dashboard/rules-perf` | `GET /api/v1/dashboard/screening/rules-perf`, `PATCH /.../rules/{id}` | Governance metrics |

### 1.2 Governance / examiner-facing use cases (feed + control)

| Use case | Dashboard route | API endpoint(s) |
|---|---|---|
| Examination Readiness (CCO/CRO/CIO lenses, 10 dimensions) | `/readiness` | `GET /api/v1/dashboard/readiness`, `/risk-matrix`, `/cro`, `/cio`, `/findings` |
| Compliance rules inventory + test status | `/rules` | `GET /api/v1/dashboard/rules`, `PATCH /rules/{id}/status`, `POST /rules/{id}/generate-test` |
| Regulatory change inbox + sign-off | `/changes` | `GET /api/v1/dashboard/changes`, `POST /changes/{ref}/signoff`, `POST /changes/signoff/bulk` |
| JIRA workflow runs (6-agent pipeline) | `/workflows` | `GET /api/v1/dashboard/workflows`, `GET /workflows/{id}`, `POST /approvals` |
| Enforcement actions feed | `/enforcement` | `GET /api/v1/dashboard/enforcement` |
| Obligations coverage | `/obligations` | `GET /api/v1/dashboard/obligations` |
| OFAC / SDN engine health | `/ofac` | `GET /api/v1/dashboard/ofac-health` |
| Examiner evidence & reports (6 report types, WORM) | `/reports` | `POST /api/v1/exports`, `GET /exports`, `POST /exports/{id}/sign`, `GET /exports/{id}/download` |
| Executive Today (board-level daily) | `/dashboard/executive` | `GET /api/v1/dashboard/screening/executive-today` |
| Vector Intelligence (model governance, Admin/CRO) | `/dashboard/vector` | `GET /api/v1/dashboard/screening/vector-intel` |

### 1.3 Ops / infra endpoints (not CCO-facing but referenced during audit)

- `GET /health`, `GET /health/ready`, `GET /health/sync` — liveness + sync freshness
- `GET /internal/sync/counts`, `POST /internal/sync/trigger` — HMAC-gated sync diagnostics
- `GET /api/v1/architecture/data-flow` — TPRM / vendor due-diligence manifest

**Total P2 API surface: 59 endpoints across 8 categories.** Full list: run `curl http://localhost:8002/openapi.json | jq '.paths | keys'`.

---

## 2. How to validate Pipeline 2

Three layers — run in order for any release or new-bank deploy.

### 2.1 Schema + code sanity

```bash
python -m py_compile src/main_pipeline2.py
ruff check src/ --fix && black src/
make schema-check                     # scripts/verify_schema.py — DB matches migrations
python -m alembic current             # confirm at expected head
```

### 2.2 Automated test suite (CI-CD)

The CI-CD workflow (`.github/workflows/ci-cd.yml`) is the release gate:

| Stage | What it validates | Command |
|---|---|---|
| `ci` | pytest against ephemeral Postgres+Redis (both P1 + P2 modes) | `make test` |
| `test-demo` | API contract / demo-readiness suite | `pytest tests/demo/` |
| `dashboard-tests` | Vitest — React components | `cd dashboard && npm test` |
| `dashboard-e2e` | Playwright F1–F5 golden paths against local backends | `cd dashboard && npm run e2e` |
| `nightly-staging-e2e` | Playwright vs staging (Slack alert, non-blocking) | scheduled cron |

Validator integration tests: `tests/integration/test_validators_e2e.py` — proves wire/OFAC/loan validators never leak customer data off-premise.

### 2.3 Live endpoint smoke tests (post-deploy)

Cold-start any new bank with these curl calls before handing off to the CCO. All examples assume `$API=https://api.<bank>.com` and `$JWT=<Cognito access token from dashboard login>`.

**Health + sync freshness**

```bash
curl -s $API/health | jq
curl -s $API/health/sync | jq          # expect age_days < 2, rule_count > 0
```

**Wire transfer — PASS example**

```bash
curl -s -X POST $API/validate/wire-transfer \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 4500,
    "currency": "USD",
    "sender_name": "Acme Corp",
    "beneficiary_name": "Widget LLC",
    "beneficiary_country": "US",
    "is_international": false,
    "approvals": []
  }' | jq
# Expect: decision=PASS, violations=[]
```

**Wire transfer — REVIEW (needs dual approval)**

```bash
curl -s -X POST $API/validate/wire-transfer \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 25000,
    "currency": "USD",
    "sender_name": "Acme Corp",
    "beneficiary_name": "Global Traders SA",
    "beneficiary_country": "SG",
    "is_international": true,
    "approvals": [{"approver_id":"teller-42","role":"teller"}]
  }' | jq
# Expect: decision=REVIEW, violation on wire_dual_approval_threshold ($10k)
```

**OFAC screen — clean party**

```bash
curl -s -X POST $API/validate/ofac-screen \
  -H "Content-Type: application/json" \
  -d '{"party_name":"John Q Public","party_country":"US"}' | jq
# Expect: decision=PASS, final_score < 0.85
```

**OFAC screen — synthetic SDN hit (demo seed)**

```bash
curl -s -X POST $API/validate/ofac-screen \
  -H "Content-Type: application/json" \
  -d '{"party_name":"Ivan Petrov","party_country":"RU"}' | jq
# Expect: decision=HOLD (>=0.92) or REVIEW (>=0.85), match_detail with sdn_number
```

**Loan application — 1071 missing demographic fields**

```bash
curl -s -X POST $API/validate/loan-application \
  -H "Content-Type: application/json" \
  -d '{
    "naics_code": "541511",
    "business_gross_revenue": 850000,
    "time_in_business": "2-5_years",
    "number_of_workers": 12
  }' | jq
# Expect: decision=REVIEW, violations flag missing race/sex/ethnicity fields
```

**Analyst screening workbench (Cognito)**

```bash
curl -s -X POST $API/api/v1/dashboard/screening/search \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"Ivan Petrov","country":"RU","external_customer_id":"CUST-1234"}' | jq
```

**Readiness score for CCO lens**

```bash
curl -s $API/api/v1/dashboard/readiness -H "Authorization: Bearer $JWT" | jq
# Expect: composite score 0-100, 10 dimensions, biggest_drag, band
```

**Generate examiner evidence pack (WORM-locked)**

```bash
curl -s -X POST $API/api/v1/exports \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"bank_id":"<BANK_ID>","type":"examiner_package","as_of":"2026-07-08","format":"pdf"}' | jq
# Then poll GET /api/v1/exports/{artifact_id} until status=complete
```

### 2.4 CCO acceptance criteria

Before signing off Pipeline 2 for a bank:

- [ ] All three `/validate/*` endpoints return the expected decisions on the six smoke examples above
- [ ] `/health/sync` `age_days < 2` and `rule_count` matches P1 count within tolerance
- [ ] Readiness score ≥ agreed band (e.g. ≥ 70) with no `biggest_drag` in scope-critical dimensions
- [ ] At least one end-to-end change: `POST /changes/{ref}/signoff` recorded in audit log
- [ ] One WORM-locked examiner evidence pack downloaded and verified against version hash
- [ ] JIRA webhook → 6-agent pipeline → GitHub PR completes for one test rule
- [ ] OFAC calibration gold-set F1 ≥ 0.90 blended, recall ≥ 0.95 (individual/alias) — see §4

---

## 3. New-bank deployment — custom suggestions

### 3.1 Deployment sequence

```bash
# 1. Fortress team — register the bank with P1
export FORTRESS_INTERNAL_SECRET="..."
./scripts/register-bank.sh \
  --bank-id firstnational \
  --bank-name "First National Bank" \
  --bank-ip 203.0.113.45 \
  --bank-profile bank-firstnational \
  --tier standard

# 2. Bank team — populate env template
cp infra/setup/bank.env.template infra/setup/bank.env.firstnational
# Fill in: JIRA_URL, JIRA_TOKEN, JIRA_PROJECT_KEY, GITHUB_ORG, GITHUB_REPO, GITHUB_PAT

# 3. Deploy P2 infra (VPC → data → compute → dashboard → security)
TRIAL_MODE=false ./infra/scripts/deploy-p2.sh firstnational production \
  --skip-security false --migrations-auto true

# 4. Push + deploy code
make push-p2 BANK_ID=firstnational IMAGE_TAG=v1.0.0 AWS_PROFILE=fortress-prod
make deploy-p2-bank BANK_ID=firstnational AWS_PROFILE=bank-firstnational

# 5. Dashboard SPA (deploy.sh is NOT in Actions — run directly)
cd dashboard && ./deploy.sh firstnational production

# 6. Register bank NAT EIP with P1 WAF (idempotent)
./infra/scripts/register-bank-ip.sh firstnational 203.0.113.45
```

### 3.2 Per-bank customization surface

| Surface | Where | Notes |
|---|---|---|
| JIRA project + issue type | `bank.env.{BANK_ID}` → CFN `JiraProjectKey`, `JiraIssueType` | Team-managed (MDP) projects only accept `Idea`. Default = `Idea`. |
| JIRA status names | SSM `/fortress/{BANK_ID}/JIRA_STATUS_*` | Must match bank's workflow states (In Review, Needs Revision, Done). |
| GitHub org + repo + PAT | `bank.env.{BANK_ID}` → SSM `/fortress/{BANK_ID}/GITHUB_*` | Fine-grained PAT, repo write scope. |
| Cognito user pool + client | Auto-created by `pipeline2-dashboard.yml` | Groups auto-provisioned: `CCO`, `CRO`, `CIO`, `Analyst`, `Admin`. |
| Dashboard origins | CFN param `ALLOWED_ORIGINS` (JSON array) | MUST be JSON array; empty or `"*"` hard-fails in production. |
| Bank domain | CFN param `BankDomain` | `TRIAL_MODE=true` skips ACM, uses `*.cloudfront.net`. |
| Alert email | CFN param `AlertEmail` | CloudWatch alarms + operational SNS. |
| VPC CIDR | CFN param `VpcCidr` (default `10.2.0.0/16`) | Unique per bank if peering. |
| P1 API key | Secrets Manager `fortress/{BANK_ID}/pipeline1-api-key` | Fortress-issued via `register-bank.sh`; never in git. |
| DB password | Secrets Manager `fortress/{BANK_ID}/db-password` | Auto-generated, non-human. Rotation currently disabled — see memory `project_p1_secret_rotation_gap`. |
| JWT signing secret | Secrets Manager `fortress/{BANK_ID}/jwt-secret` | Regenerate for rotation. |
| OFAC engine mode | Env `OFAC_SCREENER_MODE=legacy\|vector` | Flip only after §4 calibration sign-off. |
| OFAC per-class thresholds | SSM `/fortress/p2/ofac/threshold/{class}` | Loaded once after CCO approval. |
| Validation rate limit | Env `FORTRESS_P2_VALIDATE_RPM` (default 60) | Raise for load tests. |
| WAF blocklist | IPSet `fortress-p2-blocklist-{BANK_ID}` + rule priority 0 | XFF-aware (`IPSetForwardedIPConfig`). |
| Feature flags | Env: `LLM_MOCK_MODE`, `DEV_MODE`, `JIRA_AUTH_MODE` | Use sparingly in prod. |

### 3.3 Known gotchas (from prior deploys)

1. **Task definition semver pin** — P2 TDs pin to `<feature>-YYYYMMDD-HHMM` tags. `make deploy-p2-bank` alone re-pulls the *same* tag. To ship new code, register a new TD revision pointing at the new tag, then `update-service`. Verify with `/openapi.json`, not ECS RUNNING status.
2. **exports-worker is a separate TD family** — hot-fixes touching `src/exports/*` must rebuild + retag + re-register BOTH `fortress-pipeline2-{bank}` AND `fortress-p2-exports-worker-{bank}`. Otherwise the worker keeps crashing silently.
3. **`ALLOWED_ORIGINS`** — must be JSON array. Config gate rejects comma-separated form and `"*"` in production.
4. **Cognito env injection** — TD needs `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` in `secrets` block (from SSM). Missing → 401 across every dashboard endpoint.
5. **WAF IP set reset** — any P1 compute-stack update resets `fortress-p1-bank-ips-{env}`; re-run `register-bank-ip.sh` for every registered bank. EIPs mirrored in SSM `/fortress/banks/{BANK_ID}/nat-eip`.
6. **Alembic on startup** — once P2 runs `alembic upgrade head` to a new revision, older images crash-loop. Rollback needs `alembic stamp <prev_head>` first.
7. **testbank profile mismatch** — Makefile references `bank-testbank` profile but default AWS creds reach testbank in practice. Dashboard GH Actions NOT wired — always run `dashboard/deploy.sh testbank production` directly.
8. **Bedrock gates** (once per account, once per model family):
   - Anthropic use-case form submitted for Sonnet 4.x AND Haiku 4.5
   - Task role has `aws-marketplace:Subscribe/ViewSubscriptions/Unsubscribe` (Resource: `*`)
   - All model IDs go through `settings.BEDROCK_MODEL` with `us.` inference-profile prefix — never hardcode.

---

## 4. Golden dataset — OFAC calibration

**Purpose:** Compliance sign-off gate before flipping `OFAC_SCREENER_MODE=vector`.

### 4.0 Per-bank ownership (important)

**The golden dataset is per-bank, not shared.** The CSV checked into the repo is a
synthetic seed to prove the pipeline shape — every positive row carries
`TBD_OPS_FILL_NNN` and the calibration script will report 0% recall against it
until ops replace the placeholders with the bank's own 12-month screening
history. Reasons:

- **Customer mix differs** — community-bank domestic wires vs correspondent-bank global entities produce different name distributions; FP calibration needs *your* clean wires.
- **Risk appetite differs** — the CCO acceptance bar (F1 ≥ 0.90, recall ≥ 0.95 for individual/alias) is a floor; each CCO can bump precision on their own gold set.
- **Regulatory posture differs** — sanctions-heavy portfolios may want per-program recall bars (SDGT, RUSSIA-EO14024) that a community bank wouldn't.
- **Data residency** — real screening history contains customer PII and must never leave the bank's environment.

Shared vs per-bank:

| Item | Shared (repo) | Per-bank |
|---|---|---|
| CSV schema + column order | ✅ | |
| Synthetic seed rows | ✅ | |
| Calibration script | ✅ | |
| Acceptance criteria (F1/recall bars) | ✅ | |
| Real gold-set rows | | ✅ |
| Per-class threshold values | | ✅ |
| SSM threshold parameters | | ✅ (per AWS account) |

### 4.1 Artifacts

| Artifact | Path | Role |
|---|---|---|
| Gold-set CSV | `scripts/setup/data/ofac_calibration_gold.csv` | 202 rows (100 positives + 100 clean) across 4 variant classes: `individual`, `entity`, `vessel`, `alias`. Columns: `name`, `expected_sdn`, `variant_class`, `notes`. Contains `TBD_OPS_FILL_NNN` placeholders — ops replaces with real 12-month screening history. |
| Runbook | `docs/runbooks/ofac-calibration-gold-set.md` | 4-step SOP: pull screening history → label by class → verify against live SDN → replace TBDs. Includes CCO sign-off acceptance criteria. |
| Calibration script | `scripts/setup/calibrate_ofac_thresholds.py` | Sweeps cosine + blended thresholds against gold set, outputs F1-optimal cutoffs + SSM parameter suggestions. |
| Governance evidence seed | `scripts/setup/data/governance_evidence_testbank.csv` | 8 rows anchoring compliance controls (board BSA/OFAC acks, officer designation, committee charter, training, risk assessment) to WORM-locked S3 artifacts. |
| Demo SDN seed | `scripts/demo/seed.py` | Synthetic SDN entries (DEMO- prefix) for UI/E2E. Not for prod use. |
| Testbank dashboard seed | `scripts/seed_testbank_dashboard.py` | Bank profile + 50 findings + 50 change dispositions for readiness scorecard smoke tests. |
| Validator E2E tests | `tests/integration/test_validators_e2e.py` | 6 mock SDN entries + 2 mock wire rules — proves no customer data leaves premise. |

### 4.2 Calibration workflow

```bash
# 1. Ops replaces TBD_OPS_FILL_NNN in ofac_calibration_gold.csv with real SDN numbers
#    from bank's 12-month screening history (≥100 rows per class, positives AND clean).

# 2. Engineering runs calibration (against prod Aurora with embedded ofac_sdn_vectors)
PIPELINE_MODE=pipeline1 AWS_REGION=us-east-1 \
python -m scripts.setup.calibrate_ofac_thresholds \
  --gold scripts/setup/data/ofac_calibration_gold.csv \
  --out  scripts/setup/data/ofac_thresholds.json \
  --verbose

# 3. CCO reviews console output — acceptance:
#    - F1 ≥ 0.90 blended across all classes
#    - Recall ≥ 0.95 for individual and alias classes
#    - No systematic false negatives on OFAC-priority programs (SDGT, IRAN, RUSSIA-EO14024)

# 4. On approval, load per-class thresholds to SSM
for class in individual entity vessel alias; do
  aws ssm put-parameter --name "/fortress/p2/ofac/threshold/$class" \
    --value "<value from ofac_thresholds.json>" --type String --overwrite
done

# 5. A/B shadow week — run legacy + vector in parallel, compare decisions on 1k
#    synthetic + 1k real wires before flipping OFAC_SCREENER_MODE=vector.
```

### 4.3 SSM path — bank namespacing

Today the calibration script writes to `/fortress/p2/ofac/threshold/{class}`
(bank-agnostic path). Because each bank runs P2 in **its own AWS account**, the
account boundary provides the namespace — no cross-bank collision is possible.

Namespacing per bank (`/fortress/p2/{bank_id}/ofac/threshold/{class}`) becomes
required if any of these change:

1. **Multi-tenant hosting** — multiple banks share one AWS account. Without the bank ID segment, everyone reads the same global threshold.
2. **Cross-account operator role** — a central management-account role reads/writes thresholds across banks; namespacing makes audit trails unambiguous.
3. **Convention consistency** — rest of the repo uses `/fortress/p2/{bank}/…` (e.g. exports-worker log paths, per-bank env), and `/fortress/{client}/ofac/threshold/*` appears in some runbooks. The OFAC path is the odd one out.

Three touchpoints if we namespace:
- `scripts/setup/calibrate_ofac_thresholds.py:340` — write path
- `OFACScreener` init — read path (still hardcoded to `OFAC_FUZZY_THRESHOLD` today; wire per-class SSM read at the same time)
- Docs sweep (~10 files under `docs/` and `alembic/versions/036*.py`)

**Current recommendation:** convention nit, not a correctness bug — safe to defer until a multi-tenant deploy is on the roadmap.

### 4.4 Constants that must stay in sync

`src/services/compliance_rule_resolver.py` — these are hardcoded fallbacks; the DB values (from P1 sync) win in production:

```python
"beneficial_ownership_pct":       20.0   # FinCEN CHG-2026-0412
"wire_dual_approval_threshold":   10000.0
"ctr_threshold":                  10000.0
"sar_threshold":                  5000.0
"travel_rule_threshold":          3000.0
"ofac_fuzzy_threshold":           85.0   # 85% → REVIEW; 92% → HOLD
```

Any override lives in DB and can be surfaced through `PATCH /api/v1/dashboard/rules/{rule_id}/status` (CCO-gated).

---

## 5. Study list for CCO validation

Ordered by validation importance. Everything below the Tier 1 block is reference material — pull it in only when a specific validation gap surfaces.

### 5.1 Tier 1 — Must study for CCO sign-off

| Dataset | Path | Study focus | Why |
|---|---|---|---|
| **OFAC calibration gold set** | `scripts/setup/data/ofac_calibration_gold.csv` | Row shape, class balance (individual / entity / vessel / alias), TBD placeholder pattern | Blocks `OFAC_SCREENER_MODE=vector` flip until bank-specific rows replace synthetic seed |
| **Calibration runbook** | `docs/runbooks/ofac-calibration-gold-set.md` | Ops workflow §4, acceptance bars §7 (F1 ≥ 0.90, recall ≥ 0.95 for individual/alias) | The SOP each bank's compliance ops follows |
| **Validator E2E tests** | `tests/integration/test_validators_e2e.py` | 6 mock SDN entries (Al-Qahtani, Petrov, Kim variants) + 2 mock wire rules with expected decisions | Ground truth for what "correct" looks like across wire, OFAC, 1071 |

### 5.2 Tier 2 — Study to understand the surface

| Dataset | Path | Study focus |
|---|---|---|
| Demo SDN seed | `scripts/demo/seed.py` | 10 DEMO- individuals + 6 entities + 5 vessels with aliases + programs — the shape of prod SDN records |
| JIRA webhook fixture | `tests/fixtures/jira_webhook_payload.json` | Payload shape the 6-agent pipeline receives; needed to reason about test-generation triggers |
| Governance evidence seed | `scripts/setup/data/governance_evidence_testbank.csv` | 8 evidence types (board acks, officer designation, committee charter, training, risk assessment) — the audit-trail schema |

### 5.3 Tier 3 — Study to validate end-user flows

| Dataset | Path | Study focus |
|---|---|---|
| Playwright golden paths | `dashboard/e2e/f{0..5}_*.spec.ts` | 6 scenarios: first paint, SDN screen, rule sync/changes, webhook→approval, executive overview, vector-intel drilldown |
| Testbank dashboard seed | `scripts/seed_testbank_dashboard.py` | Bank profile + 50 findings + 50 change dispositions — what the readiness scorecard looks like populated |

### 5.4 What is NOT a golden dataset (skip)

- **`TBD_OPS_FILL_NNN` positive rows** in `ofac_calibration_gold.csv` — placeholders by design; calibration reports 0% recall until real bank data replaces them.
- **`DEMO-*` SDN entries** — synthetic UI plumbing, do not overlap with real SDN numbers, cannot be used for prod validation.
- **Testbank governance evidence** — schema exercise with fake `attested_by` / `artifact_ref`; validates the shape, not real controls.

### 5.5 Recommended study order

1. **Read** `docs/runbooks/ofac-calibration-gold-set.md` end-to-end (~10 min) — the whole calibration story.
2. **Open** `ofac_calibration_gold.csv` — confirm class balance and TBD pattern; agree the bank will supply real rows.
3. **Skim** `test_validators_e2e.py` — see which wire / OFAC / 1071 scenarios the code is proven against today.
4. **Run** the smoke curls from §2.3 against staging with the demo seed loaded — shows PASS / REVIEW / HOLD in action.
5. **Review** Playwright `f1_sdn_screen.spec.ts` — mirrors what an analyst does on the dashboard.

---

## 6. Next actions for CCO discussion

1. **Confirm first-live use case** — wire / OFAC / 1071 (pick one to go live first).
2. **Kick off per-bank gold-set population** — assign ops to pull 12 months of screening history and replace TBDs in `ofac_calibration_gold.csv`. Repo seed is synthetic; each bank owns its own gold set (see §4.0).
3. **Agree per-class OFAC acceptance thresholds** — CCO signs off F1 / recall bars before we load SSM values.
4. **Decide SSM namespacing timing** — namespace `/fortress/p2/{bank}/ofac/threshold/{class}` before any multi-tenant deploy; defer if per-account isolation stays (see §4.3).
5. **Review readiness dimensions weighting** — confirm the persona lens (CCO vs CRO vs CIO) and biggest-drag surfacing matches examiner narrative.
6. **Confirm reporting cadence** — which of the six examiner report types are quarterly vs on-demand.
7. **Bank onboarding parameters** — collect JIRA project/issuetype, GitHub org/repo, allowed origins, and NAT EIP for every new bank before deploy.
8. **Confirm A/B shadow duration** — runbook §8 defaults to one week of `OFAC_SCREENER_MODE=legacy` in parallel with vector; CCO to approve disagreement rate before flip.

---

## 7. Open items carried from CLAUDE.md

Direct traceability against the repo's active action list — flag any these that block a CCO milestone:

- `scripts/setup/verify_sdn_prereqs.py` not yet run against prod Aurora.
- `python -m src.jobs.embed_sdn_variants` + `alembic upgrade 022` sequence pending first full scrape.
- Real bank screening history not yet substituted for TBD rows.
- Calibration dry-run against prod Aurora pending.
- Approved thresholds not yet loaded into SSM.
- `OFACScreener` still reads a single `OFAC_FUZZY_THRESHOLD` — per-class SSM read not wired.
- End-to-end UpdateService test not yet run post-P1 deploy.
- JIRA webhook → 6-agent pipeline not yet verified with a pilot bank.
- Migrations 016/017 not yet confirmed applied on the pilot P2 Aurora.
- `PIPELINE_MODE=pipeline2` SSM injection on ECS not yet verified end-to-end.
