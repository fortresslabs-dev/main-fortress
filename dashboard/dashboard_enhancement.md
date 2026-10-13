# Fortress Dashboard — Enhancement & Backend Contract

How to evolve the existing React/Vite dashboard into the unified **Readiness + operational + reports** product (`fortress_dashboard_unified.html`). This is the build contract: for every component, the endpoint, the **exact JSON response shape** the frontend needs, the business reason it exists, and what the bank's own architect must be able to verify.

**Stack (unchanged):** Vite + React 18, `react-router-dom`, `aws-amplify` (Cognito groups → role), `axios` to FastAPI `:8002` base `/api/v1`, `recharts`, `lucide-react`. Auth already yields `role ∈ {CCO,CRO,CIO,admin,viewer}`, `canEdit`, `canApprove`.

---

## 0 · Navigation flow (the "correct flow")

```
Readiness (home, executive)  →  Rules · Obligations · Changes · OFAC (operate)
        ↓ see the gap                         ↓ produce evidence
   risk × coverage matrix         Workflows (pipeline that generates it)
        ↓ prove it
   Reports (point-in-time exports → examiner / board / audit)
```

Make **Readiness** the index route (`/` → `/readiness`); demote the old Overview into it. Everything else stays a tab. The flow mirrors how an executive actually works: *why → operate → prove.*

---

## 1 · Business framing — why each persona needs this

| Persona | Core question | Business stake | Primary surface |
|---------|---------------|----------------|-----------------|
| **CCO** | "Am I covered, and can I prove it?" | Avoid MRAs/enforcement; cut manual test-authoring cost; pass the exam | Readiness (CCO lens), Rules, Changes, Reports |
| **CRO** | "Where is risk concentrated vs. appetite?" | Board/risk-committee governance; capital & reputational risk | Readiness (CRO lens), risk × coverage matrix, Enforcement |
| **CIO** | "Is the engine trustworthy, secure, resident?" | Third-party/vendor risk (TPRM); data-residency liability; SOC 2 | Readiness (CIO lens), Workflows, OFAC technical, architecture panel |
| **Bank architect / tech director** | "Can I safely integrate this into our environment?" | Vendor due diligence; data-flow boundary; integration burden | OFAC technical expander, CIO architecture panel, §7 below |

The shared **Readiness Score** is the one number all four can talk to; each lens is a reweighting of the *same* data, never a separate dataset.

---

## 2 · Existing endpoints — fixes to fill the corrected tabs

These are data-side corrections; the frontend already renders the fields. (Detail and field names in the companion `backend_changes.md`.)

- **`/dashboard/enforcement`** — fix `penalty_amount_usd` parser (the $480B bug), sanity-bound > $10B to review, add `institution_name` (today only `institution_type` exists → "unknown").
- **`/dashboard/rules`** — populate `enforcement_risk_score` (0–1; returns `0.00` today because unpopulated). Return `null` not `0` until scored. Add `jurisdiction`, `test_threshold`, `threshold_rationale`.
- **`/dashboard/changes`** — server-side dedup (`dedup_key`, `duplicate_count`), real `content_change_pct`, and an `obligation_impact` field distinct from `change_summary`.
- **`/dashboard/workflows`** — populate `duration_sec`, add `failure_reason`, `failure_stage`, `dedup_of`.
- **`/dashboard/ofac-health`** — add `freshness_sla_hours`, `hours_since_refresh`, `is_stale`, `source_label` (kill hardcoded "P1").
- **`/dashboard/obligations`** — enforce regulator attribution at intake; never emit `unknown`; expose `unattributed_count` separately.

---

## 3 · NEW endpoint — Readiness score

`GET /api/v1/dashboard/readiness?lens={cco|cro|cio}` — fills the gauge, the band, the business-read line, and the 10 dimension bars.

```jsonc
{
  "as_of": "2026-06-02T12:00:00Z",
  "lens": "cco",
  "score": 62,                       // shown in gauge; persona-weighted
  "band": "developing",              // failing|developing|operational|strong
  "biggest_drag": "24 obligations unmapped, 12 changes unsigned",
  "dimensions": [
    {"key":"inventory",      "label":"Obligation inventory",        "score":88},
    {"key":"control_mapping","label":"Control mapping",             "score":71},
    {"key":"testing",        "label":"Testing & rationale",         "score":24},
    {"key":"ra_currency",    "label":"Risk-assessment currency",    "score":66},
    {"key":"change_mgmt",    "label":"Change management",           "score":82},
    {"key":"findings",       "label":"Findings remediation",        "score":45},
    {"key":"governance",     "label":"Governance evidence",         "score":90},
    {"key":"independent",    "label":"Independent testing",         "score":58},
    {"key":"reporting",      "label":"Point-in-time reporting",     "score":40},
    {"key":"engine",         "label":"Engine integrity",            "score":78}
  ]
}
```
**Compute server-side from existing tables.** `lens` only changes the dimension **weights** (CCO → coverage/evidence heavy; CRO → risk/findings heavy; CIO → engine/reporting heavy), not the underlying dimension scores. Band thresholds: `<40 failing, 40–69 developing, 70–84 operational, 85+ strong`.

---

## 4 · NEW endpoint — Risk × coverage matrix

`GET /api/v1/dashboard/risk-matrix` — fills the 3×3 centerpiece and the danger callout.

```jsonc
{
  "as_of": "2026-06-02T12:00:00Z",
  "grid": [
    {"risk":"high","coverage":"untested","count":7,"rule_ids":["OCC-WIRE-SDN-001","..."]},
    {"risk":"high","coverage":"partial", "count":3,"rule_ids":[...]},
    {"risk":"high","coverage":"tested",  "count":4,"rule_ids":[...]},
    {"risk":"med", "coverage":"untested","count":12},
    {"risk":"med", "coverage":"partial", "count":5},
    {"risk":"med", "coverage":"tested",  "count":3},
    {"risk":"low", "coverage":"untested","count":28},
    {"risk":"low", "coverage":"partial", "count":4},
    {"risk":"low", "coverage":"tested",  "count":2}
  ],
  "danger_count": 7,                         // high & untested → examiner's first finding
  "danger_rule_ids": ["OCC-WIRE-SDN-001","FINCEN-CTR-AGG-001","..."]
}
```
- `risk` bucket: `high ≥0.8`, `med 0.6–0.79`, `low <0.6` from `enforcement_risk_score` — **depends on §2 risk-score fix**; ship that first or the grid collapses into one column.
- `coverage`: `tested` = `test_count>0 & passing`; `partial` = generating/awaiting-approval; `untested` = no test & not N/A.
- Clicking a cell should deep-link to `/rules` filtered by `rule_ids` (drill-down = the "operate" step of the flow).

---

## 5 · NEW endpoints — CRO & CIO lens data

`GET /api/v1/dashboard/cro`
```jsonc
{
  "residual_by_domain": [
    {"domain":"sanctions","residual":78,"appetite":60},   // residual > appetite = breach
    {"domain":"bsa_aml","residual":64,"appetite":70},
    {"domain":"consumer","residual":52,"appetite":65},
    {"domain":"privacy","residual":38,"appetite":60},
    {"domain":"licensing","residual":31,"appetite":55}
  ],
  "kris": [
    {"name":"High-risk obligations untested","value":7,"trend":"up","bad":true},
    {"name":"Avg days to remediate","value":71,"trend":"down"},
    {"name":"Overdue MRAs","value":1,"bad":true},
    {"name":"Coverage velocity","value":"+14%","trend":"up","good":true},
    {"name":"Risk-assessment age (months)","value":13,"warn":true}
  ],
  "peer_signal": [                                          // derived from enforcement table
    {"regulator":"CFPB","focus":"Credit-reporting accuracy","level":"elevated"},
    {"regulator":"OFAC","focus":"Screening completeness","level":"elevated"}
  ]
}
```

`GET /api/v1/dashboard/cio`
```jsonc
{
  "engine": {
    "pipeline_success_rate_7d": 0.72, "trend":"up",
    "avg_duration_sec": 221, "open_failures": 3,
    "index_integrity": {"embedded":44008,"total":44008,"ok":true},
    "uptime_30d": 0.9994
  },
  "architecture": {
    "deployment_model": "in_vpc_customer",
    "customer_data_leaves_vpc": false,
    "cross_boundary_flow": "compliance_rules_only",
    "connectors": [
      {"name":"JIRA","ok":true},{"name":"GitHub","ok":true},{"name":"Core banking","ok":false}
    ],
    "rbac_enforced": true,
    "audit_trail_immutable": true
  },
  "certifications": [ {"name":"SOC 2 Type II","status":"in_progress"} ]
}
```
Most of this is **aggregation over existing `workflows` + `ofac-health` data** — little new collection.

---

## 6 · NEW endpoints — Reports / exports (the deliverable)

Exams consume frozen, dated, tamper-evident files. This is the genuinely new capability.

`POST /api/v1/dashboard/exports`
```jsonc
// request
{ "type":"examiner_package", "as_of":"2026-06-02", "format":"pdf" }   // type ∈ examiner_package|ofac_attestation|board_report|mra_tracker|change_log|coverage_gap
// response
{ "export_id":"exp_8f3a", "version_hash":"A1B2C3D",
  "as_of":"2026-06-02T00:00:00Z", "status":"ready",
  "download_url":"/api/v1/dashboard/exports/exp_8f3a/download" }
```
`GET /api/v1/dashboard/exports/{id}/download` → the file (PDF / CSV / XLSX).

**Hard requirements (what makes it examiner-acceptable):**
1. **Point-in-time** — snapshot data *as of* the date (historical query, not `now()`); same date ⇒ same artifact.
2. **Tamper-evident** — `version_hash = sha256(serialized_payload)`, stamped on every page/row; persist `{export_id,type,as_of,version_hash,generated_by,generated_at}` in an `exports` audit table.
3. **Self-contained** — readable a year later with no live link.
4. **RBAC** — gate behind `canApprove`; log generator identity.

**Build order:** `examiner_package` + `ofac_attestation` first (the two the exam requires), then the rest.

**New supporting store — findings/MRA** (none today): `GET /api/v1/dashboard/findings` → `[{finding_ref, source, opened_at, due_at, owner, status, remediation_evidence}]`. Powers the CCO open-findings panel **and** the `mra_tracker` export — the most-scrutinized exam artifact.

---

## 7 · For the bank's technical director / architect (vendor due diligence)

A bank won't deploy this without its own architect signing off on data flow and security. Surface these so the answer is "here, verify it yourself" rather than a slide. Most map to the **CIO architecture panel** + an exportable architecture attestation.

What they will ask, and where it lives:

- **Where does our customer/transaction data go?** Answer must be: *nowhere outside our VPC.* Expose `customer_data_leaves_vpc:false` and `cross_boundary_flow:"compliance_rules_only"`. The SDN screener and vector index run **in-VPC**; only public OFAC data flows *in*, only non-sensitive verdicts (counts, pass/fail — never screened names) flow *out*.
- **What crosses the boundary, exactly?** Provide a data-flow manifest endpoint (`GET /api/v1/architecture/data-flow`) listing each outbound field and its sensitivity classification — this is what TPRM teams attach to the vendor file.
- **How is access controlled?** Cognito groups → RBAC (`CCO/CRO/CIO/CTO` role-scoped views) already exists; expose `rbac_enforced` and the role→permission map.
- **Is the evidence tamper-proof?** Point them at the `exports` audit table + `version_hash` and the GitHub PR trace (versioned artifacts, not screenshots).
- **What's the integration surface?** Connectors: JIRA (intake), GitHub (PR/evidence), core-banking (screening feed). Expose `connector_health` and a documented auth model per connector.
- **Certifications / SOC 2.** Expose honestly (`in_progress`) — an architect respects a straight answer more than a claim they'll later disprove.

> Selling note: for community/regional banks the **in-VPC, data-never-leaves** posture is a differentiator most incumbents can't match. Make the architect's verification *easy* — a clean data-flow manifest and an architecture attestation export shorten the security review that otherwise stalls the deal.

---

## 8 · Frontend wiring summary (additions to `api.js`)

```js
export const readinessApi = {
  getReadiness: (lens) => api.get('/api/v1/dashboard/readiness', { params:{ lens } }),
  getRiskMatrix:()      => api.get('/api/v1/dashboard/risk-matrix'),
  getCro:       ()      => api.get('/api/v1/dashboard/cro'),
  getCio:       ()      => api.get('/api/v1/dashboard/cio'),
  getFindings:  ()      => api.get('/api/v1/dashboard/findings'),
  getDataFlow:  ()      => api.get('/api/v1/architecture/data-flow'),
}
export const exportApi = {
  create:   (b)  => api.post('/api/v1/dashboard/exports', b),
  download: (id) => api.get(`/api/v1/dashboard/exports/${id}/download`, { responseType:'blob' }),
}
```
- New `src/pages/Readiness.jsx` becomes the index route; lens toggle drives `getReadiness(lens)` + shows the matching CRO/CIO panels.
- New `src/pages/Reports.jsx` for exports; reuse the existing `Modal` pattern from `Rules.jsx`.
- Risk-matrix cells deep-link to `/rules?ids=...`.

---

## 9 · Sequencing

1. **P0** — penalty sanity bound (before any demo).
2. **P1 data** — risk scores (unblocks the matrix), changes dedup + `obligation_impact`, workflow duration/failure-reason, OFAC freshness SLA.
3. **Readiness layer** — `/readiness` + `/risk-matrix`, then `/cro` + `/cio`.
4. **Exports MVP** — `examiner_package` + `ofac_attestation` with point-in-time + hashing; add `findings` store.
5. **P2** — jurisdiction/state regulators + dynamic chips, unified ID grammar, threshold metadata.
6. **Architect surface** — data-flow manifest + architecture attestation export.

> The single dependency to respect: the Readiness score and risk-matrix are only as trustworthy as `enforcement_risk_score`. Land risk scoring before exposing the matrix, or the danger quadrant reads empty and undercuts the executive view that the whole product is built around.
