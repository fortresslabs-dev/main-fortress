# Examination-Ready Dashboard — Action Items for CCO / CRO / CIO

**Audience:** Chief Compliance Officer, Chief Risk Officer, Chief Information
Officer of a Fortress-Labs-onboarded bank.

**Purpose:** Explain what the Fortress P2 dashboard generates automatically vs.
what the bank must contribute before the examination PDF is a defensible
artifact for an OCC / FDIC / Federal Reserve / CFPB exam.

**Why this document exists:** A "real PDF" requires real data. Fortress can
deliver real *regulatory* data (rules, change records, enforcement actions,
OFAC lists) automatically, but every line of the exam package that asserts
something about *your bank* — what controls you operate, who owns them, what
you tested and when, what findings are open, who signed off on what — is
judgment that only bank staff can make and attest to. This document lists
those judgment calls and assigns each to the responsible officer.

---

## For new banks — start here

If you are a CCO / CRO / CIO at a bank that has just been onboarded to
Fortress, read in this order:

1. **§4 Phased rollout** — your 90-day timeline from onboarding to steady
   state, with explicit deliverables per phase.
2. **§3 Action items by role** — find your role (CCO / CRO / CIO) and
   work the checklist top-down.
3. **§5 Acceptance criteria** — the exact conditions under which the
   examination PDF is defensible. Treat this as your gating checklist
   before declaring exam-ready.
4. **§7 Pilot / demo seed data — what's synthetic and how to replace it**
   — *only* relevant if your dashboard was pre-seeded with placeholder
   bank-side data during pilot (e.g. the `testbank` reference
   environment). Lists every row that must be replaced before any real
   exam cycle.

If your tenant was provisioned fresh (no pilot seed), skip §7 and start
at §4.

---

## 1. What the product gives you automatically (no bank action needed)

These populate from Fortress P1's continuous scrape and require no bank input
beyond initial onboarding:

| Source | What it gives you | Refresh |
|---|---|---|
| `compliance_rules` | The current federal + state regulatory rule set, with citation, enforcement risk score, and obligation text | Daily P1 sync |
| `regulatory_change_records` | Detected mutations of those rules — added / modified / removed, with semantic diff | Daily P1 sync |
| `enforcement_actions` | Public enforcement actions issued by OCC / FDIC / FRB / CFPB, with citation, action type, and penalty amount | Daily P1 sync |
| `ofac_sdn_entries` + `ofac_sdn_vectors` | Current OFAC SDN list, vector-indexed for the screener | Daily Treasury pull |
| `ofac_list_versions` | Versioned audit trail of each SDN snapshot ingested | On each pull |

These four data sets are sufficient to render the `board_report` tile and to
back the `change_log` and `ofac_attestation` tiles with current regulatory
state. They are **not** sufficient on their own to render `examiner_package`,
`mra_tracker`, or `coverage`, because those tiles describe the bank's
internal posture, not the external regulatory landscape.

---

## 2. What requires bank action (the gap that blocks a defensible PDF)

The dashboard reads from six bank-side tables that no scraper can populate.
Without them, the tiles below render empty or with projected/placeholder
data:

| Bank-side table | Tiles that depend on it | Who owns it |
|---|---|---|
| `bank_profile` | All — bank identity on every page header | CCO + CFO |
| `bank_officers` | `examiner_package`, all owner-FK lookups | CCO + HR |
| `controls` | `examiner_package`, `coverage` | CCO |
| `control_rule_mappings` | `coverage` (this is where the % comes from) | CCO |
| `control_tests` + `test_executions` | `examiner_package` (the "tested?" column) | CIO + CCO jointly |
| `findings` | `mra_tracker`, parts of `board_report` | CCO |
| `change_record_dispositions` | `change_log` (without this, every change is "undecided") | CCO |
| `ofac_validations` | `ofac_attestation` (quarterly attestation row) | BSA Officer (reports to CCO) |
| `bank_policies` | Policy-gap panel of `change_log` | CCO |

---

## 3. Action items by role

### 3.1 CCO — Chief Compliance Officer

The CCO owns the majority of the examination package because it is, at root,
a compliance artifact. Every item below produces a row in one of the
bank-side tables.

**Onboarding (one-time, before first exam cycle):**

1. **Confirm `bank_profile`** — legal name, charter number, primary
   regulator, secondary regulators, fiscal year end, total assets and
   as-of date. *Why it matters:* every PDF page header asserts this; an
   examiner will reject a package with the wrong charter number.

2. **Roster `bank_officers`** — at minimum: BSA Officer, CCO, CRO,
   Internal Audit head, Audit Committee Chair, Board Chair, CEO. Each
   needs full name, email, start date. *Why it matters:* every control,
   finding, and disposition must have a named human owner; "BSA Officer"
   as a role is not sufficient — examiners want a person.

3. **Build the `controls` inventory** — for each compliance rule the bank
   is subject to, document the control(s) the bank operates to satisfy
   it. Each control needs: a unique ID, a name, a narrative description,
   the owning role, the named owning person, and a control type
   (preventive / detective / corrective). *Why it matters:* without this,
   the `examiner_package` tile is empty and the `coverage` tile is 0%.

4. **Populate `control_rule_mappings`** — for each (control, rule) pair,
   record whether the control fully addresses the rule, partially
   addresses it, or is a compensating control. Record the reviewer name
   and date. *Why it matters:* this table is the literal source of the
   `coverage` tile percentage. No mappings → 0% coverage → instant red
   flag.

5. **Document any open `findings`** — for each open MRA / MRIA / MRBA /
   violation / observation from prior exam cycles, enter:
   - Type, severity, regulatory source
   - The OCC 5 Cs: concern, cause, consequence, corrective action,
     commitment
   - Root cause + whether identified
   - Owner (FK to `bank_officers`)
   - Identified date, due date, current status
   - Evidence S3 URI as remediation progresses
   - `repeat_flag` if it recurred from a prior cycle
   *Why it matters:* an exam package missing known open MRAs is a
   credibility-killer; an examiner who finds an MRA outside the package
   that they expected to be inside it will escalate.

**Ongoing (continuous, not just at exam time):**

6. **Disposition every new regulatory change record** — as `change_log`
   surfaces new entries from P1, the CCO (or delegate) must mark each
   as `acknowledged`, `requires_action`, or `not_applicable`, with a
   sign-off name and timestamp. `requires_action` rows must reference an
   implementation link (JIRA / GitHub PR / policy doc). *Why it matters:*
   `change_log` tile renders the percentage of dispositioned changes;
   examiners read this as "is this bank keeping up with regulatory
   change?"

7. **Quarterly OFAC attestation** — the BSA Officer (under CCO oversight)
   must produce one `ofac_validations` row per quarter documenting:
   scope of validation, methodology (sample size, manual re-screen
   procedure), findings summary, and S3 URI of the signed attestation
   PDF. Next due date must be ≤ 90 days after `performed_on`. *Why it
   matters:* `ofac_attestation` tile reads the latest row; an exam
   package with a stale (> 90d old) attestation will trigger a finding.

8. **Maintain `bank_policies`** — for each internal policy document
   (BSA/AML policy, OFAC policy, fair-lending policy, etc.), record the
   active version, effective date, and which regulations it covers
   (`policy_regulation_mappings`). *Why it matters:* the policy-gap
   panel highlights any rule with no covering policy — that gap is the
   first thing an examiner will ask about.

**Pre-exam (≤ 30 days before exam letter delivery):**

9. **Sign off on the examination package** — review the generated
   `examiner_package` PDF, confirm every section, then execute the
   sign-off (writes to `changeset_signoff`). The signature is what makes
   the WORM-stored PDF defensible. Without sign-off, the artifact is
   draft, not exam-ready.

### 3.2 CRO — Chief Risk Officer

The CRO does not own day-to-day population of the bank-side tables, but
provides the risk-rating overlay that turns raw compliance data into an
examiner-facing risk narrative.

1. **Validate severity assignments** — for each finding the CCO enters,
   the CRO confirms the severity (high / medium / low) is consistent
   with the bank's enterprise risk framework. The CCO records but the
   CRO accepts.

2. **Approve the disposition policy** — for `change_record_dispositions`,
   the CRO sets the threshold for when a regulatory change requires
   board notification vs. management acknowledgement only. This is the
   bank's policy, encoded once.

3. **Review enforcement-action exposure** — `board_report` projects the
   25 most recent peer-bank enforcement actions. The CRO reviews these
   quarterly and confirms which (if any) represent specific risk to the
   bank's product mix, so the exam package can show those as "actively
   monitored" rather than just listed.

4. **Co-sign the examination package** — the CRO signs alongside the
   CCO on the final attestation. Most regulators expect a dual signature
   for high-severity findings.

### 3.3 CIO — Chief Information Officer

The CIO owns the technology surface that produces the evidence regulators
look at — automated control execution, evidence pipelines, audit log
retention, access controls.

1. **Wire control tests into CI/CD** — for each control in the
   `controls` inventory that can be tested automatically (wire-amount
   thresholds, dual-approval enforcement, OFAC pre-clear gates), build
   a `control_tests` row with `framework='pytest'`, threshold value,
   threshold rationale (with `threshold_source_rule_id` pointing to the
   compliance rule the threshold derives from), JIRA story key, GitHub
   PR URL, commit hash, and code path. *Why it matters:* an examiner
   asking "show me you actually enforce the $10K wire-dual-approval"
   needs to see the test, the code, and the execution history — all
   joined by `control_id`.

2. **Run control tests on cadence** — every test execution writes a
   `test_executions` row with result, executor, evidence S3 URI, and
   run log S3 URI. Cadence is set per control by the CCO (typically
   daily or weekly). *Why it matters:* `examiner_package` joins this
   table to show "tested in last 30 days: yes / no" per control.

3. **Confirm evidence S3 buckets are WORM-locked** — `evidence_s3_uri`
   and `attestation_s3_uri` columns point at S3 objects that must be
   in an Object-Lock-enabled bucket with retention configured. The
   bucket per bank is provisioned by infra (see
   `infra/cloudformation/pipeline2-exports-stack.yml`). The CIO must
   verify the lock is in compliance mode (not governance mode) before
   any signed artifact is written. *Why it matters:* without Object
   Lock, an examiner cannot rely on the audit trail being immutable.

4. **Enforce SSO + MFA on dashboard access** — every write to a
   bank-side table records `dispositioned_by` / `reviewer_name` / etc.
   That identity is only trustworthy if the dashboard sits behind SSO
   with MFA and session timeouts ≤ 30 minutes. The CIO owns the IdP
   integration and the WAF allowlist (see memory
   `project_p2_waf_blocklist`).

5. **Retain audit logs ≥ 7 years** — `finding_history`,
   `change_record_comments`, and `changeset_signoff` are the audit
   trail. Verify Aurora backup retention and the S3 lifecycle policy
   on the exports bucket both meet the 7-year regulatory minimum.

6. **Disaster recovery drill, quarterly** — confirm the bank can
   regenerate the examination package from the WORM-stored artifacts
   even if the live Aurora is lost. This is the "we won't lose the
   audit trail" claim the examiner expects to see evidence for.

---

## 4. Phased rollout — getting from current state to exam-ready

| Phase | Trigger | Who | Output |
|---|---|---|---|
| **Phase 0 — Onboarding** | Bank signs MSA | CCO + CIO | `bank_profile`, `bank_officers`, IdP integration, S3 Object Lock verified |
| **Phase 1 — Control inventory** | Within 30 days of onboarding | CCO | `controls`, `control_rule_mappings`. Target: ≥ 95% rule coverage |
| **Phase 2 — Test automation** | Within 60 days | CIO + CCO | `control_tests` for every automatable control; first execution run |
| **Phase 3 — Backlog clearance** | Within 90 days | CCO | `findings` (all open MRAs from prior cycles), `bank_policies`, initial dispositions on existing change records |
| **Phase 4 — Steady state** | Continuous | CCO + CRO + BSA Officer | Daily disposition of new changes, quarterly OFAC attestation, monthly control test runs |
| **Phase 5 — Pre-exam** | Exam letter received | CCO + CRO | Review generated PDF, fill any gaps, dual sign-off |

---

## 5. Acceptance criteria — when can we generate a real PDF?

The examination PDF is *technically* generatable today (the renderer works,
the data flows). It is *defensible as an exam artifact* when all of the
following are true:

- `bank_profile` is populated with the bank's real legal name, charter,
  regulator, fiscal year end
- `bank_officers` contains at minimum: BSA Officer, CCO, CRO with real
  names + emails
- `controls.count() >= 0.95 * compliance_rules.where(is_active).count()` for
  rules the bank is subject to
- `control_rule_mappings.count(distinct rule_id) / compliance_rules.count() >= 0.95`
- Every `controls.owner_person_id` resolves to a real `bank_officers.person_id`
  (no NULLs)
- All open `findings` from prior exam cycles are entered, with all OCC 5 Cs
  populated (`concern`, `cause`, `consequence`, `corrective_action`,
  `commitment` all non-NULL)
- The latest `ofac_validations` row has `performed_on >= today - 90d`
- ≥ 90% of `regulatory_change_records` with `detected_at >= today - 90d`
  have a `change_record_dispositions` row
- Every `control_tests` row has at least one `test_executions` row in the
  last 30 days
- Dashboard access is gated by SSO + MFA
- Exports S3 bucket has Object Lock in compliance mode with retention
  ≥ 7 years
- Final `changeset_signoff` row exists with signatures from CCO + CRO

The dashboard surfaces every one of these as a checklist on the
"examination readiness" tile. The bank is exam-ready when all checks pass.
The CCO is responsible for driving every check to green; the CIO is
responsible for the technical preconditions (SSO, Object Lock, retention);
the CRO is responsible for the risk-rating sign-off layer.

---

## 6. What Fortress cannot do for you

Three things that no product can substitute for:

1. **The judgment call of "does this rule apply to my bank"** — only the
   CCO can decide whether 12 CFR 1071 (small-business lending) applies
   given the bank's product mix. We can surface the rule; we cannot
   make the applicability decision.

2. **The narrative content of OCC 5 Cs on a finding** — only someone who
   was in the exam room and read the exam report can write the
   `concern` / `cause` / `consequence` accurately. The dashboard
   provides the schema; the CCO provides the words.

3. **The signature** — the WORM-stored PDF is only defensible because a
   named, authenticated officer signed it. The product can produce the
   artifact; only a human can attest to it.

Everything else on the action-item list above is mechanical population of
the bank-side tables, and once done, the dashboard renders a real PDF
backed by real data on demand.

---

## 7. Pilot / demo seed data — what's synthetic and how to replace it

The `testbank` reference tenant (and any pilot tenant onboarded via
`scripts/seed_testbank_dashboard.py`) ships with placeholder bank-side
rows so the dashboard tiles render at first login. **Every one of these
rows must be replaced or supplemented before the tenant is used for any
real exam cycle.** They are demonstration scaffolding, not exam-ready
content.

This section is the audit trail of *what was seeded synthetically* and
*what the real process must produce in its place*. The seed script
labels every row it writes so the synthetic rows are filterable from
real bank-authored rows.

### 7.1 Marker fields — how to find the seeded rows

| Table | Marker that identifies a seeded row |
|---|---|
| `bank_profile` | Synthetic-charter `charter_number = 'TBNA-0001'` (real charters are OCC-issued numerics) |
| `bank_officers` | `email LIKE '%@testbank.example'` |
| `controls` | `control_id LIKE 'CTRL-%'` AND `created_at` matches seed timestamp window |
| `control_rule_mappings` | `created_by = 'bulk_seed'` |
| `findings` | `finding_id LIKE 'EA-%'` (every seeded finding is prefixed `EA-` because it was projected from an `enforcement_actions.action_ref`) |
| `change_record_dispositions` | `dispositioned_by LIKE '% (bulk seed)'` |
| `ofac_validations` | `methodology` text contains "Sampled 100 wire transfers" (the seed template) |

A new bank can run the queries below against their own tenant to see
exactly which rows are demo data:

```sql
SELECT 'controls'    AS t, COUNT(*) FROM controls
 WHERE bank_id = :bank_id AND control_id LIKE 'CTRL-%'
UNION ALL
SELECT 'mappings'    AS t, COUNT(*) FROM control_rule_mappings
 WHERE bank_id = :bank_id AND created_by = 'bulk_seed'
UNION ALL
SELECT 'findings'    AS t, COUNT(*) FROM findings
 WHERE bank_id = :bank_id AND finding_id LIKE 'EA-%'
UNION ALL
SELECT 'dispositions' AS t, COUNT(*) FROM change_record_dispositions
 WHERE bank_id = :bank_id AND dispositioned_by LIKE '% (bulk seed)';
```

If any of those counts are non-zero, the tenant has unreplaced demo
data and is **not** exam-ready regardless of what the dashboard tiles
show.

### 7.2 What the seed wrote and what the real process must produce

| Table | What seed inserted | What the real process produces (replaces this with) | Owner |
|---|---|---|---|
| `bank_profile` (1 row) | `Test Bank, N.A.` / OCC / charter `TBNA-0001` / address `1 Test Plaza, Wilmington, DE` / `secondary_regulators=["FDIC"]` / no `total_assets_usd` | Real legal name from articles of incorporation, real OCC/FDIC charter number, real registered HQ address, current total assets with as-of date from latest call report, fiscal year end | CCO + CFO (Phase 0) |
| `bank_officers` (1 row) | A single `BSA Officer` row with email `bsa-officer@testbank.example` and a deterministic UUID derived from the bank id | Full officer roster: BSA Officer, CCO, CRO, Internal Audit head, Audit Committee Chair, Board Chair, CEO — real names, real corporate emails, real start dates. Then revisit every control / finding / disposition / OFAC validation that FK-references the seeded BSA officer and reassign to the real person | CCO + HR (Phase 0) |
| `controls` (one per `compliance_rules` row) | Auto-projected: `control_id = "CTRL-" + sha256(rule_id+regulator+geo)[:16]`, `name = regulation_name OR cfr_citation OR rule_id`, `description = first 2000 chars of rule_text`, `owner_role = bsa_officer` for BSA/AML/sanctions rules else `cco`, `owner_person_id = seeded BSA officer`, `control_type = 'preventive'` for all | A CCO-designed control framework: each control has a real narrative (what the bank actually does, not what the rule says), accurate control_type classification (preventive vs. detective vs. corrective), real named owner per control, manual-vs-automated tag, testing frequency. Many real controls cover *multiple* rules; many real rules need *multiple* controls. The 1:1 projection is structurally wrong for production | CCO (Phase 1, ≤30d) |
| `control_rule_mappings` (one per `compliance_rules` row) | One `(control_id, rule_id, mapping_strength='full')` per rule. Tautological — each control was generated from its rule, so the mapping is asserting "this control fully addresses the rule it was projected from" | CCO walks each rule, decides which of the real controls (1+) addresses it, classifies as `full` / `partial` / `compensating`, records reviewer name + date. Some rules will have multiple covering controls; some will reveal coverage gaps the CCO must close. Acceptance: ≥95% distinct rule coverage (per §5) | CCO (Phase 1, ≤30d) |
| `findings` (up to 50 rows) | Projected from real enforcement_actions where `institution_type='national_bank'` OR `regulator='OCC' AND institution_type='unknown'`. These are **real regulator-issued actions against peer banks, not against testbank**. All rows have `finding_id` prefixed `EA-`, `status='validated_closed'`, and `validation_notes` explicitly stating the origin and that they were not issued against the bank | Delete all `EA-` prefixed rows. Enter actual MRAs / MRIAs / MRBAs / violations / observations from the bank's prior exam reports with all OCC 5 Cs (`concern`, `cause`, `consequence`, `corrective_action`, `commitment`) populated, real owner, real identified date and due date, real evidence S3 URIs as remediation progresses | CCO (Phase 3, ≤90d) |
| `change_record_dispositions` (50 rows) | The 50 most recent `regulatory_change_records` auto-dispositioned `acknowledged` / `status='closed'` / `dispositioned_by='Test Bank BSA Officer (bulk seed)'` | A CCO (or delegate) reviews each new regulatory change individually as it arrives, decides `acknowledged` / `requires_action` / `not_applicable`, signs off with their real name + role, attaches an implementation link for `requires_action` rows. Steady state: every new record in `regulatory_change_records` gets a disposition within the bank's SLA (typically 14 days) | CCO (Phase 3 + Phase 4) |
| `ofac_validations` (1 row) | One synthetic quarterly attestation per calendar quarter, `performed_by_person_id = seeded BSA officer`, scope and methodology are template text, `attestation_s3_uri = NULL` | Real BSA Officer performs the quarterly OFAC list validation: real sample of wires + new accounts re-screened against the live Treasury SDN tool, real findings_summary, real signed attestation PDF uploaded to the WORM-locked exports S3 bucket, `attestation_s3_uri` set, next_due ≤ 90 days out | BSA Officer (Phase 4, every quarter) |

### 7.3 What the seed *did not* write — tables that stayed empty

These tables are not pre-seeded, so onboarding banks see them empty from
day one. This is intentional; faking them would be misleading.

| Table | Why not seeded | Who must populate it |
|---|---|---|
| `control_tests` | Test framework binding, threshold value, threshold source, JIRA story, GitHub PR — all are real-engineering artifacts; can't be faked defensibly | CIO + CCO (Phase 2, ≤60d) |
| `test_executions` | Depends on `control_tests` existing first; each row records a real run with evidence | CIO (Phase 2 onward, on cadence) |
| `bank_policies` + `policy_regulation_mappings` | Bank-specific policy documents (BSA/AML policy, OFAC policy, fair-lending policy) — real PDFs in the bank's policy repository | CCO (Phase 3, ≤90d) |
| `finding_history` | Append-only audit log; populates naturally as CCO updates findings | (automatic on writes) |
| `change_record_comments` | Append-only audit log; populates naturally as CCO dispositions changes | (automatic on writes) |
| `changeset_signoff` | The final attestation row signed by CCO + CRO; must be a real signature | CCO + CRO (Phase 5, pre-exam) |

### 7.4 The clean-cutover script (when the tenant becomes real)

When an onboarding bank moves from "pilot demo with seed data" to "real
tenant about to undergo exam cycle 1," run this cleanup sequence in a
single transaction (suggested file:
`scripts/decommission_demo_seed.py` — not yet written; create when first
tenant transitions):

```sql
BEGIN;
-- 1. Remove projected findings (real MRAs will be inserted by CCO afterward).
DELETE FROM findings
 WHERE bank_id = :bank_id AND finding_id LIKE 'EA-%';

-- 2. Remove tautological control-rule mappings (CCO will re-author).
DELETE FROM control_rule_mappings
 WHERE bank_id = :bank_id AND created_by = 'bulk_seed';

-- 3. Remove bulk-acknowledged dispositions so the CCO sees the real
--    backlog and must disposition each.
DELETE FROM change_record_dispositions
 WHERE bank_id = :bank_id AND dispositioned_by LIKE '% (bulk seed)';

-- 4. Remove synthetic OFAC attestations; BSA Officer will produce real ones.
DELETE FROM ofac_validations
 WHERE bank_id = :bank_id
   AND methodology LIKE '%Sampled 100 wire transfers%';

-- 5. Keep controls + bank_officers in place ONLY if the CCO has reviewed
--    each and confirmed the auto-projection is acceptable. Otherwise:
-- DELETE FROM controls WHERE bank_id = :bank_id AND control_id LIKE 'CTRL-%';
-- DELETE FROM bank_officers WHERE bank_id = :bank_id AND email LIKE '%@testbank.example';
-- DELETE FROM bank_profile WHERE bank_id = :bank_id;  -- only if re-onboarding
COMMIT;
```

### 7.5 Acceptance gate for "demo data removed"

A tenant has fully transitioned off demo data when **all** of the
following are true:

- The §7.1 marker query returns zero across all four counts
- `controls` rows are CCO-authored (not `CTRL-` prefixed) OR explicitly
  retained by the CCO as accurate after review
- `bank_officers` contains zero `@testbank.example` emails
- The §5 acceptance criteria all pass

Until then, the dashboard renders, the tiles populate, and the PDF
generates — but the PDF is a **demo artifact**, not an exam artifact.
The distinction is the entire point of this section.
