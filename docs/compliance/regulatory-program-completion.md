# Regulatory Program Completion — pillars → reports

How the four always-on data pillars (**Compliance rules · Obligations · Changes
· Enforcement**) roll up into the generated reports, what "done 100% correctly"
means for each pillar, and which reports become exam-grade as each pillar lands.

> **The governing rule:** reports are *derived* from the pillars. **A report is
> only as correct as its weakest input pillar** — it is "100% correct" only when
> *every* pillar it draws from is complete and correct.

---

## 1 · What "done 100% correctly" means per pillar

| Pillar | Table(s) | Complete & correct when… |
|---|---|---|
| **Compliance rules** | `compliance_rules` + `bank_profile` | Every applicable federal + state rule is scraped and LLM-extracted (grounded: real `cfr_citation`, `severity`, `jurisdiction_level`) and **scoped to this bank's charter / asset tier** — nothing applicable missing, nothing inapplicable included, no invented citations. |
| **Obligations** | `obligations` (+ embeddings, `control_obligation_mappings`) | Every rule's testable obligation sentences are extracted and mapped to controls/tests, so coverage is computable per *obligation* (the testable unit), not just per rule. |
| **Changes** | `regulatory_change_records` (+ `change_delta`, dispositions) | Change detection runs on every scrape, per-record diffs are captured, and each real mutation is dispositioned by the CCO (and routed to remediation). No missed or false changes. |
| **Enforcement** | `enforcement_actions` + `enforcement_risk_scores` | All regulators ingested via index→detail (unique per-action links, real dates/penalties/citations); citations attributed to corpus rules → per-regulation `enforcement_risk_score` feeds "risk-assessment currency." |

---

## 2 · Reports → pillar dependencies

The six report types are defined in `src/exports/policy.yaml` (with required
officer signers). Each is 100% correct only when the pillars below are complete:

| Report (`policy.yaml`) | Depends on | 100% correct when… | Signers |
|---|---|---|---|
| **Coverage report** (`coverage`) | Compliance rules **+** Obligations | Rules + Obligations complete | CCO |
| **Change log** (`change_log`) | Changes | Changes complete | CCO |
| **MRA tracker** (`mra_tracker`) | Changes + Enforcement + findings | those complete | CCO |
| **OFAC attestation** (`ofac_attestation`) | OFAC SDN screening *(separate pillar — `OfacHealth`)* | OFAC vector store complete | BSA officer + CRO |
| **Examiner evidence package** (`examiner_package`) | **ALL 4 pillars** + findings + sign-offs | the whole program is complete | CCO + Internal Audit |
| **Board / audit-committee report** (`board_report`) | **ALL 4 pillars** (readiness aggregation) | the whole program is complete | CCO |

The live **Readiness scorecard** (`dashboard/src/pages/Readiness.jsx`) is the
dashboard version of the composite — fully trustworthy only when all four
pillars are.

### Which reports are "100% correct if done"
- Finish **Rules + Obligations** → the **Coverage report** is exam-grade.
- Finish **Changes** → the **Change log** is exam-grade.
- Finish **all four** → the **Examiner package** and **Board report** are
  exam-grade (the composite, exam-facing deliverables — the point of the program).

---

## 3 · Completion order (reports unlock progressively)

Because a report inherits its pillars' correctness, complete pillars in
dependency order and reports light up as you go:

1. **Rules + Obligations** (one P1 extraction run + control/test mapping) → **Coverage report**.
2. **Changes** detection live + dispositions → **Change log**.
3. **Enforcement** across all regulators + citation→risk loop → **risk-assessment currency** populates.
4. All four green → **Examiner package + Board report** are 100% correct.

---

## 4 · Current state (as of this writing)

| Pillar | State |
|---|---|
| **Enforcement** | ⚠️ **CFPB only** — index→detail crawl live (`src/services/enforcement_actions.py`) with real links/dates/penalties/citations; FinCEN (JS-rendered), OCC (PDF orders), FDIC, FRB (JS app) still pending data-feed/headless work. Risk-scoring loop needs real multi-regulator data. |
| **Compliance rules** | Built; needs the **full P1 extraction run at scale** to be the complete scoped inventory (local sample is partial). |
| **Obligations** | Built (+ per-obligation embeddings, migration 054); needs full extraction + control/test mapping to make coverage real. |
| **Changes** | Built (`regulatory_change_records`, per-record `change_delta`); needs detection running across scrapes + CCO dispositions. |

---

## 5 · Governance gate (independent of data correctness)

Correct *data* is necessary but not sufficient. Every report also requires
**officer sign-offs** (`export_signoffs`) before the un-watermarked PDF unlocks —
e.g. the examiner package needs **CCO + Internal Audit**, the OFAC attestation
needs **BSA officer + CRO**. Artifacts are WORM-locked. So a report becomes
releasable only when its pillars are complete **and** its required signers have
signed.

---

*Source of truth for report types + signers: `src/exports/policy.yaml`. Pillar
→ endpoint → table mapping: `docs/design/dashboard-data-map.md`. Business-value
framing: `docs/compliance/dashboard-completion-sme-review.md`.*
