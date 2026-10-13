#!/usr/bin/env bash
# infra/scripts/deprovision-p2.sh
# Pipeline 2 deprovisioning — runs in the BANK's AWS account.
#
# Status: B7 Eng-side complete (FTR §13.1 B7 still partial pending Legal DPA sign-off).
# Every destructive AWS call is gated behind CONFIRM=DELETE-<bank-id>; in DRY-RUN mode
# (default), the script discovers all resources and prints what it would do but makes
# only read-only AWS calls.
#
# Usage:
#   ./infra/scripts/deprovision-p2.sh <bank-id> [environment]                     # dry-run
#   CONFIRM=DELETE-<bank-id> ./infra/scripts/deprovision-p2.sh <bank-id> [env]    # LIVE
#
# What this script DOES (in order):
#   [1/10] Discover resources + validate bank exists
#   [2/10] Trigger final export bundle via exports-worker (one-shot ECS run-task)
#   [3/10] Drain + delete ECS services on the cluster (P2 API; exports-worker is run-task)
#   [4/10] Delete dashboard SPA S3 bucket contents (bucket itself owned by CFN dashboard stack)
#   [5/10] Disable + delete CloudFront distribution (~15-20 min wait for disable to propagate)
#   [6/10] Delete WAFv2 web ACLs (id + lock-token lookup; disassociate from ALB first)
#   [7/10] Final RDS snapshot (tagged) + delete CFN stacks in reverse dependency order
#   [8/10] Cross-account revoke at Fortress (HMAC + WAF IPSet allowlist)
#   [9/10] KMS CMK pending-deletion proof — surviving exports CMK (see docs/runbooks/p2-key-deletion.md)
#   [10/10] Summary + remaining-resources reminder
#
# What this script DOES NOT do (intentional — Legal/Compliance gates):
#   * Delete the exports S3 bucket. Under Object Lock COMPLIANCE 1825d (5y).
#     The bank must wait for retention expiry OR formally request KMS key
#     deletion (30d pending window), accepting cryptographic destruction.
#   * Schedule the bank's exports KMS CMK for deletion by default. Step 9
#     prints the schedule-key-deletion command and captures a proof-shape of
#     the *current* KeyState; it only fires schedule-key-deletion when the
#     operator explicitly sets KEY_DELETION_MODE=execute (see
#     docs/runbooks/p2-key-deletion.md §4.2).
#   * Delete bank-side CloudTrail. Bank's SOC owns retention.
#   * Touch any non-Fortress resource (only resources matching fortress-p2-*-<bank-id>
#     are targeted).
#
# Resource naming reality (verified against testbank 2026-06-29):
#   * Stacks         : fortress-p2-{vpc,data,compute,dashboard,billing}-${BANK_ID}
#                      fortress-p2-{exports-bucket,exports-worker}-${BANK_ID}-${ENVIRONMENT}
#   * ECS cluster    : fortress-pipeline2-${BANK_ID}
#   * ECS service    : fortress-pipeline2-${BANK_ID}        (single; worker is run-task)
#   * RDS instance   : fortress-p2-pg-${BANK_ID}-${ENVIRONMENT}
#   * Dashboard S3   : fortress-dashboard-${BANK_ID}-${ENVIRONMENT}
#   * Exports S3     : fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}   (Object-Lock!)
#   * WAF ACL        : fortress-p2-waf-${BANK_ID}           (REGIONAL, on the ALB)
#   * Cognito pool   : output `CognitoUserPoolId` of fortress-p2-dashboard-${BANK_ID}

set -euo pipefail

# ── Args + env ────────────────────────────────────────────────────────────────
BANK_ID="${1:?Usage: deprovision-p2.sh <bank-id> [environment]}"
ENVIRONMENT="${2:-production}"
CONFIRM="${CONFIRM:-}"
SKIP_EXPORT_BUNDLE="${SKIP_EXPORT_BUNDLE:-false}"
FINAL_SNAPSHOT_RETAIN_DAYS="${FINAL_SNAPSHOT_RETAIN_DAYS:-90}"
DEPROVISION_TICKET_ID="${DEPROVISION_TICKET_ID:-NO-TICKET}"
FORTRESS_ACCOUNT_ID="${FORTRESS_ACCOUNT_ID:-520385696621}"
# Key deletion modes (step 9):
#   schedule  (default) — print the schedule-key-deletion command + capture
#                         current describe-key proof shape. NO AWS-state change.
#   execute             — actually schedule-key-deletion (LIVE mode only;
#                         requires DEPROVISION_TICKET_ID; bank Legal sign-off
#                         expected — see docs/runbooks/p2-key-deletion.md §3).
#   skip                — bank retains custody indefinitely; step 9 prints
#                         only the verification commands.
KEY_DELETION_MODE="${KEY_DELETION_MODE:-schedule}"
KEY_PENDING_WINDOW_DAYS="${KEY_PENDING_WINDOW_DAYS:-30}"

DRY_RUN=true
if [[ "$CONFIRM" == "DELETE-${BANK_ID}" ]]; then
  DRY_RUN=false
fi

REGION="${AWS_REGION:-us-east-1}"
CLUSTER="fortress-pipeline2-${BANK_ID}"
SERVICE_API="fortress-pipeline2-${BANK_ID}"
DB_ID="fortress-p2-pg-${BANK_ID}-${ENVIRONMENT}"
DASHBOARD_BUCKET="fortress-dashboard-${BANK_ID}-${ENVIRONMENT}"
EXPORTS_BUCKET="fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}"
WAF_ACL_NAME="fortress-p2-waf-${BANK_ID}"

# Stack names — exports stacks carry the env suffix, the rest don't
STACK_VPC="fortress-p2-vpc-${BANK_ID}"
STACK_DATA="fortress-p2-data-${BANK_ID}"
STACK_COMPUTE="fortress-p2-compute-${BANK_ID}"
STACK_DASHBOARD="fortress-p2-dashboard-${BANK_ID}"
STACK_BILLING="fortress-p2-billing-${BANK_ID}"
STACK_EXPORTS_BUCKET="fortress-p2-exports-bucket-${BANK_ID}-${ENVIRONMENT}"
STACK_EXPORTS_WORKER="fortress-p2-exports-worker-${BANK_ID}-${ENVIRONMENT}"

# ── Banner ───────────────────────────────────────────────────────────────────
cat <<EOF
══════════════════════════════════════════════════════════════════════════════
  Fortress Pipeline 2 — Deprovisioning
══════════════════════════════════════════════════════════════════════════════
  Bank ID        : ${BANK_ID}
  Environment    : ${ENVIRONMENT}
  Region         : ${REGION}
  Mode           : $([[ "$DRY_RUN" == "true" ]] && echo "DRY-RUN (set CONFIRM=DELETE-${BANK_ID} to execute)" || echo "★ LIVE — WILL DELETE ★")
  Ticket         : ${DEPROVISION_TICKET_ID}
  Snapshot       : retained ${FINAL_SNAPSHOT_RETAIN_DAYS} days
  KMS keys       : ${KEY_DELETION_MODE} (window: ${KEY_PENDING_WINDOW_DAYS}d)
  Fortress acct  : ${FORTRESS_ACCOUNT_ID}
══════════════════════════════════════════════════════════════════════════════
EOF

if [[ "$DRY_RUN" == "false" ]]; then
  if [[ "$DEPROVISION_TICKET_ID" == "NO-TICKET" ]]; then
    echo "✗ Refusing to run LIVE without DEPROVISION_TICKET_ID set."
    exit 2
  fi
  echo ">> 15-second cancel window. Ctrl+C to abort. <<"
  sleep 15
fi

# ── Helpers ──────────────────────────────────────────────────────────────────
run_or_print() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "    DRY: $*"
  else
    echo "    RUN: $*"
    eval "$@"
  fi
}

# Read-only describe; runs in both modes.
describe() {
  eval "$@" 2>/dev/null || true
}

stack_output() {
  local stack="$1" key="$2"
  describe "aws cloudformation describe-stacks --stack-name '$stack' --region '$REGION' --query 'Stacks[0].Outputs[?OutputKey==\`$key\`].OutputValue' --output text"
}

stack_exists() {
  local stack="$1"
  describe "aws cloudformation describe-stacks --stack-name '$stack' --region '$REGION' --query 'Stacks[0].StackStatus' --output text"
}

# ── [1/10] Discover resources + validate ──────────────────────────────────────
echo ""
echo "── [1/10] Discover resources + validate bank exists ─────────────────────"

EXISTING_STACKS=()
for stk in "$STACK_VPC" "$STACK_DATA" "$STACK_COMPUTE" "$STACK_DASHBOARD" "$STACK_BILLING" "$STACK_EXPORTS_BUCKET" "$STACK_EXPORTS_WORKER"; do
  status="$(stack_exists "$stk")"
  if [[ -n "$status" && "$status" != "None" ]]; then
    EXISTING_STACKS+=("$stk")
    printf "    found    %-60s  %s\n" "$stk" "$status"
  else
    printf "    missing  %-60s  (will skip)\n" "$stk"
  fi
done

if [[ ${#EXISTING_STACKS[@]} -eq 0 ]]; then
  echo ""
  echo "✗ No Fortress P2 stacks found for bank '${BANK_ID}' env '${ENVIRONMENT}'."
  echo "  Nothing to do. Check BANK_ID and ENVIRONMENT, then re-run."
  exit 3
fi

# Resolve dynamic resource IDs from stack outputs
USER_POOL_ID="$(stack_output "$STACK_DASHBOARD" CognitoUserPoolId)"
CF_DIST_ID="$(stack_output "$STACK_DASHBOARD" CloudFrontDistributionId)"
ALB_DNS="$(stack_output "$STACK_COMPUTE" AlbDns)"

# WAF ACL: look up by name in REGIONAL scope
WAF_ROW="$(describe "aws wafv2 list-web-acls --scope REGIONAL --region '$REGION' --query 'WebACLs[?Name==\`${WAF_ACL_NAME}\`].[Id,LockToken,ARN]' --output text")"
WAF_ID="$(awk '{print $1}' <<<"$WAF_ROW")"
WAF_LOCK="$(awk '{print $2}' <<<"$WAF_ROW")"
WAF_ARN="$(awk '{print $3}' <<<"$WAF_ROW")"

# ALB ARN for WAF disassociation
ALB_ARN="$(describe "aws elbv2 describe-load-balancers --region '$REGION' --query 'LoadBalancers[?DNSName==\`${ALB_DNS}\`].LoadBalancerArn' --output text")"

# NAT EIP — needed for cross-account WAF IPSet removal
NAT_EIP="$(stack_output "$STACK_VPC" NatGatewayEip)"

cat <<EOF

  Discovered resource IDs:
    Cognito UserPool : ${USER_POOL_ID:-<none>}
    CloudFront dist  : ${CF_DIST_ID:-<none>}
    ALB DNS / ARN    : ${ALB_DNS:-<none>}
                       ${ALB_ARN:-<none>}
    WAF ACL          : ${WAF_ID:-<none>} (lock-token ${WAF_LOCK:-<none>})
    NAT EIP          : ${NAT_EIP:-<none>}
    RDS instance     : ${DB_ID}
    Dashboard bucket : ${DASHBOARD_BUCKET}
    Exports bucket   : ${EXPORTS_BUCKET}  (Object-Lock COMPLIANCE, NOT deleted)
EOF

# ── [2/10] Final export bundle ────────────────────────────────────────────────
echo ""
echo "── [2/10] Final export bundle ────────────────────────────────────────────"
if [[ "$SKIP_EXPORT_BUNDLE" == "true" ]]; then
  echo "    SKIP_EXPORT_BUNDLE=true — assuming bank already pulled exports"
else
  WORKER_TASK_DEF="$(stack_output "$STACK_EXPORTS_WORKER" TaskDefArn)"
  if [[ -z "$WORKER_TASK_DEF" ]]; then
    echo "    WARN: exports-worker stack missing TaskDefArn output — skipping bundle"
  else
    # Subnets + SG come from the VPC stack (private subnets for the worker)
    SUBNETS="$(stack_output "$STACK_VPC" PrivateSubnetA),$(stack_output "$STACK_VPC" PrivateSubnetB)"
    WORKER_SG="$(stack_output "$STACK_VPC" SgEcs)"
    run_or_print "aws ecs run-task --cluster '${CLUSTER}' --task-definition '${WORKER_TASK_DEF}' \
      --launch-type FARGATE \
      --network-configuration 'awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${WORKER_SG}],assignPublicIp=DISABLED}' \
      --overrides '{\"containerOverrides\":[{\"name\":\"exports-worker\",\"command\":[\"python\",\"-m\",\"src.exports.final_bundle\",\"--ticket\",\"${DEPROVISION_TICKET_ID}\"]}]}' \
      --region '${REGION}'"
    echo "    NOTE: bundle lands at s3://${EXPORTS_BUCKET}/_deprovisioning/${DEPROVISION_TICKET_ID}/"
    echo "    NOTE: Verify bundle PUT before proceeding; LIVE mode does not block on completion."
  fi
fi

# ── [3/10] ECS services drain + delete ────────────────────────────────────────
echo ""
echo "── [3/10] ECS services drain + delete ────────────────────────────────────"
if describe "aws ecs describe-services --cluster '${CLUSTER}' --services '${SERVICE_API}' --region '${REGION}' --query 'services[0].status' --output text" | grep -q ACTIVE; then
  run_or_print "aws ecs update-service --cluster '${CLUSTER}' --service '${SERVICE_API}' --desired-count 0 --region '${REGION}'"
  run_or_print "aws ecs wait services-stable --cluster '${CLUSTER}' --services '${SERVICE_API}' --region '${REGION}'"
  run_or_print "aws ecs delete-service --cluster '${CLUSTER}' --service '${SERVICE_API}' --region '${REGION}'"
else
  echo "    ECS service ${SERVICE_API} not ACTIVE — skipping drain"
fi
echo "    NOTE: exports-worker has no long-running service (run-task model) — nothing to drain."

# ── [4/10] Dashboard S3 bucket empty ──────────────────────────────────────────
# Bucket itself is owned by the dashboard CFN stack (deleted in step 7); we just
# empty it here so the stack-delete doesn't fail on non-empty bucket.
echo ""
echo "── [4/10] Empty dashboard SPA bucket ─────────────────────────────────────"
if describe "aws s3api head-bucket --bucket '${DASHBOARD_BUCKET}' --region '${REGION}'" >/dev/null 2>&1; then
  obj_count="$(describe "aws s3 ls 's3://${DASHBOARD_BUCKET}' --recursive --summarize" | grep -i 'Total Objects' | awk '{print $3}')"
  echo "    Bucket ${DASHBOARD_BUCKET} has ${obj_count:-0} objects"
  run_or_print "aws s3 rm 's3://${DASHBOARD_BUCKET}' --recursive"
else
  echo "    Bucket ${DASHBOARD_BUCKET} not present — skipping"
fi

# ── [5/10] CloudFront disable + delete ────────────────────────────────────────
# CF distribution must be Enabled=false AND DeploymentStatus=Deployed before delete.
# Disable typically takes 15-20 min to propagate. LIVE mode waits; dry-run prints.
echo ""
echo "── [5/10] CloudFront disable + delete ────────────────────────────────────"
if [[ -n "$CF_DIST_ID" ]]; then
  # Fetch config + ETag for the update call
  ETAG="$(describe "aws cloudfront get-distribution-config --id '${CF_DIST_ID}' --query 'ETag' --output text")"
  echo "    Distribution ${CF_DIST_ID} current ETag: ${ETAG:-<none>}"
  run_or_print "aws cloudfront get-distribution-config --id '${CF_DIST_ID}' > /tmp/cf-${BANK_ID}.json"
  run_or_print "jq '.DistributionConfig | .Enabled = false' /tmp/cf-${BANK_ID}.json > /tmp/cf-${BANK_ID}-disabled.json"
  run_or_print "aws cloudfront update-distribution --id '${CF_DIST_ID}' --if-match '${ETAG}' --distribution-config file:///tmp/cf-${BANK_ID}-disabled.json"
  run_or_print "aws cloudfront wait distribution-deployed --id '${CF_DIST_ID}'"
  # Re-fetch ETag (it changes on update) before delete
  run_or_print "NEW_ETAG=\$(aws cloudfront get-distribution-config --id '${CF_DIST_ID}' --query ETag --output text) && \
    aws cloudfront delete-distribution --id '${CF_DIST_ID}' --if-match \"\$NEW_ETAG\""
else
  echo "    No CloudFront distribution in dashboard stack — skipping"
fi

# ── [6/10] WAFv2 delete (disassociate from ALB first) ─────────────────────────
echo ""
echo "── [6/10] WAFv2 web ACL delete ───────────────────────────────────────────"
if [[ -n "$WAF_ID" && -n "$WAF_LOCK" ]]; then
  if [[ -n "$ALB_ARN" ]]; then
    run_or_print "aws wafv2 disassociate-web-acl --resource-arn '${ALB_ARN}' --region '${REGION}'"
  fi
  # Re-fetch lock-token after disassociation (it may change)
  run_or_print "WAF_LOCK_FRESH=\$(aws wafv2 list-web-acls --scope REGIONAL --region '${REGION}' --query 'WebACLs[?Name==\`${WAF_ACL_NAME}\`].LockToken' --output text) && \
    aws wafv2 delete-web-acl --name '${WAF_ACL_NAME}' --scope REGIONAL --id '${WAF_ID}' --lock-token \"\$WAF_LOCK_FRESH\" --region '${REGION}'"
else
  echo "    No WAF ACL ${WAF_ACL_NAME} — skipping"
fi

# ── [7/10] Final snapshot + CFN stack teardown (reverse dependency order) ─────
echo ""
echo "── [7/10] Final RDS snapshot + CFN stack teardown ────────────────────────"
SNAPSHOT_ID="${DB_ID}-final-$(date -u +%Y%m%d-%H%M)"
echo "    Final snapshot ID: ${SNAPSHOT_ID}"

# Snapshot first (independent of stack deletion timing)
run_or_print "aws rds create-db-snapshot --db-instance-identifier '${DB_ID}' --db-snapshot-identifier '${SNAPSHOT_ID}' --region '${REGION}'"
run_or_print "aws rds wait db-snapshot-completed --db-snapshot-identifier '${SNAPSHOT_ID}' --region '${REGION}'"
run_or_print "aws rds add-tags-to-resource --resource-name \"arn:aws:rds:${REGION}:\$(aws sts get-caller-identity --query Account --output text):snapshot:${SNAPSHOT_ID}\" \
  --tags 'Key=DeprovisionTicket,Value=${DEPROVISION_TICKET_ID}' 'Key=BankId,Value=${BANK_ID}' 'Key=RetainUntil,Value='\"\$(date -u -d \"+${FINAL_SNAPSHOT_RETAIN_DAYS} days\" +%Y-%m-%d)\""

# CFN stack delete order (reverse of dependency):
#   billing → dashboard → exports-worker → compute → exports-bucket (Object-Lock; will fail intentionally)
#   data → vpc
# The exports-bucket stack delete will fail because the bucket is Object-Locked.
# That's deliberate — bank can revisit after retention or via KMS deletion.
TEARDOWN_ORDER=(
  "$STACK_BILLING"
  "$STACK_DASHBOARD"
  "$STACK_EXPORTS_WORKER"
  "$STACK_COMPUTE"
  "$STACK_DATA"
  "$STACK_VPC"
)

for stk in "${TEARDOWN_ORDER[@]}"; do
  if printf '%s\n' "${EXISTING_STACKS[@]}" | grep -qx "$stk"; then
    run_or_print "aws cloudformation delete-stack --stack-name '${stk}' --region '${REGION}'"
    run_or_print "aws cloudformation wait stack-delete-complete --stack-name '${stk}' --region '${REGION}'"
  fi
done

echo ""
echo "    NOTE: ${STACK_EXPORTS_BUCKET} intentionally NOT deleted (Object-Lock COMPLIANCE 1825d)."
echo "    Bank-side teardown of that stack must wait for retention expiry or KMS key deletion."

# ── [8/10] Cross-account revoke at Fortress (P1) ──────────────────────────────
echo ""
echo "── [8/10] Cross-account revoke at Fortress (P1) ──────────────────────────"
echo "    Two actions needed in Fortress account (${FORTRESS_ACCOUNT_ID}):"
echo "      a) Revoke bank HMAC credential in P1 client_credentials table"
echo "      b) Remove NAT EIP ${NAT_EIP:-<unknown>} from P1 WAF IPSet allowlist"
echo ""
if aws sts assume-role \
   --role-arn "arn:aws:iam::${FORTRESS_ACCOUNT_ID}:role/FortressDeprovisionCallback" \
   --role-session-name "deprov-${BANK_ID}-$(date +%s)" \
   --region "${REGION}" >/dev/null 2>&1; then
  echo "    Cross-account role assumable — automation possible (not yet implemented)."
  echo "    TODO(C2-followup): wire scripts/rotate-bank-credentials.sh + register-bank-ip.sh --remove"
  echo "    via assumed credentials. For now, run them manually in the Fortress account."
else
  echo "    Cross-account role NOT assumable — Fortress eng must run manually:"
  echo "      1) scripts/rotate-bank-credentials.sh ${BANK_ID} --revoke"
  echo "      2) infra/scripts/register-bank-ip.sh --remove ${BANK_ID}"
fi

# ── [9/10] KMS CMK pending-deletion proof ────────────────────────────────────
# Procedure documented in docs/runbooks/p2-key-deletion.md.
# Default mode prints the schedule-key-deletion command + captures the
# current describe-key shape (proof). KEY_DELETION_MODE=execute actually
# fires schedule-key-deletion (LIVE mode only, with bank-Legal sign-off).
echo ""
echo "── [9/10] KMS CMK pending-deletion proof ─────────────────────────────────"
EXPORTS_KEY_ALIAS="alias/fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}"
EXPORTS_KEY_ARN="$(describe "aws kms describe-key --key-id '${EXPORTS_KEY_ALIAS}' --region '${REGION}' --query 'KeyMetadata.Arn' --output text")"

if [[ -z "$EXPORTS_KEY_ARN" || "$EXPORTS_KEY_ARN" == "None" ]]; then
  echo "    No exports CMK found at ${EXPORTS_KEY_ALIAS}."
  echo "    Either the bank never received P2, the exports-bucket stack was"
  echo "    deleted out-of-band (against policy), or the alias was already"
  echo "    severed. Inspect the audit trail before continuing."
else
  EXPORTS_KEY_STATE="$(describe "aws kms describe-key --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}' --query 'KeyMetadata.KeyState' --output text")"
  echo "    Exports CMK : ${EXPORTS_KEY_ARN}"
  echo "    Current state : ${EXPORTS_KEY_STATE:-<unknown>}"
  echo "    Mode          : ${KEY_DELETION_MODE}"
  echo ""

  case "$KEY_DELETION_MODE" in
    skip)
      echo "    KEY_DELETION_MODE=skip — bank retains custody indefinitely."
      echo "    Verification command for the auditor:"
      echo "      aws kms describe-key --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}'"
      ;;
    schedule)
      echo "    Print-only (default). To fire schedule-key-deletion, the bank"
      echo "    IT operator runs the command below (auditable, single call):"
      echo ""
      echo "      aws kms schedule-key-deletion \\"
      echo "        --key-id '${EXPORTS_KEY_ARN}' \\"
      echo "        --pending-window-in-days ${KEY_PENDING_WINDOW_DAYS} \\"
      echo "        --region '${REGION}'"
      echo ""
      echo "    Then capture proof:"
      echo "      aws kms describe-key --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}' \\"
      echo "        --query KeyMetadata --output json | \\"
      echo "        tee evidence/ftr-b7-key-deletion-${BANK_ID}-\$(date -u +%Y%m%d).json"
      echo ""
      echo "    Rollback inside the pending window:"
      echo "      aws kms cancel-key-deletion --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}' && \\"
      echo "      aws kms enable-key --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}'"
      ;;
    execute)
      if [[ "$DRY_RUN" == "true" ]]; then
        echo "    KEY_DELETION_MODE=execute requires LIVE mode (CONFIRM=DELETE-${BANK_ID})."
        echo "    Refusing to fire in DRY-RUN."
      elif [[ "$EXPORTS_KEY_STATE" == "PendingDeletion" ]]; then
        echo "    Key is already in PendingDeletion — nothing to do. Re-capturing proof."
        run_or_print "aws kms describe-key --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}' --query KeyMetadata --output json"
      else
        run_or_print "aws kms schedule-key-deletion --key-id '${EXPORTS_KEY_ARN}' --pending-window-in-days ${KEY_PENDING_WINDOW_DAYS} --region '${REGION}'"
        run_or_print "aws kms tag-resource --key-id '${EXPORTS_KEY_ARN}' \
          --tags 'TagKey=DeprovisionTicket,TagValue=${DEPROVISION_TICKET_ID}' 'TagKey=BankId,TagValue=${BANK_ID}' \
          --region '${REGION}'"
        run_or_print "aws kms describe-key --key-id '${EXPORTS_KEY_ARN}' --region '${REGION}' --query KeyMetadata --output json"
      fi
      ;;
    *)
      echo "    Unknown KEY_DELETION_MODE='${KEY_DELETION_MODE}' (must be schedule|execute|skip)."
      exit 4
      ;;
  esac
fi

echo ""
echo "    Note: the RDS + Redis CMKs (alias/fortress-rds-${BANK_ID} and"
echo "    alias/fortress-redis-${BANK_ID}) were scheduled for deletion"
echo "    automatically when the data stack was deleted in step 7. Their"
echo "    PendingDeletion proof is the CFN DELETE_COMPLETE event for"
echo "    ${STACK_DATA} — see docs/runbooks/p2-key-deletion.md §8."

# ── [10/10] Summary ────────────────────────────────────────────────────────────
cat <<EOF

══════════════════════════════════════════════════════════════════════════════
  Deprovision $([[ "$DRY_RUN" == "true" ]] && echo "DRY-RUN" || echo "COMPLETE") for ${BANK_ID}
══════════════════════════════════════════════════════════════════════════════

  Resources removed in bank account:
    * ECS cluster + service (${CLUSTER} / ${SERVICE_API})
    * RDS instance ${DB_ID} (final snapshot ${SNAPSHOT_ID:-<none>} retained ${FINAL_SNAPSHOT_RETAIN_DAYS}d)
    * CloudFront distribution ${CF_DIST_ID:-<none>}
    * WAFv2 ACL ${WAF_ACL_NAME}
    * Dashboard S3 bucket contents (bucket via CFN dashboard stack)
    * Cognito user pool ${USER_POOL_ID:-<none>} (via CFN dashboard stack)
    * CFN stacks: billing, dashboard, exports-worker, compute, data, vpc

  Resources INTENTIONALLY left in bank account:
    * Exports S3 bucket ${EXPORTS_BUCKET}     (Object-Lock COMPLIANCE 1825d)
    * Exports-bucket CFN stack                (depends on the bucket above)
    * Exports KMS CMK ${EXPORTS_KEY_ALIAS}    ($([[ "$KEY_DELETION_MODE" == "execute" && "$DRY_RUN" == "false" ]] && echo "scheduled for deletion — ${KEY_PENDING_WINDOW_DAYS}d window" || echo "retained — see docs/runbooks/p2-key-deletion.md"))
    * Bank-owned CloudTrail                   (bank SOC retention)
    * Final RDS snapshot ${SNAPSHOT_ID:-<none>}

  Next steps for the bank IT team:
    * Verify final export bundle in s3://${EXPORTS_BUCKET}/_deprovisioning/${DEPROVISION_TICKET_ID}/
    * Schedule exports CMK deletion when retention requirements are met
      (docs/runbooks/p2-key-deletion.md §4 — re-run this script with
      KEY_DELETION_MODE=execute, or run schedule-key-deletion directly)
    * Acknowledge Fortress cross-account revoke (step 8 actions complete)
    * Close deprovisioning ticket ${DEPROVISION_TICKET_ID}

EOF
