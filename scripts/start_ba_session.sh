#!/usr/bin/env bash
#
# Fortress P2 — open a read-only SQL session for the Business Analyst.
#
# What it does:
#   1. Launches the BA jump Fargate task (pipeline2-ba-jump.yml) with
#      --enable-execute-command and tag Purpose=ba-session.
#   2. Polls until the task is RUNNING and ECS Exec is ACTIVATING-done.
#   3. Opens an SSM port-forwarding session: localhost:55432 → RDS:5432.
#   4. Prints the psql one-liner the BA pastes into a second terminal.
#
# Authentication is the caller's existing AWS CLI identity. For a BA seat
# this should be an IAM principal with the managed policy
# fortress-p2-ba-query-readonly-${BANK_ID} (exported by pipeline2-ba-jump.yml)
# attached — and nothing else. SELECT-only access is enforced inside
# Postgres by the fortress_ba_ro role (init_ba_readonly_role.sql).
#
# Usage:
#   ./scripts/start_ba_session.sh <bank-id>
#
# Requirements on the operator's laptop:
#   - aws CLI v2.13+
#   - session-manager-plugin (https://docs.aws.amazon.com/systems-manager/...)
#   - psql client (only if running the BA's queries from this machine)
#   - jq

set -euo pipefail

BANK_ID="${1:?Usage: $0 <bank-id>}"
REGION="${AWS_REGION:-us-east-1}"
CLUSTER="fortress-pipeline2-${BANK_ID}"
TD_FAMILY="fortress-p2-ba-jump-${BANK_ID}"
LOCAL_PORT="${LOCAL_PORT:-55432}"

# --- preflight checks -------------------------------------------------------

for cmd in aws jq session-manager-plugin; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 2
  fi
done

echo "==> Resolving network config for bank=$BANK_ID region=$REGION"
SUBNET_A=$(aws ec2 describe-subnets --region "$REGION" \
  --filters "Name=tag:aws:cloudformation:logical-id,Values=PrivateSubnetA" \
            "Name=tag:Name,Values=*${BANK_ID}*" \
  --query "Subnets[0].SubnetId" --output text)
SUBNET_B=$(aws ec2 describe-subnets --region "$REGION" \
  --filters "Name=tag:aws:cloudformation:logical-id,Values=PrivateSubnetB" \
            "Name=tag:Name,Values=*${BANK_ID}*" \
  --query "Subnets[0].SubnetId" --output text)
SG_ECS=$(aws cloudformation list-exports --region "$REGION" \
  --query "Exports[?Name=='FortressP2-${BANK_ID}-SgEcs'].Value" --output text)
RDS_HOST=$(aws ssm get-parameter --region "$REGION" \
  --name "/fortress/${BANK_ID}/AURORA_HOST" \
  --query "Parameter.Value" --output text)
RDS_PORT=$(aws ssm get-parameter --region "$REGION" \
  --name "/fortress/${BANK_ID}/AURORA_PORT" \
  --query "Parameter.Value" --output text)
DB_NAME=$(aws ssm get-parameter --region "$REGION" \
  --name "/fortress/${BANK_ID}/DB_NAME" \
  --query "Parameter.Value" --output text)

if [[ -z "$SUBNET_A" || "$SUBNET_A" == "None" || -z "$SG_ECS" ]]; then
  echo "ERROR: could not resolve subnets / SgEcs for $BANK_ID — is pipeline2-vpc deployed?" >&2
  exit 3
fi

# --- launch jump task -------------------------------------------------------

echo "==> Starting jump task in cluster=$CLUSTER"
RUN_OUT=$(aws ecs run-task --region "$REGION" \
  --cluster "$CLUSTER" \
  --task-definition "$TD_FAMILY" \
  --launch-type FARGATE \
  --enable-execute-command \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_A,$SUBNET_B],securityGroups=[$SG_ECS],assignPublicIp=DISABLED}" \
  --tags "key=Purpose,value=ba-session" "key=BankId,value=${BANK_ID}" \
  --output json)

TASK_ARN=$(echo "$RUN_OUT" | jq -r '.tasks[0].taskArn')
if [[ -z "$TASK_ARN" || "$TASK_ARN" == "null" ]]; then
  echo "ERROR: RunTask returned no task ARN. Failures:" >&2
  echo "$RUN_OUT" | jq '.failures' >&2
  exit 4
fi
TASK_ID="${TASK_ARN##*/}"
echo "    task ARN: $TASK_ARN"

cleanup() {
  echo
  echo "==> Stopping jump task $TASK_ID"
  aws ecs stop-task --region "$REGION" --cluster "$CLUSTER" --task "$TASK_ID" \
    --reason "BA session ended" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# --- wait for RUNNING + ECS Exec ready --------------------------------------

echo "==> Waiting for task RUNNING + Exec channel ACTIVE (~45s typically)"
for _ in $(seq 1 60); do
  STATE=$(aws ecs describe-tasks --region "$REGION" \
    --cluster "$CLUSTER" --tasks "$TASK_ID" \
    --query "tasks[0].{LastStatus:lastStatus,ExecEnabled:enableExecuteCommand,Agent:containers[0].managedAgents[?name=='ExecuteCommandAgent'].lastStatus|[0]}" \
    --output json)
  LAST=$(echo "$STATE" | jq -r .LastStatus)
  AGENT=$(echo "$STATE" | jq -r .Agent)
  if [[ "$LAST" == "RUNNING" && "$AGENT" == "RUNNING" ]]; then
    break
  fi
  if [[ "$LAST" == "STOPPED" ]]; then
    echo "ERROR: task stopped before becoming ready. State: $STATE" >&2
    exit 5
  fi
  sleep 3
done

if [[ "$LAST" != "RUNNING" || "$AGENT" != "RUNNING" ]]; then
  echo "ERROR: task did not reach RUNNING + Exec agent RUNNING within timeout" >&2
  echo "Last state: $STATE" >&2
  exit 6
fi

RUNTIME_ID=$(aws ecs describe-tasks --region "$REGION" \
  --cluster "$CLUSTER" --tasks "$TASK_ID" \
  --query "tasks[0].containers[0].runtimeId" --output text)
SSM_TARGET="ecs:${CLUSTER}_${TASK_ID}_${RUNTIME_ID}"

# --- print psql one-liner for the BA ---------------------------------------

echo
echo "============================================================"
echo "  Tunnel: localhost:${LOCAL_PORT}  →  ${RDS_HOST}:${RDS_PORT}"
echo "  SSM target: $SSM_TARGET"
echo
echo "  In a SECOND terminal on this machine, run:"
echo
echo "    BA_PW=\$(aws secretsmanager get-secret-value \\"
echo "             --region ${REGION} \\"
echo "             --secret-id fortress/${BANK_ID}/db-password-readonly \\"
echo "             --query SecretString --output text | jq -r .password)"
echo
echo "    PGPASSWORD=\"\$BA_PW\" psql \\"
echo "      \"host=localhost port=${LOCAL_PORT} user=fortress_ba_ro dbname=${DB_NAME} sslmode=require\""
echo
echo "  The §12 cheat-sheet lives in"
echo "  docs/business_analyst_validation_guide.md."
echo
echo "  Ctrl-C this terminal to close the tunnel + stop the task."
echo "============================================================"
echo

# --- start the SSM port-forwarding session (blocks) ------------------------

exec aws ssm start-session --region "$REGION" \
  --target "$SSM_TARGET" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"${RDS_HOST}\"],\"portNumber\":[\"${RDS_PORT}\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}"
