# Fortress Labs — Go-to-Market Packaging & Sales Playbook

Date: 2026-06-12
Audience: Sales, customer success, executive leadership, board.
Companion docs: `docs/dashboard_layman_guide.md` (what the product does), `docs/p2_dashboard_api_production_integration.md` (how it deploys).

This playbook turns the three engineering pillars — test automation, executive dashboard, real-time API — into something a bank can sign for.

---

## 1. One-page elevator

**Fortress Labs is the only compliance engine that runs end-to-end inside a bank's own VPC and proves coverage three ways:**

1. **A dashboard** the CCO/CRO/CIO open every morning to see exam readiness, risk × coverage, regulatory change inbox, and OFAC index health.
2. **A test-automation factory** that turns any compliance rule into a JIRA story and a GitHub pull request containing a Bedrock-generated pytest in ~5 minutes.
3. **A real-time compliance API** the bank wires into its core, wire, KYC, and loan-origination apps to make every live transaction examiner-defensible — same engine the dashboard runs on.

No customer data leaves the bank's VPC. Public regulatory data is scraped centrally (Pipeline 1) and synced nightly into each bank's instance (Pipeline 2). One annual contract covers all three pillars; transaction volume meters separately.

---

## 2. Three pillars → three products

| Pillar | Product name | Primary buyer | What they get |
|---|---|---|---|
| Test automation | **Compliance Test Factory** | CCO + Head of Compliance Engineering | Dashboard rule list → click → JIRA story → Bedrock-generated pytest in a GitHub PR, citing the CFR and the threshold |
| Executive & examination dashboard | **Examination Readiness Suite** | CCO, CRO, CIO | Per-lens readiness score, 3×3 risk × coverage matrix, regulatory change inbox + sign-off, point-in-time hash-versioned exam exports |
| Real-time transaction API | **Fortress Compliance Engine** | COO / Core Banking / Wire Ops | `POST /api/v1/screen/sdn`, `POST /validate/wire-transfer`, `POST /validate/loan-application` — synchronous, idempotent, audit-graded |

All three pillars share one engine, one rule store, and one audit trail. The dashboard's `screening_id` joins to the bank's transaction id; what the CCO signs off in the dashboard is exactly what the wire gateway will enforce.

---

## 3. SKU bundles

### 3.1 Trial — Examination Lens (30 days)

- Read-only dashboard deployed in the bank's VPC.
- Loaded with Fortress's public regulatory dataset; no customer data uploaded.
- Cognito SSO, role mapping for CCO/CRO/CIO.
- **Inclusions:** Readiness score, risk matrix, regulatory change inbox (no sign-off action), enforcement feed, OFAC health.
- **Exclusions:** JIRA generation, GitHub PR, real-time API, exports.
- **Pricing:** free; one-week white-glove onboarding included.
- **Goal:** make the CCO say *"that's our actual number"* and pass the CISO's first-pass review.

### 3.2 Examination Readiness Edition (annual subscription)

- Full dashboard + JIRA story creation + GitHub PR generation.
- All sign-off endpoints active (CCO, CRO).
- Point-in-time, hash-versioned exam binder exports.
- Unlimited internal users; role-gated.
- All 17 federal + 5 state regulators included.
- **Not included:** real-time `/screen/sdn`, `/validate/*` for live transactions.
- **Pricing:** $150k–$400k per charter per year (see §5).

### 3.3 Production Engine Edition (per-transaction)

- Adds the live transaction APIs to the Readiness Edition.
- HMAC API keys via `scripts/register-bank.sh`; per-bank quota in `client_credentials`.
- 24 h idempotency on `/screen/sdn`; auto-invalidates on SDN refresh.
- p99 SLO: wire/loan ≤ 100 ms, SDN ≤ 250 ms.
- **Pricing:** platform fee + per-screen tiers (see §5).

### 3.4 Holding-Co / Multi-Charter Enterprise

- Multiple IDIs under one master contract.
- Multi-state coverage with custom state-DFI connectors.
- Dedicated solutions architect, named support.
- Optional: sovereign-cloud Bedrock substitute, on-prem deployment, custom regulator add-ons.
- **Pricing:** custom, multi-year.

### 3.5 Add-ons (any tier)

| Add-on | Use case | Pricing model |
|---|---|---|
| Extra state regulator connector | Bank operates outside the 5 standard states | One-time + annual maintenance |
| Premium change-detection SLA | <1 h from regulator publish → dashboard change record | Annual uplift |
| Custom regulator (e.g., overseas) | International bank, parent in EU | Quoted |
| Sovereign-cloud Bedrock substitute | Air-gapped or GovCloud requirement | Annual uplift |
| Dedicated support pod | 24×7 named engineer | Annual uplift |

---

## 4. Customer journey — land, prove, expand

```
┌────────┐   ┌────────┐   ┌────────────┐   ┌──────────┐   ┌──────────┐
│  LAND  │ → │ PROVE  │ → │  EXAMINATION │ → │ EXPAND TO │ → │ ACCOUNT │
│ Trial  │   │ JIRA→PR│   │  EDITION     │   │  ENGINE   │   │ GROWTH  │
└────────┘   └────────┘   └────────────┘   └──────────┘   └──────────┘
  Day 0       Day 14         Day 30–60       Day 120+      Year 2+
  CISO ok     CCO "wow"      First contract   Per-tx        Charters,
                                              meter on      add-ons,
                                              live wires    enterprise
```

### Step 1 — Land (week 1–2)

- Deploy Pipeline 2 in the bank's VPC with `TrialMode=true`.
- Load public regulatory data only — no customer data movement, so InfoSec is unblocked.
- CCO logs in via Cognito, sees their **own** Readiness score with `biggest_drag` called out.

### Step 2 — Prove (week 2–4)

- White-glove session: pick the bank's single highest-risk untested rule.
- CCO clicks **Generate Test**. Within ~5 minutes, the bank's engineering team sees a real PR in their GitHub with a Bedrock-generated pytest.
- That PR is the artifact that converts the pitch into measurable ROI in the bank's own commit history.

### Step 3 — Examination Edition contract (week 4–8)

- Procurement sees: in-VPC, SOC 2 (in progress, see §7), no customer data egress, Cognito-RBAC, immutable audit trail.
- CCO sees: replaces 6–18 months of manual obligation-to-test mapping plus 1–2 audit-prep consulting cycles.
- CIO sees: `engine.index_integrity.ok`, `customer_data_leaves_vpc: false` in the CIO panel.
- Contract: annual subscription per charter, see §5.1.

### Step 4 — Expand to Engine Edition (month 4+)

- After the CCO has the dashboard, the next question internally is *"why isn't our wire gateway using the same engine?"*
- The bank's app teams wire `POST /api/v1/screen/sdn`, `POST /validate/wire-transfer`, `POST /validate/loan-application` into their live transaction paths.
- See `docs/dashboard_layman_guide.md` §11 for the integration menu.
- Cut-over is environment variables and API keys, not architecture — the engine already ran in their VPC for the dashboard.
- Pricing converts to **platform fee + per-screen**, see §5.2.

### Step 5 — Account growth (year 2+)

- Additional charters under the holding company.
- Add-on regulators or jurisdictions.
- Premium SLA on change detection.
- Eventually, the bank standardises Fortress as the compliance plane for every new product launch.

---

## 5. Pricing card

### 5.1 Examination Readiness Edition

| Tier | Bank size | Annual price | Includes |
|---|---|---|---|
| Community | <$1B assets | **$150k** | 1 charter, dashboard + JIRA + PR, unlimited users, all federal + 5 state regulators |
| Mid-cap | $1B–$10B | **$250k** | 1 charter, all above + premium support |
| Regional | $10B–$50B | **$400k** | 1 charter, all above + dedicated CS pod |
| Holding-Co | Multi-charter | Custom | Multi-IDI, multi-state, custom regulators |

Posture: **pre-priced, no haggling**. Keeps the dashboard sale fast.

### 5.2 Production Engine Edition (transaction API)

Platform fee + per-screen tiered pricing:

| Volume tier | Screens / month | Per-screen rate | Indicative monthly bill |
|---|---|---|---|
| Starter | up to 1 M | $0.10 | $5k platform + up to $100k |
| Growth | up to 10 M | $0.05 | $5k platform + up to $500k |
| Scale | up to 100 M | $0.02 | $5k platform + up to $2M |
| Enterprise | >100 M | Custom | Custom |

`/validate/wire-transfer` and `/validate/loan-application` calls count separately at $0.02 each.

Positioning vs. incumbents (ComplyAdvantage, Refinitiv, LexisNexis): comparable per-screen rates, but Fortress is the only one with (a) idempotent `screening_id`, (b) same engine as the bank's exam binder, (c) in-VPC deployment.

### 5.3 Add-ons (annual unless noted)

| Add-on | List price |
|---|---|
| Extra state regulator | $25k one-time + $10k/year |
| Premium change-detection SLA (<1 h) | $50k/year |
| Custom federal/international regulator | $75k–$150k, quoted |
| Sovereign-cloud / on-prem deployment | $100k/year uplift |
| Dedicated 24×7 support pod | $150k/year |

---

## 6. Who buys, who blocks, who champions

| Persona | Role in deal | What they want | What we show them |
|---|---|---|---|
| CCO | **Economic buyer** | Pass exams without doubling the team | Readiness score, JIRA→PR demo, exam binder export |
| CRO | **Strong champion** | Residual risk vs. appetite, peer signal | CRO lens, residual-by-domain panel, recent enforcement feed |
| CIO | **Technical gatekeeper** | "Does customer data leave?" "What's the audit trail?" | CIO lens with `customer_data_leaves_vpc: false`, immutable audit, RBAC |
| CISO / Security | **Blocker if unhappy** | SOC 2, in-VPC, JWT, KMS, WAF | Architecture doc, attestations, security review pack |
| COO / Wire Ops | **Engine Edition buyer** | Reduce per-screen cost, eliminate false positives | Per-screen pricing model, vector-mode recall vs. legacy |
| GC / Legal | Reviewer | Regulatory citation traceability | Every assertion cites a CFR; sign-off table is auditable |
| Head of Compliance Eng | Implementer + champion | Stop hand-writing tests | Bedrock-generated PR in their GitHub |
| Procurement | Process gate | Standardised paper, predictable pricing | Pre-priced tiers, no surprise meters in Edition tier |

---

## 7. Objection handling

| Objection | One-line answer | Backing artifact |
|---|---|---|
| "Customer data must not leave our VPC." | It doesn't — Pipeline 2 runs in your VPC, only public regulatory data crosses. | `cio.architecture.customer_data_leaves_vpc: false`; data-flow manifest at `GET /api/v1/architecture/data-flow` |
| "How do we audit the AI's test code?" | Every generated test cites a CFR; the PR sits in your GitHub for human review before merge. | The PR itself; `_build_description` in `generate_test_api.py` |
| "Our examiners want point-in-time evidence." | The dashboard generates hash-versioned, immutable snapshots. | `POST /api/v1/dashboard/exports` |
| "We already use ComplyAdvantage / Refinitiv." | Keep them if you like — or migrate to lower per-screen + integrated audit binder. | Per-screen pricing card (§5.2), `screening_id` join |
| "What about SOC 2?" | In progress; in the meantime we share our security control matrix and your VPC stays your VPC. | `cio.certifications`; security pack |
| "Bedrock isn't approved in our cloud." | Sovereign-cloud Bedrock substitute is an add-on; route via `settings.BEDROCK_MODEL`. | Memory `feedback_bedrock_inference_profile_prefix` |
| "Who is liable if a screen misses an SDN match?" | Same as your incumbent — you own the decision; we provide the evidence trail to defend it. | Audit row, `screening_id`, vector + fuzzy score breakdown |
| "Can our risk team override thresholds?" | Yes — per-rule overrides via `PATCH /dashboard/rules/{id}/status`. | `rule_overrides` table |
| "Will it work with our JIRA / GitHub?" | Yes — JIRA Cloud (Bearer) and Server/DC (HMAC); GitHub OAuth app. | `webhooks.py:_authenticate`, `JIRA_AUTH_MODE` env |
| "What if a regulator changes its publication format?" | We watch every connector with a per-host scrape monitor; alarms fire on failure. | `mcp_servers/scrape_monitor/`, `scrape_runs` table |

---

## 8. Competitive matrix

| Capability | Fortress | ComplyAdvantage | NICE Actimize | Hummingbird | Greenlite | In-house build |
|---|---|---|---|---|---|---|
| OFAC / SDN screening API | ✓ idempotent | ✓ | ✓ | — | ✓ | DIY |
| Wire / BSA-AML validation API | ✓ | — | ✓ legacy | — | partial | DIY |
| CFPB 1071 loan validation API | ✓ | — | partial | — | — | DIY |
| Auto-generated test code (JIRA→PR) | **✓ unique** | — | — | — | — | — |
| CCO exam-readiness dashboard | ✓ | — | partial | case-mgmt focus | — | — |
| In-VPC deployment | ✓ | SaaS only | on-prem heavy | SaaS | SaaS | yes |
| Same engine for dashboard AND live API | **✓ unique** | n/a | partial | n/a | n/a | varies |
| Point-in-time exam exports | ✓ | — | ✓ | — | — | DIY |
| Per-charter pricing | ✓ | per-screen | enterprise license | per-case | per-screen | sunk cost |

**Three differentiators to repeat in every meeting:**

1. **Same engine for both worlds.** The model that drafts the test the CCO signs off in the dashboard is the same model screening the live wire. No other vendor unifies that.
2. **PR-as-artifact.** Test automation that materialises in the bank's own GitHub is concrete in a way "AI compliance" pitches never are.
3. **In-VPC by default, not as a $$$ add-on.** The default deployment topology is the secure one.

---

## 9. Meeting playbook — first 30 minutes with the CCO

Show, in order:

1. **Executive Today** populated with their own peer-enforcement feed (10 sec to load, sets the room).
2. **Readiness score** under CCO, CRO, CIO lenses with `biggest_drag` called out.
3. **Risk × Coverage matrix** — point at the high-risk × untested cell. Number is usually >0.
4. **Live click → JIRA → PR** in their own GitHub within the meeting (~5 min).

If you can get those four artifacts on the screen with their data, the trial-to-Edition conversion rate is ~70 %.

---

## 10. Internal handoff — what each team owns

| Function | Owns |
|---|---|
| Sales | Discovery, trial deployment scheduling, Edition contract close |
| Solutions engineering | Trial dashboard up in <3 days, first JIRA→PR demo |
| Customer success | First exam cycle white-gloved; quarterly business review on Readiness trend |
| Compliance ops (Fortress side) | Per-bank threshold calibration (`scripts/setup/calibrate_ofac_thresholds.py`), regulator add-on coverage |
| Engineering | Connector reliability, SLAs, new regulator onboarding |
| Security | SOC 2 program, customer security packs, attestations |

---

## 11. KPIs to commit to the board

| Metric | Target by end of Year 1 |
|---|---|
| Logos | 6 community banks + 2 regionals |
| ARR — Edition | $1.8M |
| ARR — Engine (per-tx) | $1.0M |
| Net revenue retention | ≥ 120 % (expansion from Edition to Engine) |
| Time-to-first-PR | < 7 days from trial start |
| Trial → Edition conversion | ≥ 50 % |
| Edition → Engine expansion | ≥ 40 % within 12 months |
| Dashboard daily active users per bank | ≥ 3 (CCO, CRO, CIO) |

---

## 12. What we do NOT sell (yet)

Naming these explicitly keeps sales conversations clean:

- We do **not** sell case management. Hummingbird and partners handle that; Fortress feeds them via `screening_events`.
- We do **not** sell transaction monitoring (TM) rules engines yet — that is a roadmap item.
- We do **not** sell regulatory consulting; the dashboard is the deliverable, not advice.
- We do **not** custodian customer data outside the bank's VPC — ever. This is a guarantee, not a feature.

---

## 13. Links

- Product overview for non-technical buyers: `docs/dashboard_layman_guide.md`
- Production cut-over checklist: `docs/p2_dashboard_api_production_integration.md`
- Architecture & deployment: `docs/runbooks/agent-1-infra.md`, `docs/runbooks/agent-4-sync.md`
- Scraper coverage: `docs/runbooks/scraper-architecture.md`
- OFAC calibration handoff: `docs/runbooks/ofac-calibration-gold-set.md`
