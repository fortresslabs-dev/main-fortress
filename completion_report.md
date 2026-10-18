# Fortress BSA/AML — Completion Report

**Audience:** BSA/AML Compliance Officer (CCO) + engineering.
**Purpose:** a plain-language record of what has been fixed/completed, what is
in progress, and what remains — for review once all work is done.
**Status:** living document · last updated 2026-10-18.

> **One-line summary.** The automated compliance **test-generation pipeline**
> (regulation → JIRA story → AI-written pytest → GitHub PR) was broken end-to-end
> and is now working *and* grounded on the correct rule; the **CCO dashboard** is
> being driven from partially-empty to fully populated, one use case at a time.

---

## 1. Test-generation pipeline — COMPLETE & improved

**What it does:** a CCO clicks a compliance rule → the system opens a JIRA story,
runs a 6-agent AI pipeline that retrieves the relevant BSA/AML rules, writes
`pytest` compliance tests, validates them, and opens a GitHub pull request with
the test code, posting the PR link back on the JIRA story.

**What was fixed (was fully non-functional):**
| Issue | Resolution |
|---|---|
| JIRA rejected story creation | Correct issue type (`Idea`) for the project |
| Workflow crashed on save | Database status vocabulary aligned to the code |
| AI model unreachable | Credentials wired to the pipeline |
| Dashboard panels erroring | Query/data mismatch fixed |
| No pull request created | Valid GitHub access token |

**Accuracy improvements (the important part for compliance):**
- **Right-rule coverage:** tests now always cover the *obligation of the rule the
  CCO actually selected* — previously coverage could land on unrelated neighbour
  rules. Verified on `FINCEN-1010-660-R3` (correspondent-account screening).
- **Grounded generation:** the AI is now given the rule's exact obligation text,
  its CFR citation, and the authoritative dollar thresholds (CTR $10k, SAR $5k,
  Travel Rule $3k, OFAC 85%, beneficial ownership 20%) as "must-test, do-not-invent"
  inputs. Result on the same rule: generated test went from **0 → 15** citations
  of §1010.660 and added a dedicated "screen at onboarding and periodically" test.
- **Self-healing:** if the AI emits unparseable code it retries automatically
  before opening a PR, so broken tests don't reach review.
- **Searchability:** every rule and obligation now has a vector embedding, so the
  AI's rule-retrieval finds the right BSA/AML rules (the FinCEN Part 1010 block
  was previously invisible to search).

**Evidence:** a scoring harness ran the BSA/AML rule slice end-to-end. On a
5-rule sample, 100% of generated tests cited the correct CFR and covered every
obligation. A full 116-rule run completed; detailed aggregate pending write-up.

**Caveat for the officer:** these checks confirm the test *targets the right
regulation and obligations* and is syntactically valid. They do **not** yet
confirm each assertion is substantively correct against the regulation — that
needs an SME review layer (planned; see §4).

---

## 2. CCO Dashboard — completion in progress (by use case)

The dashboard has 14 operator views. Status:

| Use case | What it shows | Status |
|---|---|---|
| Rules | BSA/AML rules ranked by severity/risk, test coverage | ✅ working (applicability now bank-scoped) |
| Obligations | Obligations by regulator, test-coverage % | ✅ working |
| Changes | Regulatory-change inbox + sign-off | ✅ working (OFAC change feed) |
| Enforcement | Peer enforcement actions / penalties | ✅ working (2,150 actions) |
| Workflows | 6-agent test-gen run history + PR links | ✅ working |
| OFAC Health | SDN screening engine health | ✅ working |
| **Exam Readiness** | Exam-readiness gauge + CCO/CRO/CIO lenses | ✅ **completed this round** (see §2a) |
| Findings / MRA | Findings & remediation tracker | ✅ completed this round (50 findings) |
| Reports | WORM-locked exam-evidence exports (6 types) | 🔄 **in progress** |
| Cases | OFAC screening case queue | ⬜ pending (screening data empty) |
| Investigation | Single screening case detail | ⬜ pending |
| SDN Search | Live sanctions screening | ⬜ pending |
| Rules Performance | OFAC rule precision / alert volume | ⬜ pending |
| Vector Intel | Screening model governance | ⬜ pending |
| Executive Today | Board same-day OFAC tiles | ⬜ pending |

### 2a. Exam Readiness — what "complete" means
The readiness score is built from 10 dimensions, each scored 0–100 and
weighted by role (CCO / CRO / CIO). We populated the bank's compliance data so
the score reflects reality instead of placeholders:
- **Applicable-obligation coverage** (which required CFRs our rule corpus covers)
- **Control coverage** (bank controls mapped to obligations — 198 mapped)
- **Governance evidence** (all 8 board/committee/policy artifacts on file)
- **Findings remediation** (50 findings tracked)

Current overall readiness: **53 / 100 ("developing")**, 9 of 10 dimensions
measured. This is an *honest* score — the two low dimensions are real gaps, not
display bugs (see §3).

---

## 3. Known gaps & decisions for the officer

| Gap | Impact | Options |
|---|---|---|
| **Enforcement risk scores not computed** | Readiness "risk-assessment currency" = 0; `/risk` view empty | Run the enforcement-risk scoring enrichment (we have 2,150 enforcement actions to score) |
| **No regulatory *rule* changes locally** (only OFAC SDN) | Readiness "change management" shows the neutral default | Expected locally; real rule-change feed comes from the live scraper |
| **Exam reports not yet generated** | Readiness "point-in-time reporting" unmeasured; Reports page empty | Generating now (§2 Reports) |
| **Screening domain empty** (no cases/events/rules) | 6 OFAC screening views empty | Seed screening rules + run live sanction screens |
| **Substantive test correctness unverified** | Tests target the right rule but assertions not SME-checked | Build an SME "golden answer" review layer |

---

## 4. What's next (planned order)
1. **Reports** — generate all 6 exam-evidence exports end-to-end (also completes readiness "reporting").
2. **Enforcement risk backfill** — score the 2,150 enforcement actions → fixes readiness "risk currency" + the Risk view.
3. **Screening domain** — seed screening rules + generate screening cases so the 6 OFAC views populate.
4. **Changes drawer** — seed change-record comments.
5. **Final end-to-end verification** — confirm all 14 views return real data.
6. **SME validation layer** — a golden-set of rules with officer-reviewed expected assertions, to measure *substantive* test correctness (not just grounding).
7. **Production rollout** — the fixes live on a branch; a documented rollout runbook exists (`docs/runbooks/prod-rollout-testgen.md`).

---

## 5. Deployment note
All code fixes are committed on the `code_cleanup` branch (not yet merged to
`main` or deployed to AWS). The data population described here is in the **local**
environment for validation; production requires running the documented migration
+ backfill steps. Nothing here has been deployed to a bank's live environment yet.
