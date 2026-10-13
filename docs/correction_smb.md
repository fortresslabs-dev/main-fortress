# Pipeline 1 — Complete Rule Dataset Audit & Corrections

**File:** `docs/engineering/p1_rule_dataset_corrections.md`
**Target:** `src/connectors/regulatory/usa/` — all 225 rows currently in the P1 codebase
**Verified against:** eCFR, FinCEN, FFIEC, state codes — 2026-07-09
**Companion:** `new_rule_addition.md` (rules to add)

> **Every rule in the corpus is listed below.** Each carries a verdict — `OK`, `FIX`, or `VERIFY` — the correction required, and the authoritative source to scrape it from. Nothing is omitted; if a rule isn't flagged, it passed.

---

## 0. Scoreboard

| | Count |
|---|---|
| Total rows | 225 |
| Source rules / Requirements / KPIs / Test scenarios | 41 / 72 / 46 / 66 |
| | |
| **Verdict — source rules** | |
| `OK` (no change) | 12 |
| `FIX` (wrong value, wrong cite, wrong type, fabricated) | 21 |
| `VERIFY` (unsettled / stale / needs primary-source check) | 8 |
| | |
| **Defect classes** | |
| **Fabricated obligations** (rule does not exist in law) | **2** |
| Wrong values (threshold / deadline) | 3 |
| **Wrong citations** (points at the wrong CFR section) | **4** |
| Guidance mislabeled as law | 11 |
| Missing citations — source rules | 20 / 41 |
| Missing citations — requirements | 28 / 72 |
| Missing citations — KPIs & TCs | 112 / 112 |
| **Primary-key collisions** | **1** |
| Orphan requirements (no parent rule) | 1 |
| Wrong `regulator` metadata | 12+ rows |
| Junk `effective_date` (page-publish, not rule-effective) | ~15 |
| Connectors emitting zero rows | 4 |

---

## 1. Root cause — read before patching

> **No connector in `regulatory/usa/` ingests the primary text of any regulation.**

The corpus scrapes Comptroller's Handbook pages, FFIEC exam manuals, FDIC FILs, OCC bulletins. Those are agencies *describing* the law — the right corpus for *"how will I be examined,"* the wrong corpus for *"what is my legal obligation."*

When the pipeline must attach a CFR citation to prose that carries none, it **infers one**. That is where both fabricated obligations came from.

The one connector that works — `ofac_sdn.py` — is the only one that scrapes a **primary artifact** (`SDN_ADVANCED.XML`), and it is the only one producing genuinely examiner-grade evidence. The architecture is already right; it just isn't applied to the rules.

**§11 (schema + hard gate) fixes the class. Everything above it fixes instances. Do both.**

---

## 2. `occ_bsa.py` — OCC BSA/AML

### Source rules

| rule_id | current cite | verdict | correction | source of truth |
|---|---|---|---|---|
| `OCC-BSA-WIRE-001` | 31 CFR 1010.410(f) | **FIX — FABRICATED** | See C-1 | eCFR 31/1010.410 |
| `OCC-BSA-CTR-001` | 31 CFR 1010.311 | **OK** | Cite, $10,000, 15-day deadline all correct | eCFR 31/1010.311 |
| `OCC-BSA-CDD-001` | 31 CFR 1010.230 | **FIX** | 20% → **25%**; delete fabricated amendment | eCFR 31/1010.230(d)(1) |

### Requirements

| req_id | current cite | verdict | correction |
|---|---|---|---|
| `OCC-BSA-WIRE-001-R1` | 31 CFR 1010.410(f) | **FIX** | Fabricated. Delete or reclassify → INTERNAL_CONTROL |
| `OCC-BSA-WIRE-001-R2` | 31 CFR 1020.410(a) | **FIX** | Cite misapplied — 1020.410(a) is recordkeeping, not approvers |
| `OCC-BSA-WIRE-001-R3` | 31 CFR 1010.430 | **VERIFY** | 5-yr retention correct, but wire-specific retention is `1020.410(a)`. Re-cite |
| `OCC-BSA-CTR-001-R1` | 31 CFR 1010.311 | **OK** | — |
| `OCC-BSA-CTR-001-R2` | 31 CFR 1010.311 | **OK** | Structuring *detection*. (The structuring *prohibition* is `31 CFR 1010.314` — add as a new req) |
| `OCC-BSA-CDD-001-R1` | 31 CFR 1010.230 | **FIX** | 20% → 25% |
| `OCC-BSA-CDD-001-R2` | 31 CFR 1010.230 | **OK** | Ongoing monitoring |
| `OCC-BSA-CDD-001-R3` | 31 CFR 1010.230 | **OK** | Risk rating at onboarding + trigger events |

---

### C-1 · `OCC-BSA-WIRE-001` — a federal rule that does not exist

**Corpus asserts:** *"Wire transfers of $10,000 or more require approval from two separate authorized employees."* Cited to `31 CFR 1010.410(f)`.

**Three errors compounded:**

1. **There is no federal dual-approval requirement for wire transfers.** Not in 31 CFR, not in 12 CFR. Dual control over wire initiation/release is a legitimate *internal control* discussed in the Comptroller's Handbook — but it is **not law**.
2. **`31 CFR 1010.410(f)` is the Travel Rule** — it requires the transmittor's FI to *include specified information in the transmittal order*. Threshold **$3,000**, not $10,000. Nothing about approvals.
3. **`31 CFR 1020.410(a)` is the Recordkeeping Rule** — originator's-bank record retention. Also nothing about approvers.

**Why P0, not a data-quality nit** — the obligation propagates end-to-end:

```
corpus row (fabricated)
  → dashboard: untested high-risk rule, drags the CCO's exam score
  → Test Factory: Bedrock writes a pytest citing 31 CFR 1010.410(f)
  → PR lands in the BANK'S OWN repo, enters their control library
  → examiner_package: exported as rule → citation → control → test
  → CCO signs; hash-anchored; non-repudiable
  → POST /validate/wire-transfer: BLOCKS LIVE WIRES on this rule
```

**Fix — split into three records**

```python
# (a) NEW — the real rule, at the real threshold
{
  "rule_id":        "BSA-TRAVEL-001",
  "authority_type": "LAW",
  "citation":       "31 C.F.R. § 1010.410(f)",
  "thresholds":     {"threshold_usd": 3000},          # ← NOT 10000
  "rule_text": ("For transmittals of funds of $3,000 or more, the transmittor's financial "
                "institution must include in the transmittal order: transmittor name and address, "
                "transmittor account number (if ordered from an account), amount, date, identity of "
                "the recipient's financial institution, and as many of the recipient's name, address, "
                "and account number as are received. Intermediary FIs must pass on all information."),
  "source_url":     "https://www.ecfr.gov/current/title-31/section-1010.410",
}

# (b) NEW — recordkeeping, correctly cited
{
  "rule_id":        "BSA-RECKEEP-001",
  "authority_type": "LAW",
  "citation":       "31 C.F.R. § 1020.410(a)",
  "thresholds":     {"threshold_usd": 3000, "retention_years": 5},
  "rule_text": ("For each payment order of $3,000 or more accepted as originator's bank, retain "
                "originator name/address, amount, date, payment instructions, and beneficiary bank "
                "identity. Retrievable by originator name (and account number for established "
                "customers). 5-year retention."),
}

# (c) RECLASSIFY — dual control survives, as what it actually is
{
  "rule_id":          "BSA-DUALCTRL-001",
  "authority_type":   "INTERNAL_CONTROL",     # ← the field that prevents this class of error
  "citation":         None,                   # ← MUST be None when authority_type != LAW
  "authority_source": "OCC Comptroller's Handbook — Wire Transfer; safety-and-soundness",
  "rule_text": ("Segregation of duties and dual control over wire initiation and release. A "
                "supervisory expectation and common internal control. NOT mandated by any federal "
                "regulation — do not cite a CFR section."),
  "bank_configurable_threshold": True,
}
```

**Downstream**

| Artifact | Action |
|---|---|
| `OCC-BSA-KPI-001` / `KPI-002` | Keep — re-parent to `BSA-DUALCTRL-001`, label "Internal Control" |
| `TC-001` / `TC-002` / `TC-003` | Keep as internal-control tests; strip CFR cite from generated docstrings |
| **NEW KPI** | Travel Rule Field Completeness: `(transmittals ≥ $3,000 with all required fields / total ≥ $3,000) × 100` |
| **NEW TC** | `$3,000 transmittal missing recipient address → order rejected` |
| `POST /validate/wire-transfer` | See §12 |

---

### C-2 · `OCC-BSA-CDD-001` — 20% → 25%, delete fabricated amendment

```diff
- "…beneficial owners with 20 percent or more ownership stake in legal entity customers,
-  per FinCEN amendment effective April 11, 2026."
+ "…each individual who directly or indirectly owns 25 percent or more of the equity interests
+  of a legal entity customer (ownership prong — up to 4 individuals), AND one individual with
+  significant responsibility to control, manage, or direct the entity (control prong)."
```

Two errors. Threshold is **25%** (`31 CFR 1010.230(d)(1)`). And **there is no FinCEN amendment effective April 11, 2026** — that date and rule change are fabricated. Delete it; do not soften.

FinCEN *did* act in 2026, on something else. Add as a **separate record**, not a threshold change:

```python
{
  "rule_id":          "BSA-CDD-002",
  "authority_type":   "GUIDANCE",
  "authority_source": "FinCEN Administrative Ruling FIN-2026-R001 (2026-02-13)",
  "citation":         None,
  "rule_text": ("Account-Opening Exceptive Relief. Covered FIs may limit identification/verification "
                "of beneficial owners to: (1) first account opening; (2) when the institution has "
                "knowledge of facts calling prior BO information into question; (3) as needed under "
                "risk-based ongoing CDD — rather than at every new account opening."),
}
```

**Source:** eCFR 31/1010.230; `fincen.gov/resources/statutes-regulations/administrative-rulings`

---

## 3. `fincen_sar.py` — FinCEN SAR

| id | current cite | verdict | correction |
|---|---|---|---|
| `FINCEN-SAR-001` | 31 CFR 1020.320 | **OK** | $5k / $25k / insider thresholds + 30-day deadline correct |
| `FINCEN-SAR-002` | 31 CFR 1020.320 | **FIX** | 90 days → **120 days**; add elective flag |
| `FINCEN-SAR-TRIG-001` | 1020.320(a) | **OK** | Insider abuse, any amount |
| `FINCEN-SAR-TRIG-002` | 1020.320(b)(3) | **OK** | ≥ $5,000, identified suspect |
| `FINCEN-SAR-TRIG-003` | 1020.320(b)(3) | **OK** | ≥ $25,000, unidentified suspect |
| `FINCEN-SAR-DEADLINE-001` | 1020.320(b)(3) | **OK** | 30 days. *Add: 60 days if no suspect identified* |
| `FINCEN-SAR-CONT-001` | 1020.320(b)(3) | **FIX** | 90 → 120 days |
| `FINCEN-SAR-FIELD-001` | 1020.320(b)(2) | **OK** | — |
| `TC-013`…`TC-018`, `TC-024` | — | **FIX (metadata)** | `regulator` = **OCC**, should be **FinCEN** |

### C-3 · Continuing-activity SAR: 90 → 120 days

```diff
  FINCEN-SAR-002
- "…must file a continuing activity SAR within 90 calendar days of the initial SAR filing.
-  Continuing activity SARs must be filed for each 90-day period…"
+ "…the institution reviews the account for a 90-day period following the prior SAR, then has
+  30 additional calendar days to file — a 120-calendar-day deadline from the date of the
+  previously related SAR filing. Per FinCEN's Oct-2025 SAR FAQs this 120-day path is ELECTIVE;
+  an institution may instead file within 30 days of identifying continuing activity. A separate
+  90-day review is NOT required where risk-based monitoring is reasonably designed to identify
+  and report continuing activity."
```

```python
"thresholds": {
    "continuing_sar_deadline_days": 120,     # was 90
    "continuing_sar_review_days":    90,
    "continuing_sar_is_elective":  True,
    "separate_review_required":   False,
}
```

**`TC-024` must be rewritten** — it models a 90-day cycle (*"Day 85 … due in 5 days"*):

```diff
- Initial SAR filed Jan 1 | Day 85 reached without case closure
- | Alert: 'Continuing activity SAR review due in 5 days'
+ Initial SAR filed Jan 1 | Day 110, activity continues, continuing SAR not filed
+ | Alert: 'Continuing activity SAR due in 10 days (120-day deadline)'
```

**`FINCEN-SAR-KPI-003`** ("90-day reviews completed on time") must measure against the **120-day filing deadline**.

**Source:** eCFR 31/1020.320; FIN-2007-G003; FinCEN SAR FAQs (Oct 2025).

---

## 4. `fdic.py` — FDIC

| id | current cite | verdict | correction | source |
|---|---|---|---|---|
| `FDIC-EXAM-001` | **12 CFR 337.6** | **FIX** | 337.6 is **Brokered Deposits**. Exam frequency = **12 CFR 337.12** | eCFR 12/337.12 |
| `FDIC-DEPOSIT-001` | 12 CFR 330.1 | **OK** | $250k SMDIA correct | eCFR 12/330 |
| `FDIC-CAPITAL-001` | 12 CFR 324.10 | **OK** | Basel III minimums correct | eCFR 12/324.10 |
| `FDIC-DEPOSIT-001-R1` | 12 CFR 330.1 | **OK** | — | |
| `FDIC-CAPITAL-001-R1` | 12 CFR 324.10 | **OK** | CET1 4.5% | |
| `FDIC-CAPITAL-001-R2` | **12 CFR 324.12** | **FIX** | ⚠️ **324.12 is the CBLR**, not total capital. Minimum total risk-based capital 8% = **324.10(a)(3)** | eCFR 12/324.10 |
| `FDIC-SIGN-001-R1` | 12 CFR 328.3 | **FIX (orphan)** | Cite correct, but **parent rule `FDIC-SIGN-001` does not exist**. Create it, or re-parent to `FDIC-DEPOSIT-001` | eCFR 12/328 |

### C-4 · `FDIC-EXAM-001` — cited to the brokered-deposit section

```diff
- "citation": "12 C.F.R. § 337.6"      # ← Brokered Deposits
+ "citation": "12 C.F.R. § 337.12"     # ← Frequency of Examination
```

Substance is right; only the cite is wrong. **Also add the two conditions the corpus omits:** the 18-month cycle requires a **management** rating of 1–2 in addition to composite 1–2, and **no formal enforcement action** pending.

*(Separately: `12 CFR 337.6` is a real rule you don't cover at all — see `new_rule_addition.md` § BROKERED-001.)*

### C-5 · `FDIC-CAPITAL-001-R2` — cited to the CBLR

**New defect.** `12 CFR 324.12` is the **Community Bank Leverage Ratio framework**, not the total-capital minimum.

```diff
- "citation": "12 C.F.R. § 324.12"
+ "citation": "12 C.F.R. § 324.10(a)(3)"
```

Matters twice: the cite is wrong, *and* the corpus contains a pointer to CBLR without implementing CBLR — the single most relevant capital rule for a sub-$10B bank. See `new_rule_addition.md` § CAP-CBLR-001.

---

## 5. `cfpb.py` — CFPB

| id | current cite | verdict | correction | source |
|---|---|---|---|---|
| `CFPB-1071-001` | *(none)* | **FIX + VERIFY** | Add `12 CFR 1002 Subpart B`. No compliance-date tiering; dates moved repeatedly through litigation → `verify_before_display = True` | consumerfinance.gov 1071 final rule |
| `CFPB-HMDA-001` | *(none)* | **FIX** | Add `12 CFR 1003`. Missing reporting thresholds **and** EGRRCPA partial exemption | eCFR 12/1003 |
| `CFPB-REGB-001` | *(none)* | **FIX** | Add `12 CFR 1002` | eCFR 12/1002 |
| `CFPB-REGE-001` | *(none)* | **FIX** | Add `12 CFR 1005`. Missing **Subpart B** (Remittance Transfer Rule) | eCFR 12/1005 |
| `CFPB-FAIRLEND-001` | 12 CFR 1002; 42 USC 3605 | **OK** | Cite acceptable. Note: the 2:1 disparity trigger is **supervisory practice**, not codified → type GUIDANCE | |
| `CFPB-HMDA-PEER-001` | 12 CFR 1003; Exam Procedures | **VERIFY** | Peer-comparison methodology is exam practice, not a rule → type GUIDANCE | |
| **All 9 CFPB requirements** | *(none)* | **FIX** | Backfill — mapping below | eCFR |

**Requirement citation backfill**

| req_id | correct citation |
|---|---|
| `CFPB-1071-DEMO-001` | 12 CFR 1002.107(a)(18)–(19) |
| `CFPB-1071-BIZ-001` | 12 CFR 1002.107(a) |
| `CFPB-1071-OWN-001` | 12 CFR 1002.107(a)(18) |
| `CFPB-HMDA-LAR-001` | 12 CFR 1003.5(a) |
| `CFPB-REGB-ADV-001` | 12 CFR 1002.9(a)(1) |
| `CFPB-REGE-ERR-001` | 12 CFR 1005.11(c) |
| `CFPB-REGE-LIAB-001` | 12 CFR 1005.6(b) |
| `CFPB-REGE-DISC-001` | 12 CFR 1005.7 |
| `CFPB-REGE-PREAUTH-001` | 12 CFR 1005.10(c) |

**Missing tier logic — add to `CFPB-HMDA-001`:**

```python
"thresholds": {
    "reporting_threshold_closed_end": 25,     # 100 was VACATED — NCRC v. CFPB (2022)
    "reporting_threshold_open_end":  200,
    "partial_exemption": {                     # EGRRCPA — reduced field set
        "closed_end_lt": 500,
        "open_end_lt":   500,
        "in_each_of_prior_years": 2,
    },
}
```

---

## 6. `ofac_sdn.py` — OFAC

| id | current cite | verdict | correction | source |
|---|---|---|---|---|
| `OFAC-SDN-001` | *(none)* | **FIX** | Add `31 CFR Chapter V`. **Scope gap:** ingests `SDN_ADVANCED.XML` only — not the Consolidated (non-SDN) lists | eCFR 31 Ch. V |
| `OFAC-SCREEN-001` | *(none)* | **FIX** | Add `31 CFR Ch. V` | |
| `OFAC-SCREEN-002` | *(none)* | **FIX** | Add `31 CFR Ch. V` | |
| `OFAC-50PERCENT-001` | *(none)* | **FIX** | → `authority_type = GUIDANCE`. The 50% Rule is **OFAC guidance, not a CFR section** | ofac.treasury.gov FAQ 1521 |
| `OFAC-SDN-DAILY-001` | *(none)* | **FIX — FABRICATED** | See C-6 | — |
| `OFAC-KPI-001` | — | **FIX** | Rename "OFAC Screening Coverage" → **"SDN Screening Coverage"** | |
| `TC-007`…`TC-011`, `TC-ALIAS-001` | — | **FIX (metadata)** | `regulator` = **OCC**, should be **OFAC** | |

### C-6 · `OFAC-SDN-DAILY-001` — the second fabricated obligation

**Corpus asserts:** *"SDN list must be updated within 24 hours of OFAC publication."*

**OFAC imposes no such deadline.** Sanctions are **strict liability and effective immediately on publication** — there is no 24-hour grace period, and equally no regulation mandating a 24-hour refresh SLA. This is a **Fortress internal service-level target** written into the corpus as a regulatory requirement.

```python
{
  "rule_id":          "OFAC-SDN-DAILY-001",
- "authority_type":   "LAW",
+ "authority_type":   "INTERNAL_CONTROL",
+ "citation":         None,
+ "authority_source": "Fortress platform SLA — NOT an OFAC requirement",
+ "rule_text": ("Fortress ingests the SDN list within 24 hours of OFAC publication. NOTE: OFAC "
+               "imposes no refresh deadline; sanctions are effective immediately on publication and "
+               "liability is strict. This 24-hour figure is a Fortress SLA, not a legal obligation, "
+               "and must not be presented to an examiner as one."),
}
```

`TC-011` ("SDN list stale — exceeds 24h threshold") and `OFAC-KPI-003` ("SDN List Freshness") stay — as **platform-health** metrics, not compliance metrics.

**Scope fix:** add `CONS_ADVANCED.XML`. Until then, API and marketing must say **"SDN screening"**, never "OFAC screening."

---

## 7. `ffiec_bsa.py` — FFIEC BSA/AML

| id | current cite | verdict | correction |
|---|---|---|---|
| `FFIEC-BSA-PROG-001` | 31 CFR 1020.210 | **OK** | — |
| `FFIEC-BSA-CIP-001` *(rule)* | 31 CFR 1020.220 | **FIX — PK COLLISION** | See C-7 |
| `FFIEC-BSA-314-001` | 31 CFR 1010.520–540 | **OK** | — |
| `FFIEC-BSA-PILLAR-001` | 31 CFR 1020.210 | **FIX** | Says **four** pillars — a fifth (risk-based CDD/EDD) was added by the CDD Rule |
| `FFIEC-BSA-CIP-001` *(requirement)* | 31 CFR 1020.220(a)(2) | **FIX — PK COLLISION** | See C-7 |
| `FFIEC-BSA-EDD-001` | 31 CFR 1010.610 | **FIX** | `.610` = foreign correspondent only. Private banking / PEPs = **`.620`** |
| `FFIEC-BSA-314A-001` | 31 CFR 1010.520 | **OK** | 14-day search window correct |
| `FFIEC-BSA-SAR-SAFE-001` | 31 USC 5318(g)(3) | **OK** | → type as `STATUTE` (USC, not CFR) |

### C-7 · Primary-key collision — `FFIEC-BSA-CIP-001` used twice

**New defect.** The id `FFIEC-BSA-CIP-001` appears as **both** a source rule (`category=BSA/AML`) **and** a requirement (`category=requirements`). Any join keyed on `rule_id` silently picks one.

```diff
  rule:        FFIEC-BSA-CIP-001        # keep
- requirement: FFIEC-BSA-CIP-001
+ requirement: FFIEC-BSA-CIP-REQ-001    # rename
```

**Add a uniqueness constraint at ingest** — see §11.

### C-8 · Five pillars, not four

```diff
- "…four pillars: (1) Internal controls, (2) Independent testing,
-  (3) A designated BSA Officer, (4) Training…"
+ "…five pillars: (1) internal controls, (2) independent testing, (3) a designated BSA Officer,
+  (4) training, and (5) risk-based customer due diligence (added by the CDD Rule)."
+ "citation": "31 C.F.R. § 1020.210; 31 C.F.R. § 1010.230"
```

### C-9 · EDD citation incomplete

```diff
- "citation": "31 C.F.R. § 1010.610"
+ "citation": "31 C.F.R. § 1010.610; 31 C.F.R. § 1010.620"
```

`.610` = foreign correspondent accounts. `.620` = private banking / senior foreign political figures. The requirement text names both.

---

## 8. Guidance mislabeled as law — `ffiec_cybersecurity`, `irr`, `cre_concentration`

**All 11 rows in these three connectors carry no citation — correctly, because none of them are law.** The defect is that they are *presented as rules*. Set `authority_type = GUIDANCE`, `citation = None`, populate `authority_source`.

### `ffiec_cybersecurity.py`

| id | verdict | correction |
|---|---|---|
| `FFIEC-CAT-001` | **FIX + VERIFY** | → GUIDANCE. ⚠️ **FFIEC sunset the CAT in Aug-2024**, pointing institutions to NIST CSF / CRI Profile. Corpus presents it as current → `verify_before_display = True` |
| `FFIEC-ITSEC-001` | **FIX** | → GUIDANCE (FFIEC IT Handbook — Information Security) |
| `FFIEC-BCM-001` | **FIX** | → GUIDANCE (FFIEC IT Handbook — BCM) |
| `FFIEC-CAT-IRP-001` | **FIX** | → GUIDANCE |
| `FFIEC-CAT-MATURITY-001` | **FIX** | → GUIDANCE |
| `FFIEC-ITSEC-PATCH-001` | **FIX** | → GUIDANCE. 30/90-day patch SLAs are *examples*, not codified deadlines |
| `FFIEC-BCM-RTO-001` | **FIX** | → GUIDANCE. "RTO ≤ 4 hours" is *illustrative*, not a rule |
| `FFIEC-ITSEC-MFA-001` | **FIX** | → GUIDANCE |
| `TC-CYBER-001`…`004` | **FIX (metadata)** | `regulator` = **OCC**, should be **FFIEC** |

> 🔴 **The enforceable law behind all of this is missing entirely:** the Interagency Guidelines Establishing Information Security Standards (GLBA §501(b)) — `12 CFR 30 App B` (OCC) / `364 App B` (FDIC) / `208 App D-2` (FRB). You have the study guide but not the statute. See `new_rule_addition.md` § GLBA-SEC-001.
>
> ⚠️ **16 CFR 314 (FTC Safeguards Rule) does NOT apply to banks** — FTC jurisdiction covers non-banks only.

### `irr.py`

| id | verdict | correction |
|---|---|---|
| `IRR-OCC-001` | **FIX** | → GUIDANCE (OCC Bulletin 2010-1). Link to `12 CFR 30 App A` as the enforceable hook |
| `IRR-FFIEC-001` | **FIX** | → GUIDANCE (2010 FFIEC Advisory) |
| `IRR-REQ-POLICY-001` … `IRR-REQ-MODEL-001` (5) | **FIX** | → GUIDANCE. The ±100–400bps shocks and the "-20% EVE" figure are supervisory *reference* scenarios, not codified limits |

### `cre_concentration.py`

| id | verdict | correction |
|---|---|---|
| `CRE-JOINT-001` | **FIX** | → GUIDANCE (2006 Interagency CRE Guidance — FIL-104-2006 / OCC 2006-46) |
| `CRE-REQ-CONSTR-001` / `-TOTAL-001` / `-GROWTH-001` / `-STRESS-001` / `-POLICY-001` | **FIX** | → GUIDANCE **and reword** — see C-10 |

### C-10 · CRE thresholds are screening criteria, not limits

The 100% / 300% figures are **supervisory screening criteria**. Exceeding them triggers heightened scrutiny; **it is not a violation.** Current text ("supervisory concern… must demonstrate") reads as prescriptive law.

```diff
- "Construction and land development loans exceeding 100% of total risk-based capital represent
-  a supervisory concern. Institutions exceeding this threshold must demonstrate enhanced risk
-  management practices."
+ "Construction and land development loans exceeding 100% of total risk-based capital meet the
+  supervisory SCREENING criterion for CRE concentration and warrant heightened risk-management
+  practices. This is a screening threshold, not a regulatory limit; exceeding it is not a violation."
```

---

## 9. Remaining connectors

### `ffiec_call_report.py`

| id | current cite | verdict | correction |
|---|---|---|---|
| `FFIEC-CALL-031/041/051` | 12 CFR Part 304 | **OK** | Form-selection thresholds correct |
| `FFIEC-CALL-DEADLINE-001` | 12 CFR 304.3 | **VERIFY** | The 30-day filing deadline derives from **12 USC 1817(a)(3)** + FFIEC instructions. Confirm 304.3 actually states it — likely mis-cited |
| `FFIEC-CALL-XBRL-001` | 12 CFR 304.4 | **VERIFY** | CDR/XBRL submission is an FFIEC instruction, not a CFR mandate. Confirm |
| `FFIEC-CALL-RC-001` / `-RCN-001` / `-FORM-SELECT-001` | FFIEC instructions | **OK** | → type as `GUIDANCE` (form instructions, not CFR) |
| `TC-CALL-001`…`004` | — | **FIX (metadata)** | `regulator` = **OCC**, should be **FFIEC** |

> ⚠️ **Part 304 collision:** `12 CFR 304 Subpart C` is now the **Computer-Security Incident Notification** rule (36-hour). Citing "12 CFR Part 304" bare for Call Reports is ambiguous. Use subpart-level cites.

### `cra.py`

| id | verdict | correction |
|---|---|---|
| `CRA-OCC-001` / `-FDIC-001` / `-FRB-001` | **VERIFY** | Cites (Part 25 / 345 / 228) OK. **Tiers are stale:** `<$600M` small / `≥$1.4B` large with **no intermediate tier**. 2023 rule restructured; litigated → `verify_before_display = True` |
| `CRA-REQ-ASSESS-001` (25.41) | **VERIFY** | Section numbering changed under the 2023 rule |
| `CRA-REQ-EXAM-SMALL-001` (25.26) | **VERIFY** | Same |
| `CRA-REQ-EXAM-LARGE-001` (25.21–25.28) | **VERIFY** | Same. **Missing the intermediate-bank tier entirely** |
| `CRA-REQ-PUBLIC-001` (25.43) | **VERIFY** | Same |
| `CRA-REQ-RATING-001` (25.28) | **OK** | Four ratings correct |
| All CRA rows | **FIX (metadata)** | `regulator` = **"CRA"** — not a regulator. Should be OCC / FDIC / FRB |

### `nydfs.py`

| id | current cite | verdict | correction |
|---|---|---|---|
| `NYDFS-500-001` | 23 NYCRR Part 500 | **OK** | — |
| `NYDFS-504-001` | **3 NYCRR Part 116** | **VERIFY — likely wrong** | The rule is named **Part 504** and the id says `504`, but the cite says **Part 116**. **Self-inconsistent.** NYDFS Transaction Monitoring is **3 NYCRR Part 504**. Verify and correct |
| `NYDFS-500-CISO-001` (500.4) | **OK** | — |
| `NYDFS-500-BREACH-001` (500.17) | **OK** | 72-hour notice correct |
| `NYDFS-500-CERT-001` (500.17(b)) | **OK** | April 15 certification correct |
| `NYDFS-504-CERT-001` (3 NYCRR 116.2) | **VERIFY** | Same Part 116 / Part 504 inconsistency |

> Part 500 was **amended in 2023** with phased compliance dates running into 2025 (MFA everywhere, asset inventory, expanded CISO reporting). The corpus reflects the *original* 2017 rule. Re-scrape.

### `ca_dfpi.py` / `tx_dob.py` / `il_idfpr.py` / `fl_ofr.py`

| id | verdict | correction |
|---|---|---|
| `CA-CFL-001` | **FIX** | No cite → `Cal. Fin. Code § 22000 et seq.` |
| `CA-CCPA-001` | **FIX** | No cite → `Cal. Civ. Code § 1798.100 et seq.` **Wrong regulator:** CCPA is enforced by **CPPA / AG**, not DFPI. Also **GLBA-covered data is largely exempt** — scope far narrower than the corpus implies → `verify_before_display = True` |
| `CA-DFAL-001` | **VERIFY** | No cite. `Cal. Fin. Code § 3200` on the requirement — confirm against the DFAL division |
| `TX-BANK-001` / `TX-MSA-001` | **FIX** | No cite → `TX Finance Code Ch. 31` / `Ch. 151` |
| `IL-BANK-001` / `IL-TOMA-001` | **FIX** | No cite → `205 ILCS 5` / `205 ILCS 657` |
| `FL-BANK-001` / `FL-MSA-001` | **FIX** | No cite → `Fla. Stat. Ch. 655` / `Ch. 560` |
| **All state source rules** | **FIX** | `effective_date` values are **page-publication dates, not rule-effective dates** — they pollute the pending computation. Null them until real dates are sourced |

---

## 10. Cross-cutting defects

### C-11 · KPIs and test scenarios carry no citation — 112 rows

Not acceptable for `examiner_package`. When an examiner asks *"what authority requires this control test,"* a TC with no chain back to a rule is unevidenced.

```python
# add to KPI and test-scenario schema
"parent_requirement_id": str,     # FK — REQUIRED
"citation":              str,     # INHERITED from parent, never re-derived
```

### C-12 · `regulator` metadata is wrong on 12+ rows

`TC-007`…`TC-018`, `TC-024`, `TC-CALL-*`, `TC-CYBER-*` all carry `regulator = OCC` regardless of the issuing agency (FinCEN, OFAC, FFIEC). Fix by **inheriting from the parent rule**, not restating.

### C-13 · `effective_date` is junk on ~15 rows

Many effective dates are **page-publication or form-version dates**, not rule-effective dates (`FFIEC-CALL-031 eff=2024-06-30` is the *form* version; `CFPB-REGE-001 eff=2020-06-01` is arbitrary). These directly corrupt the "published but not yet effective" computation.

**Fix:** null every `effective_date` that did not come from a Federal Register `effective_on` field; repopulate from the FR connector.

### C-14 · Dead connectors

`cecl.py`, `liquidity_risk.py`, `operational_risk.py`, `peer_benchmarks.py` are in the roster and in the "17 federal" coverage claim but produced **zero rows**. Debug or remove from the claim — a bank's procurement team can falsify it in one query.

---

## 11. SCHEMA — the structural fix

Everything above fixes *instances*. This fixes the *class*.

### 11.1 Obligation table

```sql
CREATE TABLE compliance_rules (
  -- identity
  rule_id                TEXT PRIMARY KEY,          -- UNIQUE across ALL categories (see C-7)
  parent_rule_id         TEXT REFERENCES compliance_rules(rule_id),
  category               TEXT NOT NULL,             -- rule | requirement | kpi | test_scenario
  connector              TEXT NOT NULL,
  regulator              TEXT NOT NULL,             -- INHERITED from parent (see C-12)

  -- ▼ THE FIELD THAT PREVENTS THE FABRICATION CLASS ▼
  authority_type         TEXT NOT NULL
      CHECK (authority_type IN ('LAW','STATUTE','GUIDANCE','INTERNAL_CONTROL')),

  citation               TEXT,                      -- CFR / USC / NYCRR / state cite
  authority_source       TEXT,                      -- for GUIDANCE: the manual or bulletin
  citation_verified      BOOLEAN NOT NULL DEFAULT FALSE,
  citation_verified_at   TIMESTAMPTZ,

  -- provenance: generalize the OFAC pattern to EVERY rule
  source_url             TEXT NOT NULL,
  source_content_hash    TEXT NOT NULL,             -- sha256 of the fetched primary artifact
  scraped_at             TIMESTAMPTZ NOT NULL,

  -- rulemaking lifecycle (from Federal Register; NULL until that connector lands)
  publication_date       DATE,
  effective_date         DATE,                      -- ONLY from FR `effective_on` (see C-13)
  comment_deadline       DATE,
  federal_register_doc   TEXT,
  rin                    TEXT,

  -- applicability: the community-bank join
  applicability_asset_min   NUMERIC,
  applicability_asset_max   NUMERIC,
  applicability_charter     TEXT[],                 -- national|state_member|state_nonmember|savings
  applicability_activities  TEXT[],                 -- mortgage|small_business|remittance|debit_issuing|...
  applicability_states      TEXT[],

  -- flags
  is_exemption                 BOOLEAN DEFAULT FALSE,   -- CBLR, small-creditor QM, HMDA partial...
  verify_before_display        BOOLEAN DEFAULT FALSE,   -- WATCH items
  bank_configurable_threshold  BOOLEAN DEFAULT FALSE,

  rule_text              TEXT NOT NULL,
  thresholds             JSONB                      -- {"threshold_usd":3000,"deadline_days":120,...}
);

-- ── INVARIANTS: reject the defect class at ingest ─────────────────────────
ALTER TABLE compliance_rules ADD CONSTRAINT law_requires_citation
  CHECK (authority_type <> 'LAW' OR citation IS NOT NULL);

ALTER TABLE compliance_rules ADD CONSTRAINT guidance_forbids_citation
  CHECK (authority_type NOT IN ('GUIDANCE','INTERNAL_CONTROL') OR citation IS NULL);

ALTER TABLE compliance_rules ADD CONSTRAINT verified_implies_law
  CHECK (NOT citation_verified OR authority_type = 'LAW');

ALTER TABLE compliance_rules ADD CONSTRAINT children_have_parents
  CHECK (category = 'rule' OR parent_rule_id IS NOT NULL);
```

### 11.2 Per-tenant disposition (so pending items clear)

```sql
CREATE TABLE tenant_rule_state (
  bank_id            TEXT NOT NULL,
  rule_id            TEXT NOT NULL REFERENCES compliance_rules(rule_id),
  applicable         BOOLEAN NOT NULL,       -- computed from the applicability join
  last_reviewed_at   TIMESTAMPTZ,            -- CCO disposition. WITHOUT THIS, items read
  reviewed_by        TEXT,                   --   as "pending" forever
  disposition        TEXT,                   -- ACCEPTED | NOT_APPLICABLE | REMEDIATING
  disposition_hash   TEXT,                   -- hash-anchored signature
  PRIMARY KEY (bank_id, rule_id)
);
```

### 11.3 ▶ THE HARD GATE ◀

> **No obligation may (a) export to `examiner_package`, (b) generate a test in the Test Factory, or (c) gate a transaction in the Compliance Engine unless `authority_type = 'LAW'` AND `citation_verified = TRUE`.**

```python
def may_assert_as_law(rule) -> bool:
    return rule.authority_type == "LAW" and rule.citation_verified

# Test Factory
if not may_assert_as_law(rule):
    pr_body = f"Control expectation per {rule.authority_source}"   # NOT a CFR cite
    test_docstring_citation = None
else:
    pr_body = f"Required by {rule.citation}"

# Compliance Engine
blocking_checks = [c for c in checks if may_assert_as_law(c.rule)]
advisory_checks = [c for c in checks if not may_assert_as_law(c.rule)]
```

Guidance-derived controls **still render and still generate tests** — the PR body just cites the manual instead of inventing a CFR section.

**This single constraint makes C-1 and C-6 structurally impossible, not merely patched.**

### 11.4 Delete `_build_regulations()`

`ffiec_bsa.py` and `ffiec_cybersecurity.py` short-circuit on `_NO_SCRAPE_HOSTS` (Cloudflare-JS gating) and fall back to **hardcoded CFR text** described in `project_ffiec_cf_gated` as *"authoritative."*

It is not. It is frozen, unvalidated, unversioned — **the least trustworthy content in the system**. Both the SAR-90-day and CDD-20% errors live in it.

Cloudflare gating on `bsaaml.ffiec.gov` is not a reason to hardcode law. The underlying rules (31 CFR 1010, 1020) are in eCFR and fetchable today via a free, keyless JSON API. Replace the block with eCFR fetches.

---

## 12. API changes

### `POST /api/v1/validate/wire-transfer`

```diff
- checks: [dual_approval, ctr_threshold, travel_rule]
+ checks: [ctr_threshold, travel_rule, recordkeeping, sdn_screen]
+ internal_control_checks: [dual_approval]      # advisory, non-blocking

- travel_rule.threshold_usd = 10000
+ travel_rule.threshold_usd = 3000              # ← CRITICAL
```

> 🔴 **At $10,000 the endpoint is not applying the Travel Rule to the $3,000–$10,000 band.** That is a real BSA gap the product is *causing* in a bank's production wire flow. It is the single most damaging line in the codebase.

```diff
  {
    "checks": [
      { "check": "travel_rule",
+       "authority_type": "LAW",
        "cfr_citation": "31 CFR 1010.410(f)",
-       "threshold": 10000,
+       "threshold": 3000,
        "result": "PASS" },
      { "check": "dual_approval",
-       "cfr_citation": "31 CFR 1010.410(f)",       // ← FABRICATED
-       "result": "FAIL", "blocking": true
+       "authority_type": "INTERNAL_CONTROL",
+       "cfr_citation": null,
+       "authority_source": "OCC Comptroller's Handbook — Wire Transfer",
+       "result": "ADVISORY", "blocking": false }
    ]
  }
```

### `POST /api/v1/screen/sdn`

Rename all "OFAC screening" language → **"SDN screening"** until `CONS_ADVANCED.XML` is ingested.

---

## 13. Execution order

| # | Task | Ref | Blocks |
|---|---|---|---|
| 1 | Travel-rule threshold $10K → $3K | §12 | **Live BSA gap in production wires** |
| 2 | Reclassify wire dual-approval; add real Travel Rule + Recordkeeping | C-1 | Demo blocker |
| 3 | SAR 120d; CDD 25% + delete fabricated amendment | C-2, C-3 | Demo blocker |
| 4 | Reclassify OFAC 24h SLA | C-6 | Demo blocker |
| 5 | `authority_type` + invariants + hard gate | §11 | **Prevents recurrence** |
| 6 | Delete `_build_regulations()`; wire in eCFR connector | §11.4 | **Prevents recurrence** |
| 7 | Citation fixes: 337.12, 324.10(a)(3), 1010.620, PK collision | C-4,5,7,9 | Exam-package integrity |
| 8 | Reclassify 11 guidance rows | §8 | Exam-package integrity |
| 9 | Backfill 48 missing citations; inherit to 112 KPIs/TCs | C-11 | Coverage claim |
| 10 | Fix `regulator` metadata; null junk `effective_date` | C-12, C-13 | Pending computation |
| 11 | Debug 4 dead connectors | C-14 | Coverage claim |

**Items 1–4 are a day of work and they are the difference between a demo that lands and a demo where the bank's BSA officer stops the meeting.**
