#!/usr/bin/env bash
# scripts/demo/dryrun.sh — scripted, timed pre-demo dry run.
#
# Implements docs/runbooks/demo-readiness.md §6. Walks the §6 checklist,
# enforces the per-flow time budgets, and prints a pass/fail report.
# Exits non-zero on any blocker so it can gate a release.
#
# Pre-reqs (local mode):
#   - `make up && make migrate` already run.
#   - P1 (port 8001) and P2 (port 8002) running with:
#       DEV_MODE=true ENVIRONMENT=test JIRA_WEBHOOK_SECRET=test-webhook-secret
#   - `cd dashboard && npm install && npm run e2e:install` already run.
#
# Usage:
#   ./scripts/demo/dryrun.sh                       # local
#   E2E_BASE_URL=https://… ./scripts/demo/dryrun.sh   # staging
#
# Override budgets via env if needed (seconds, FCP in ms):
#   DEMO_BUDGET_RESET DEMO_BUDGET_F1 DEMO_BUDGET_F2 DEMO_BUDGET_F3
#   DEMO_BUDGET_F4 DEMO_BUDGET_F5 DEMO_FCP_BUDGET_MS

# Intentionally not `set -e` — we want every step to run so the final
# report shows the full picture, even if an earlier step failed.
set -uo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/../.." && pwd )"
cd "${REPO_ROOT}"

# Budgets from demo-readiness.md §6. Env overrides allow CI tuning.
BUDGET_RESET="${DEMO_BUDGET_RESET:-120}"
BUDGET_F1="${DEMO_BUDGET_F1:-60}"
BUDGET_F2="${DEMO_BUDGET_F2:-90}"
BUDGET_F3="${DEMO_BUDGET_F3:-120}"
BUDGET_F4="${DEMO_BUDGET_F4:-30}"
BUDGET_F5="${DEMO_BUDGET_F5:-30}"
export DEMO_FCP_BUDGET_MS="${DEMO_FCP_BUDGET_MS:-2000}"

ARTIFACTS="${DEMO_DRYRUN_ARTIFACTS:-${REPO_ROOT}/dryrun-artifacts}"
mkdir -p "${ARTIFACTS}"

# Tier-1 / debug knobs — preserve pre-staged JSON when skipping playwright,
# and preserve any pre-staged reset log when skipping make demo-reset.
SKIP_RESET="${DEMO_SKIP_RESET:-0}"
SKIP_PLAYWRIGHT="${DEMO_SKIP_PLAYWRIGHT:-0}"
[[ "${SKIP_RESET}" != "1" ]] && rm -f "${ARTIFACTS}/reset.log"
[[ "${SKIP_PLAYWRIGHT}" != "1" ]] && rm -f \
  "${ARTIFACTS}/playwright.json" "${ARTIFACTS}/playwright.log"
rm -f "${ARTIFACTS}/playwright-summary.tsv"

# Per-row results, keyed by step id. Format: "<id>|<status>|<elapsed>|<detail>"
declare -a ROWS=()
record() {
  ROWS+=("$1|$2|$3|${4:-}")
}

# ── §6.1: make demo-reset ────────────────────────────────────────────
if [[ "${SKIP_RESET}" == "1" ]]; then
  echo "── §6.1 — SKIPPED (DEMO_SKIP_RESET=1) ──"
  record "6.1 demo-reset"        "SKIP" "-"        "skipped via DEMO_SKIP_RESET"
else
  echo "── §6.1 — make demo-reset (budget ${BUDGET_RESET}s) ──"
  t0=$(date +%s)
  if make demo-reset > "${ARTIFACTS}/reset.log" 2>&1; then
    t1=$(date +%s); elapsed=$((t1 - t0))
    if (( elapsed <= BUDGET_RESET )); then
      record "6.1 demo-reset"    "PASS" "${elapsed}s" ""
    else
      record "6.1 demo-reset"    "FAIL" "${elapsed}s" "exceeded ${BUDGET_RESET}s budget"
    fi
  else
    t1=$(date +%s); elapsed=$((t1 - t0))
    record "6.1 demo-reset"      "FAIL" "${elapsed}s" "non-zero exit — see reset.log"
  fi
fi
echo ""

# ── §6.2 – §6.7: Playwright F0–F5 with per-spec budgets ──────────────
if [[ "${SKIP_PLAYWRIGHT}" == "1" ]]; then
  echo "── §6.2–7 — SKIPPED (DEMO_SKIP_PLAYWRIGHT=1, expecting pre-staged playwright.json) ──"
  if [[ ! -s "${ARTIFACTS}/playwright.json" ]]; then
    echo "  (warning: no ${ARTIFACTS}/playwright.json found — all F0–F5 rows will FAIL as missing)"
  fi
else
  echo "── §6.2–7 — Playwright F0–F5 ──"
  (
    cd dashboard
    npx playwright test --reporter=json
  ) > "${ARTIFACTS}/playwright.json" 2> "${ARTIFACTS}/playwright.log"
  PW_EXIT=$?
  if (( PW_EXIT != 0 )); then
    echo "  (playwright exited ${PW_EXIT} — see playwright.log)"
  fi
fi

# Parse Playwright JSON → tab-separated rows of title, status, duration_ms.
python3 - "${ARTIFACTS}/playwright.json" > "${ARTIFACTS}/playwright-summary.tsv" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
if not p.exists():
    sys.exit(0)
raw = p.read_text() or "{}"
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)

def walk(suite):
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            duration = sum(r.get("duration", 0) for r in test.get("results", []))
            status = test.get("status", "unknown")
            print(f"{spec.get('title','?')}\t{status}\t{int(duration)}")
    for child in suite.get("suites", []):
        walk(child)

for s in data.get("suites", []):
    walk(s)
PY

# Map spec titles → step id + budget.
declare -A SAW=()
while IFS=$'\t' read -r title status duration_ms; do
  [[ -z "${title}" ]] && continue
  elapsed_s=$(( duration_ms / 1000 ))
  case "${title}" in
    F0*) id="6.2 F0 first-paint"; budget=$((DEMO_FCP_BUDGET_MS / 1000 + 5)) ;;
    F1*) id="6.3 F1 sdn-screen";  budget="${BUDGET_F1}" ;;
    F2*) id="6.4 F2 rule-sync";   budget="${BUDGET_F2}" ;;
    F3*) id="6.5 F3 jira-flow";   budget="${BUDGET_F3}" ;;
    F4*) id="6.6 F4 exec-overview"; budget="${BUDGET_F4}" ;;
    F5*) id="6.7 F5 vector-intel";  budget="${BUDGET_F5}" ;;
    *) continue ;;
  esac
  SAW["${id}"]=1
  if [[ "${status}" != "expected" ]]; then
    record "${id}" "FAIL" "${elapsed_s}s" "playwright status=${status}"
  elif (( elapsed_s > budget )); then
    record "${id}" "FAIL" "${elapsed_s}s" "exceeded ${budget}s budget"
  else
    record "${id}" "PASS" "${elapsed_s}s" ""
  fi
done < "${ARTIFACTS}/playwright-summary.tsv"

# Any spec that didn't run at all → record as MISSING (counts as fail).
for id in "6.2 F0 first-paint" "6.3 F1 sdn-screen" "6.4 F2 rule-sync" \
          "6.5 F3 jira-flow" "6.6 F4 exec-overview" "6.7 F5 vector-intel"; do
  [[ -z "${SAW[$id]:-}" ]] && record "${id}" "FAIL" "-" "no result — playwright didn't run this spec"
done

# ── §6.8 console.error: assertCleanJourney inside each spec already
#    fails the spec on any console.error. The line in the report just
#    reflects the F0–F5 aggregate.
any_fail=0; any_pass=0
for row in "${ROWS[@]}"; do
  IFS='|' read -r rid rstat _ _ <<< "${row}"
  if [[ "${rid}" =~ ^6\.[2-7] ]]; then
    [[ "${rstat}" == "FAIL" ]] && any_fail=1
    [[ "${rstat}" == "PASS" ]] && any_pass=1
  fi
done
if (( any_fail == 1 )); then
  record "6.8 zero console.error" "FAIL" "-" "one or more F0–F5 specs failed; check journey output"
elif (( any_pass == 1 )); then
  record "6.8 zero console.error" "PASS" "-" "asserted by every F0–F5 spec"
else
  record "6.8 zero console.error" "SKIP" "-" "no F0–F5 specs ran"
fi

# ── Final report ─────────────────────────────────────────────────────
echo ""
echo "════════ DRY RUN REPORT ════════"
printf "  %-26s %-7s %-9s %s\n" "STEP" "RESULT" "ELAPSED" "DETAIL"
printf "  %-26s %-7s %-9s %s\n" "----" "------" "-------" "------"
fail=0
for row in "${ROWS[@]}"; do
  IFS='|' read -r rid rstat relapsed rdetail <<< "${row}"
  case "${rstat}" in
    PASS) mark="✓" ;;
    FAIL) mark="✗"; fail=1 ;;
    SKIP) mark="·" ;;
    *)    mark="?" ;;
  esac
  printf "  %-26s %s %-5s %-9s %s\n" "${rid}" "${mark}" "${rstat}" "${relapsed}" "${rdetail}"
done

echo ""
echo "── Manual steps (operator must check) ──"
echo "  [ ] §6.9 Backup screencast recorded + on presenter's machine"
echo ""
echo "Artifacts: ${ARTIFACTS}"

if (( fail == 0 )); then
  echo "──────── DRY RUN: PASS ────────"
else
  echo "──────── DRY RUN: BLOCKED ─────"
fi
exit "${fail}"
