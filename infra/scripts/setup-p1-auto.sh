#!/usr/bin/env bash
# infra/scripts/setup-p1-auto.sh
#
# Fully automated Pipeline 1 setup using Claude CLI.
# Runs all 8 prompts sequentially. Each step must succeed before the next.
#
# Usage:
#   export AWS_REGION=us-east-1
#   export AWS_PROFILE=fortress-prod
#   export DOMAIN_NAME=fortresslabs.dev
#   export ALERT_EMAIL=jamie@fortresslabs.dev
#   export ENVIRONMENT=production
#   export IMAGE_TAG=v5.0
#   chmod +x infra/scripts/setup-p1-auto.sh
#   ./infra/scripts/setup-p1-auto.sh
#
# Resume from a specific step:
#   START_STEP=4 ./infra/scripts/setup-p1-auto.sh

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE="${AWS_PROFILE:-fortress-prod}"
DOMAIN_NAME="${DOMAIN_NAME:-fortresslabs.dev}"
ALERT_EMAIL="${ALERT_EMAIL:-ops@fortresslabs.dev}"
ENVIRONMENT="${ENVIRONMENT:-production}"
IMAGE_TAG="${IMAGE_TAG:-v5.0}"
START_STEP="${START_STEP:-1}"
# Pilot deployments run on Aurora pgvector + tsvector and skip OpenSearch.
# To restore the full-prod OpenSearch index step, set SKIP_OPENSEARCH=false
# AND re-add the OpenSearch resources in pipeline1-data.yml.
SKIP_OPENSEARCH="${SKIP_OPENSEARCH:-true}"
LOG_DIR="infra/setup/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/setup_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

# ── Colours ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "${RED}✗ $*${NC}"; exit 1; }

# ── Check Claude CLI is installed ─────────────────────────────────────
if ! command -v claude &>/dev/null; then
  fail "Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code"
fi

ok "Claude CLI found: $(claude --version 2>/dev/null || echo 'installed')"
echo "Logs: $LOG_FILE"
echo ""

# ── Run a single prompt through Claude CLI ────────────────────────────
run_step() {
  local step_num="$1"
  local step_name="$2"
  local prompt_file="$3"
  local success_marker="$4"

  if [[ "$step_num" -lt "$START_STEP" ]]; then
    warn "Skipping step $step_num (START_STEP=$START_STEP)"
    return 0
  fi

  echo ""
  echo "══════════════════════════════════════════════════════════"
  echo "  STEP ${step_num}/8 — ${step_name}"
  echo "  $(date '+%H:%M:%S')"
  echo "══════════════════════════════════════════════════════════"

  # Run Claude CLI in non-interactive mode
  # --print flag outputs response without interactive prompts
  local output
  output=$(claude --print "$(cat "$prompt_file")" 2>&1 | tee -a "$LOG_FILE")

  echo "$output"

  # Check success marker in output
  if echo "$output" | grep -q "$success_marker"; then
    ok "Step $step_num complete: $step_name"
    echo "STEP_${step_num}_COMPLETE=$(date)" >> "$LOG_DIR/.progress"
    return 0
  else
    fail "Step $step_num did not print success marker: '$success_marker'
    
Check the log: $LOG_FILE
Resume from this step: START_STEP=$step_num ./infra/scripts/setup-p1-auto.sh"
  fi
}

# ── Export vars so Claude CLI subprocess can access them ──────────────
export AWS_REGION AWS_PROFILE DOMAIN_NAME ALERT_EMAIL ENVIRONMENT IMAGE_TAG

# ── Write each prompt to a temp file and run it ───────────────────────
TMPDIR_PROMPTS=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PROMPTS"' EXIT

# Parse PIPELINE1_SETUP.md into individual prompt files
python3 << PYEOF
import re

with open("infra/setup/PIPELINE1_SETUP.md") as f:
    content = f.read()

# Extract code blocks inside ## Prompt N sections
pattern = r'## Prompt (\d+)[^\n]*\n+\`\`\`\n(.*?)\`\`\`'
matches = re.findall(pattern, content, re.DOTALL)

for num, prompt_text in matches:
    path = "${TMPDIR_PROMPTS}/prompt_${num}.txt".replace('\${TMPDIR_PROMPTS}', "$TMPDIR_PROMPTS")
    # Expand shell variables in the prompt
    import os
    for var in ["AWS_REGION", "AWS_PROFILE", "DOMAIN_NAME", "ALERT_EMAIL", "ENVIRONMENT", "IMAGE_TAG"]:
        prompt_text = prompt_text.replace(f"\${{{var}}}", os.environ.get(var, ""))
        prompt_text = prompt_text.replace(f"\${var}", os.environ.get(var, ""))
    path = f"$TMPDIR_PROMPTS/prompt_{num}.txt"
    with open(path, "w") as f:
        f.write(prompt_text)
    print(f"Wrote prompt {num}: {path}")
PYEOF

# ── Run each step ─────────────────────────────────────────────────────

run_step 1 "Verify AWS access and prerequisites" \
  "$TMPDIR_PROMPTS/prompt_1.txt" \
  "All prerequisites met"

run_step 2 "Deploy VPC stack" \
  "$TMPDIR_PROMPTS/prompt_2.txt" \
  "STEP 2 COMPLETE"

run_step 3 "Deploy data stack (Aurora Serverless v2 + Redis + S3 + KMS)" \
  "$TMPDIR_PROMPTS/prompt_3.txt" \
  "STEP 3 COMPLETE"

# Step 4 is DEFERRED — it runs as a one-shot ECS task after Step 7. Aurora is
# only reachable from SgEcs (the ECS task SG), so we need the compute stack
# (and the image it runs) before we can bootstrap the schema.
warn "Step 4 deferred — runs after Step 7 as a one-shot ECS task."

if [[ "$SKIP_OPENSEARCH" == "true" ]]; then
  echo ""
  echo "══════════════════════════════════════════════════════════"
  echo "  STEP 5/8 — SKIPPED (SKIP_OPENSEARCH=true, pilot mode)"
  echo "  Aurora pgvector + tsvector handle hybrid search."
  echo "══════════════════════════════════════════════════════════"
  echo "STEP_5_SKIPPED=$(date)" >> "$LOG_DIR/.progress"
else
  run_step 5 "Create OpenSearch index mapping" \
    "$TMPDIR_PROMPTS/prompt_5.txt" \
    "STEP 5 COMPLETE"
fi

run_step 6 "Build and push Docker image" \
  "$TMPDIR_PROMPTS/prompt_6.txt" \
  "STEP 6 COMPLETE"

run_step 7 "Deploy compute stack (ECS + ALB + WAF)" \
  "$TMPDIR_PROMPTS/prompt_7.txt" \
  "STEP 7 COMPLETE"

# Now that the compute stack has provisioned EcsTaskDefInitP1 and SgEcs is in
# Aurora's allowlist, run the deferred schema bootstrap as a one-shot Fargate
# task and then bump the API service to DesiredCount=1.
run_step 4 "Bootstrap Aurora schema (one-shot ECS task)" \
  "$TMPDIR_PROMPTS/prompt_4.txt" \
  "STEP 4 COMPLETE"

# M5 — verify SDN prereqs (pgvector, pg_trgm, ofac_sdn_vectors table + HNSW
# index, tsv generated col + GIN index) are actually live before any /sync/
# ofac-sdn traffic flows. Without this, the WAF is up, the API is healthy,
# but /sync/ofac-sdn quietly degrades to legacy mode.
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  STEP 4b — verify SDN prereqs on Aurora"
echo "══════════════════════════════════════════════════════════"
if python3 scripts/setup/verify_sdn_prereqs.py 2>&1 | tee -a "$LOG_FILE"; then
  ok "SDN prereqs verified"
  echo "STEP_4B_COMPLETE=$(date)" >> "$LOG_DIR/.progress"
else
  fail "SDN prereqs check failed — fix migrations 021/022 and re-run with START_STEP=4"
fi

echo ""
warn "Step 8 requires the CNAME to be created first."
warn "Check the DNS record instruction from Step 7 output, then continue."
echo ""
read -p "Have you created the CNAME for api.${DOMAIN_NAME} ? (y/n): " dns_ready
if [[ "$dns_ready" != "y" && "$dns_ready" != "Y" ]]; then
  warn "Pausing. Run START_STEP=8 ./infra/scripts/setup-p1-auto.sh when DNS is ready."
  exit 0
fi

run_step 8 "Deploy security stack + enable scraper + first run" \
  "$TMPDIR_PROMPTS/prompt_8.txt" \
  "PIPELINE 1 FULLY OPERATIONAL"

# ── Final summary ──────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
ok "PIPELINE 1 SETUP COMPLETE"
echo ""
echo "  Full log saved to: $LOG_FILE"
echo "  Stacks created:"
aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'fortress-p1')].{Name:StackName,Status:StackStatus}" \
  --output table 2>/dev/null || true
echo ""
echo "  Next: register banks using:"
echo "  ./scripts/register-bank.sh --bank-id <id> --bank-name '<Name>' \\"
echo "    --bank-ip <nat-eip> --bank-profile bank-<id>"
echo "══════════════════════════════════════════════════════════"
