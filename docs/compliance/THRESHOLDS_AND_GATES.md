# Fortress Labs — Compliance Thresholds & Validation Gates

**Audience:** CRO / CCO (bank side), Fortress Labs Compliance Lead
**Status:** Source of truth for what regulatory values and internal QA gates the platform applies. Review and sign-off required before each new bank cut-over.

---

## 1. Why this document exists

Every test the platform generates is gated by two layers of thresholds:

1. **Regulatory thresholds** — statutory values (CTR $10,000, SAR $5,000, OFAC fuzzy match 85%, etc.). These are *applied* to the bank's transactions, not opinion.
2. **Bank-specific overrides** — values the CCO chooses tighter than the statutory floor (e.g. internal dual-approval at $5,000 vs. the OCC floor of $10,000). These live in `bank_policies` / `rule_overrides` and override the defaults at runtime.
3. **Internal QA gates** — `quality_score` and `compliance_score` on LLM-generated test code. These are *not* compliance policy; they decide whether the generated test is mature enough to auto-open a GitHub PR. CI + human review still gate the merge into `main`.

This document lists every value in all three layers, where it's stored, and how to change it.

---

## 2. Regulatory thresholds (USA federal — defaults)

These are the **last-resort fallback** values the platform uses when neither `bank_policies` nor `compliance_rules` in the DB has been populated. They live in `src/services/compliance_rule_resolver.py:_FALLBACK_THRESHOLDS`.

| Domain | Key | Default | Regulator / Source | Notes |
|---|---|---|---|---|
| Wire transfer | `ctr_threshold` | **$10,000** | BSA / FinCEN (31 CFR 1010.311) | Cash transactions ≥ this amount → CTR filing required |
| Wire transfer | `dual_approval_threshold` | **$10,000** | OCC guidance | Floor; banks routinely tighten |
| Wire transfer | `travel_rule_threshold` | **$3,000** | FinCEN Travel Rule (31 CFR 1010.410(f)) | Originator info required ≥ this |
| SAR | `suspicious_threshold` | **$5,000** | FinCEN (31 CFR 1020.320) | SAR filing for suspicious activity ≥ this |
| SAR | `insider_threshold` | **$0** | FinCEN | SAR required at any amount for insider abuse |
| CTR | `cash_threshold` | **$10,000** | FinCEN | Same as wire CTR threshold |
| OFAC | `fuzzy_match_threshold` | **85%** | OFAC / Treasury guidance | ≥ this similarity → block / review |
| OFAC | `reporting_deadline_days` | **10 business days** | OFAC | Blocked transactions reported within this window |
| Reg E | `reporting_window_days` | **60 days** | CFPB Regulation E | Consumer reporting window for unauthorized EFT |
| Reg E | `provisional_credit_days` | **10 business days** | Reg E | Bank issues provisional credit within this |
| Reg E | `investigation_days` | **45 days** | Reg E | Dispute investigation deadline (existing accounts) |
| Reg E | `new_account_investigation_days` | **90 days** | Reg E | Same, for new accounts |
| CDD | `beneficial_ownership_pct` | **20%** | FinCEN CHG-2026-0412 (April 11 2026) | Was 25% before April 2026 amendment |

**Where else these values appear (must stay in sync):**

- `src/services/compliance_service.py` (Tier-3 service cache)
- `src/agents/understanding.py` (default values when ComplianceService misses)
- `src/agents/framework.py` (used to template wire/CTR scenarios)
- `src/agents/generation_bedrock.py` (fallback constants written into the LLM prompt — see §4)
- `src/mcp_servers/compliance_knowledge/server.py` (MCP tool surface)

---

## 3. Bank-specific overrides (CCO-controllable)

### Resolution order (3-tier resolver in `compliance_rule_resolver.py`)

1. **Postgres `compliance_rules` table** — full rule text + threshold from regulator scrape. Authoritative when present.
2. **Redis cache** — `compliance:resolved:*` keys, TTL 1 hour. Avoids hot-path DB hits.
3. **pgvector hybrid search** — top-K semantic match on rule embeddings + tsvector BM25, only when Postgres misses the exact key.
4. **Hardcoded fallback** — the table in §2. Used only if nothing above hits.

### Per-bank override tables (recently split — commit `f9ff401`)

| Table | Purpose | Editable via |
|---|---|---|
| `bank_policies` | Bank's institutional policy (e.g. our internal dual-approval is $5,000) | CCO dashboard → Policies tab; writes audit-logged |
| `rule_overrides` | Per-regulation override (e.g. apply Reg E to a wider product set) | CCO dashboard → Rules tab |
| `policy_regulation_mappings` | Joins policies to specific regulator rules | Auto-populated; CCO can adjust priority |

**Effect at workflow time:** `compliance_rule_resolver` checks `bank_policies` / `rule_overrides` *before* the fallback table. The first match wins.

### CCO override checklist (sign-off per bank)

- [ ] Reviewed default values in §2 — every threshold confirmed as correct for current USA federal regulation as of sign-off date.
- [ ] Bank's internal limits (CTR threshold, dual-approval, suspicious-activity floor) entered in `bank_policies` *or* explicitly inherited from federal defaults.
- [ ] OFAC fuzzy-match threshold confirmed (85% is industry default; some banks run tighter at 90%).
- [ ] Beneficial-ownership threshold confirmed at 20% post-CHG-2026-0412.
- [ ] Reg E timing windows confirmed — note the 90-day new-account window vs. 45-day existing-account window.

---

## 4. LLM-generated test code: validation gates

These are **internal QA** thresholds, not compliance policy. They determine whether the auto-generated pytest code is mature enough to auto-open a GitHub PR. **The CI pipeline + human reviewer remain the gate to merging anything into `main`.**

### Validation scoring (`src/agents/validation.py`)

The `ValidationAgent` runs three checks on every generated test file:

| Check | Function | Result |
|---|---|---|
| Python syntax | `ast.parse(test_code)` | Hard fail → `quality_score = 0` |
| Compliance keywords | keyword presence in code | `compliance_score` (0–100) |
| Quality scoring | syntax + compliance + coverage + idioms | `quality_score` (0–100) |

**Compliance keyword penalties** (`validation.py:_check_compliance`):

| Keyword absent | Score penalty | Rationale |
|---|---|---|
| `audit` | −15 | BSA recordkeeping |
| `10000` / `10_000` / `10,000` | −15 | CTR $10k threshold |
| `ofac` / `sdn` / `sanction` | −15 | OFAC screening |
| `sar` / `suspicious` | −10 | SAR consideration |
| `mask` / `redact` / `pii` | −10 | PII handling |
| `encrypt` / `tls` | −5 | GLBA Safeguards |

**Known calibration gap:** for non-AML rules (e.g. state licensing `FL-MSA-LIC-001`) the OFAC/SAR/CTR keywords don't naturally appear → compliance_score = 60 even on a perfectly valid licensing test. **Mitigation:** PR gate currently set to `compliance_score >= 50` and the PR body surfaces the score + missing-keyword list so reviewers see the gap. **Follow-up:** make scoring aware of `discovery.banking_patterns` so OFAC penalty only applies when the rule is OFAC-related.

### PR auto-open gate (`src/agents/execution.py`)

| Gate | Value | Effect |
|---|---|---|
| `is_valid` | `syntax_valid AND compliance_score >= 50` | Required to open a PR |
| `quality_score >= 80` | Threshold for "approved" recommendation in PR body | Cosmetic — doesn't gate PR open |

### LLM model + generation settings (`src/services/bedrock_service.py`, `src/utils/config.py`)

| Setting | Value | Override |
|---|---|---|
| Model | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | `BEDROCK_MODEL` env / `/fortress/<bank>/BEDROCK_MODEL` SSM |
| `max_tokens` | 8,000 | Hardcoded in `generation_bedrock.py` |
| `temperature` | 0.3 | Hardcoded; low for deterministic code |
| Per-agent model | Sonnet 4.5 for Generation; Haiku 4.5 for Discovery/Framework/Validation | `P2_AGENT_*_MODEL` env vars |

### LLM prompt constants (`src/agents/generation_bedrock.py:_build_prompt`)

If the rule context omits a numeric threshold, the prompt instructs the LLM to use these fallbacks (must match §2):

```
ctr_threshold = 10000
sar_threshold = 5000
travel_rule_threshold = 3000
wire_dual_approval_threshold = 10000
ofac_fuzzy_threshold = 85
beneficial_ownership_pct = 20
```

---

## 5. Audit trail

Every workflow run records:

- `workflow_executions.context_data` — the JIRA story + rule context used as input.
- `workflow_executions.artifacts` — the generated test code, validation report (score breakdown), and GitHub PR URL.
- `workflow_executions.rule_id` — link back to the specific `compliance_rules` row in effect at run time.
- CW Logs `/fortress/pipeline2/<bank>` — every agent's started/completed events with workflow_id, plus the full validation breakdown (`syntax_valid`, `syntax_errors`, `compliance_score`).

A CCO can reconstruct *exactly* which thresholds applied to any given workflow run by joining `workflow_executions.rule_id` → `compliance_rules` and checking `bank_policies` as of the run timestamp.

---

## 6. Recommended dashboard surfacing

| Panel | Audience | Read / Write | Backed by |
|---|---|---|---|
| **Compliance Policy** | CRO / CCO | Read + write | `bank_policies`, `rule_overrides` |
| **Effective Thresholds (this bank)** | CRO / CCO | Read only | Live read from `compliance_rule_resolver` |
| **Regulatory Changes Feed** | CCO | Read | `regulatory_change_records` |
| **Test Generation Settings** | Admin / Audit | Read only | `settings.BEDROCK_MODEL`, `validation.py` constants |
| **Workflow Audit Log** | CCO / Auditor | Read | `workflow_executions` + `artifacts` |

The first two panels are the CRO/CCO-facing surface. The fourth is an admin/audit view — visible so reviewers can confirm *which gating logic approved any historic test*, but not something the CCO tunes.

---

## 7. Change-control process

| Change type | Approver | Mechanism |
|---|---|---|
| Federal regulatory value change (e.g. another CHG-style amendment) | Fortress Compliance Lead | Update `_FALLBACK_THRESHOLDS` + migration backfilling `compliance_rules`; commit + PR review |
| Bank-specific override (CCO tightens internal limit) | Bank CCO | Dashboard write → `bank_policies` UPSERT; audit-logged |
| Validation gate threshold (e.g. `compliance_score >= 50`) | Fortress Engineering Lead + CCO sign-off | Code change + commit, no DB component |
| LLM model swap | Fortress Engineering Lead | SSM parameter update; no code redeploy needed |

---

## 8. Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| Bank CRO | | | |
| Bank CCO | | | |
| Fortress Compliance Lead | | | |
| Fortress Engineering Lead | | | |
