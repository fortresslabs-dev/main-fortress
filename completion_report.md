# Fortress BSA/AML — Completion Report

**Audience:** BSA/AML Compliance Officer (CCO) + engineering.
**Purpose:** a plain-language record of what has been fixed/completed, what
remains, and the honest gaps — for review now that the dashboard-completion
work is done.
**Status:** living document · last updated 2026-10-21.

> **One-line summary.** The automated compliance **test-generation pipeline**
> was broken end-to-end and is now working *and* grounded on the correct rule;
> the **CCO dashboard** was driven from mostly-empty to **13 of 14 views showing
> real data**, verified end-to-end. The remaining thin views are honest data
> gaps that need the production data feeds (not bugs, and not faked).

---

## 1. Test-generation pipeline — COMPLETE & improved

A CCO clicks a rule → the system opens a JIRA story, runs a 6-agent AI pipeline
that retrieves the relevant BSA/AML rules, writes `pytest` compliance tests,
validates them, and opens a GitHub pull request, posting the PR link to JIRA.

**Fixed (was fully non-functional):** JIRA issue type, workflow DB status
vocabulary, AI-model credentials, dashboard query errors, GitHub access token.

**Accuracy improvements:**
- **Right-rule coverage** — tests always cover the obligation of the rule the
  CCO selected (previously drifted to neighbour rules).
- **Grounded generation** — the AI gets the rule's exact obligation text, CFR
  citation, and authoritative thresholds (CTR $10k, SAR $5k, Travel $3k, OFAC
  85%, beneficial ownership 20%) as must-test inputs. On `FINCEN-1010-660-R3`,
  generated tests went 0 → 15 §1010.660 citations with a dedicated screening test.
- **Self-healing** — unparseable AI output is retried before a PR is opened.
- **Searchability** — every rule *and* obligation now has a vector embedding.

**Evidence:** an objective scoring harness; a 5-rule sample scored 100% on
right-rule grounding; a 116-rule BSA/AML run completed.

**Caveat:** these confirm the test targets the right regulation/obligation and
is syntactically valid — **not** that each assertion is substantively correct.
That needs the SME review layer (§4).

---

## 2. CCO Dashboard — COMPLETE (13/14 views populated)

Final end-to-end verification: **all 17 endpoints return HTTP 200.**

| Use case | Status | Note |
|---|---|---|
| Rules | ✅ data | 50 applicable rules, bank-scoped; mark-N/A works |
| Obligations | ✅ data | 198 obligations, coverage % by regulator |
| Enforcement | ✅ data | 2,150 peer enforcement actions |
| Workflows | ✅ data | 6-agent run history + PR links |
| OFAC Health | ✅ data | 19.8k SDN entries, 47.6k variants, 100% embedded |
| **Exam Readiness** | ✅ data | score **57 / "developing"**, 9/10 dims measured (§2a) |
| Findings / MRA | ✅ data | 50 findings (endpoint was a stub — now reads the table) |
| Reports | ✅ data | all 6 exam-evidence exports generated, signed, downloadable |
| Cases | ✅ data | 4 OFAC screening cases |
| Investigation | ✅ data | case detail + decision history |
| SDN Search | ✅ data | live screening (7 run: 4 HOLD hits, 3 clean) |
| Rules Performance | ✅ data | 4 screening rules, alert/precision stats |
| Vector Intel | ✅ data | embedding coverage + score histogram (Admin/CRO role) |
| Executive Today | ✅ data | 6 same-day OFAC tiles |
| **Changes** | ⚠️ default-empty | works with "include OFAC" (20 shown); default CCO inbox hides OFAC and there are **no non-OFAC rule changes** locally (needs P1 change-detector) |

### 2a. Exam Readiness detail (score 57, 9/10 dims measured)
| Dimension | Score | Basis |
|---|---|---|
| Applicable-obligation coverage | 23 | registry: 8 of 35 required CFRs present in corpus |
| Control coverage | 100 | 198 obligations mapped to controls |
| Governance evidence | 100 | all 8 board/committee/policy artifacts on file |
| Findings remediation | 100 | 50 findings, all closed |
| Change management | 80 | neutral default — no non-OFAC rule changes locally |
| Testing & rationale | 10 | obligations with test coverage (grows as tests run) |
| Testing execution rate | 0 | 1 rule tested of 476 |
| **Risk-assessment currency** | **0** | ⚠️ enforcement risk not scored (§3) |
| Point-in-time reporting | 100 | 6 signed exam exports in the window |
| Engine integrity | measured | SDN freshness + embedding + pipeline health |

The score is **honest** — the low dimensions are real posture gaps, not display bugs.

---

## 3. Honest gaps (documented, not faked) — for the officer

| Gap | Effect on dashboard | Root cause / fix |
|---|---|---|
| **Enforcement risk not scored** | Readiness "risk-assessment currency" = 0; Risk view thin | `enforcement_actions.regulations_cited` is empty for all 2,150 rows — P1 never extracted which CFRs each action cited. The scorer works but has nothing to attribute. **Fix = P1 enforcement-citation extraction.** We did **not** fabricate citations. |
| **No non-OFAC regulatory changes** | Changes default inbox empty; readiness "change management" = neutral default | Only OFAC SDN changes exist locally (52k). Real rule-change records come from the P1 scraper's change detector. |
| **Substantive test correctness** | n/a | Tests target the right rule but assertions aren't SME-verified. Needs the golden-set review layer. |
| **Low coverage / testing dims** | Readiness testing≈10, execution≈0 | Real — only a handful of rules have generated tests so far; rises as the pipeline runs. |

---

## 4. What's done vs. next

**Done this effort:**
- Test-gen pipeline fixed + accuracy (grounding, Fix A, embeddings, self-heal)
- Readiness dimensions seeded to registry mode (inventory/control/governance/findings)
- Findings endpoint fixed; Reports generated+signed; Screening domain populated; Rules applicability; Changes drawer comments
- Full end-to-end verification (17/17 endpoints 200)

**Next (requires production data feeds or further work):**
1. **P1 enforcement-citation extraction** → unblocks Risk view + readiness risk-currency
2. **P1 rule-change detection** → populates the Changes inbox + change-management dim
3. **SME golden-set review layer** → measures *substantive* test correctness
4. **Production rollout** → fixes live on branch `code_cleanup`; runbook at `docs/runbooks/prod-rollout-testgen.md`

---

## 5. Deployment note
All code fixes are committed on the `code_cleanup` branch (not merged to `main`
or deployed to AWS). The data population here is in the **local** validation
environment — production requires the documented migration + backfill + rollout
steps. **Nothing here is deployed to a bank's live environment.** The local
seeding used the bank id `testbank`; no fabricated regulatory attribution was
introduced (enforcement/rule-change gaps were left honest).
