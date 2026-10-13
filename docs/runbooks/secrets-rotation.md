# Secrets Manager — Manual Rotation Runbook

> **Status:** Compensating control for FTR tracker `B4` / FTR §4.6 `D1` — AWS Secrets Manager **auto-rotation is disabled** on Pipeline 1 since 2026-05-13 after the AWS-managed `PostgreSQLSingleUser` rotator broke Aurora (per memory `project_p1_secret_rotation_gap`). Until B4 is closed with a safe rotation Lambda, **this runbook is the rotation control** AWS reviewers will inspect.
>
> **Closes FTR tracker:** `D2`.
> **Last reviewed:** 2026-06-26 · **Cadence:** quarterly rotation, or immediately on suspected compromise.

---

## 1. Why manual

The AWS-managed `SecretsManagerRDSPostgreSQLRotationSingleUser` Lambda issued an `ALTER USER ... PASSWORD` that conflicted with Aurora Serverless v2's connection-pinning behaviour; the Lambda exited successfully but the cluster's primary connection retained the OLD credential, and every ECS task subsequently issued the NEW credential and got `28P01` errors. CloudFormation rotation block is commented out in `infra/cloudformation/pipeline1-data.yml` pending a safer custom rotator.

Manual rotation in the meantime is acceptable for FTR provided:

1. There is a documented runbook (this file).
2. Rotation actually happens on cadence (audit trail — §6).
3. There is a defined trigger for emergency rotation (§5).
4. Compensating monitoring detects credential misuse (Security Hub + GuardDuty are already in place).

## 2. Secret inventory

### 2.1 Pipeline 1 (Fortress account, `us-east-1`)

| Secret name | Type | Consumer | Rotation owner | Cadence |
|---|---|---|---|---|
| `fortress/p1/db-password` | Aurora master password | P1 ECS tasks, alembic | Eng on-call | Quarterly |
| `fortress/p1/redis-auth-token` | Redis AUTH token | P1 ECS tasks | Eng on-call | Quarterly |
| `fortress/p1/sync-api-jwt-secret` | JWT signing secret for sync API | P1 sync API | Eng on-call | Quarterly |
| `fortress/p1/hmac-keys/{bank-id}` | Per-bank HMAC key (one per bank) | Bank's P2 UpdateService | Eng on-call | **On bank-side request OR suspected compromise** |
| `fortress/p1/openai-api-key` (if present) | Bedrock fallback | scraper extraction | Eng on-call | Annually |

### 2.2 Pipeline 2 (per-bank account, `us-east-1`)

Per the FTR §1.3 shared-responsibility table, **secrets in the bank account are bank-owned (KMS CMK bank-owned)**. Fortress publishes the runbook below; bank IT executes against their own account.

| Secret name | Type | Consumer | Rotation owner | Cadence |
|---|---|---|---|---|
| `fortress/p2/{bank}/db-password` | RDS master password | P2 ECS tasks, alembic | Bank IT | Per bank policy (typically 90d) |
| `fortress/p2/{bank}/redis-auth-token` | Redis AUTH token | P2 ECS tasks | Bank IT | Per bank policy |
| `fortress/p2/{bank}/cognito-client-secret` | Cognito app client secret | P2 dashboard backend | Bank IT | Per bank policy |
| `fortress/p2/{bank}/jira-token` | JIRA API token | P2 webhook handler | Bank IT (bank-owned token) | Per bank's JIRA admin policy |
| `fortress/p2/{bank}/github-token` | GitHub PR-creation token | P2 generation step | Bank IT (bank-owned token) | Per bank's GitHub admin policy |
| `fortress/p2/{bank}/hmac-key` | Mirror of P1's per-bank HMAC | P2 UpdateService → P1 sync | Bank IT (rotated when Fortress rotates P1 side) | On bank-side request |
| `fortress/p2/{bank}/app-config` | Aggregate app config (encoded) | P2 ECS tasks | Bank IT | On config change |
| `fortress/p2/{bank}/exports-signing-key` | Signs export PDF metadata | exports-worker | Bank IT | Annually |

## 3. Rotation procedure — Aurora / RDS password (P1 example)

The pattern below applies to `fortress/p1/db-password` and to bank-side equivalents. **Execute during a low-traffic window** (P1: weekend; P2: per bank's maintenance window).

### 3.1 Pre-flight (≤30 min before rotation)

```bash
# Confirm we know the current secret version + active task count
aws secretsmanager describe-secret --secret-id fortress/p1/db-password \
  --query "VersionIdsToStages"

aws ecs describe-services \
  --cluster fortress-pipeline1-production \
  --services fortress-pipeline1-api-production \
  --query "services[0].{Running:runningCount,Desired:desiredCount}"

# Open a tail on Aurora error log
aws logs tail /aws/rds/cluster/fortress-pipeline1-prod/postgresql --follow &
TAIL_PID=$!
```

### 3.2 Generate new password

```bash
NEW_PW=$(aws secretsmanager get-random-password \
  --password-length 40 \
  --exclude-characters '@/"' \
  --require-each-included-type \
  --query RandomPassword --output text)
```

Do NOT echo or log `NEW_PW`. Hold it in shell scope only.

### 3.3 Apply to Aurora FIRST, then to Secrets Manager

The order matters — if Secrets Manager flips first, every new ECS task will fail until Aurora catches up.

```bash
# Step 1: ALTER USER on Aurora using current credential (psql via SSM session OR bastion ECS task)
# This must run from inside the VPC. Use an ECS run-task with a one-shot psql container,
# OR (recommended) a Session Manager session to the bastion.

# Inside the VPC:
PGPASSWORD="<current-pw-from-SM>" psql \
  -h fortress-pipeline1-prod.cluster-xxx.us-east-1.rds.amazonaws.com \
  -U fortress \
  -d fortress \
  -c "ALTER USER fortress WITH PASSWORD '${NEW_PW}';"

# Verify with a fresh connection using NEW pw:
PGPASSWORD="${NEW_PW}" psql -h <endpoint> -U fortress -d fortress -c "SELECT 1;"
# Expect: 1 row returned. If 28P01, ABORT and re-run ALTER with the previously-known password.
```

### 3.4 Update Secrets Manager

```bash
# Build the JSON payload — Secrets Manager stores the credential as JSON for RDS-style secrets
cat > /tmp/new-secret.json <<EOF
{"username":"fortress","password":"${NEW_PW}","engine":"postgres","host":"fortress-pipeline1-prod.cluster-xxx.us-east-1.rds.amazonaws.com","port":5432,"dbname":"fortress"}
EOF

aws secretsmanager put-secret-value \
  --secret-id fortress/p1/db-password \
  --secret-string file:///tmp/new-secret.json

shred -u /tmp/new-secret.json  # do not leave plaintext on disk
```

### 3.5 Force ECS rollover

```bash
aws ecs update-service \
  --cluster fortress-pipeline1-production \
  --service fortress-pipeline1-api-production \
  --force-new-deployment
```

ECS tasks resolve `valueFrom: arn:...:fortress/p1/db-password:...` at task start, so a rolling deploy picks up the new credential. Watch the deploy:

```bash
aws ecs wait services-stable \
  --cluster fortress-pipeline1-production \
  --services fortress-pipeline1-api-production
```

### 3.6 Post-flight

```bash
# Verify no 28P01 in the last 10 min
aws logs filter-log-events \
  --log-group-name /aws/rds/cluster/fortress-pipeline1-prod/postgresql \
  --start-time $(($(date +%s) - 600))000 \
  --filter-pattern "28P01"
# Expect: no events.

# Verify API health
curl -sf https://api.fortresslabs.dev/health

# Stop the tail
kill $TAIL_PID 2>/dev/null || true
```

### 3.7 Document the rotation

Append to `docs/runbooks/secrets-rotation-log.md` (create if missing):

```
| Date (UTC) | Secret | Pipeline | Operator | Trigger | Verified |
|---|---|---|---|---|---|
| 2026-06-26 14:32 | fortress/p1/db-password | P1 | jamier | quarterly | yes |
```

## 4. Rotation procedure — non-RDS secrets

| Secret type | Procedure |
|---|---|
| Redis AUTH token | ElastiCache supports two simultaneous auth tokens during rotation. Use `ModifyReplicationGroup --auth-token <new> --auth-token-update-strategy ROTATE`, then `--auth-token-update-strategy SET` once all clients have rolled over. |
| JWT signing secret | Two-key rotation: introduce new key as `JWT_SIGNING_KEY_NEXT`; deploy; flip primary; retire old. Documented in `src/api/auth.py`. |
| HMAC per-bank | `scripts/rotate-bank-credentials.sh <bank-id>` (script lives in repo). Banks receive new key out-of-band; P2 redeployed with new env. |
| Cognito client secret (P2) | `aws cognito-idp update-user-pool-client --generate-secret`. Backend reads from Secrets Manager — update there too. |
| JIRA / GitHub tokens (P2) | Bank rotates via the upstream service, then updates Secrets Manager value. |

## 5. Emergency rotation triggers

Rotate **immediately**, out of band, on any of:

- GuardDuty finding `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration.InsideAWS` or similar.
- Confirmed leaked credential in code, CI logs, or third-party breach corpus.
- Departure of any human with credential access (within 24h).
- Bank reports a suspected compromise on their side (P2 secrets).
- Active SEV-1 incident where credential abuse is in scope (per `docs/runbooks/incident-response.md`).

Emergency rotation skips the maintenance-window constraint. Log the trigger in §3.7 and pin the entry in the IR channel.

## 6. Audit trail

- Every rotation logged in `docs/runbooks/secrets-rotation-log.md` (PR'd, reviewed by another engineer).
- `CloudTrail` already captures `secretsmanager:PutSecretValue` and `rds-data:ExecuteStatement` API calls.
- Quarterly review by CTO: did each scheduled rotation happen, and on time?

## 7. When B4 is closed, this runbook is the fallback

Once a safe custom rotation Lambda is deployed (B4), auto-rotation handles steady-state. This runbook stays in force for:

- Emergency rotation triggers (§5).
- HMAC keys (no AWS-managed rotator exists for our HMAC scheme).
- P2 secrets in bank accounts where the bank declines to enable auto-rotation.

## 8. Sign-off

| Role | Name | Date |
|---|---|---|
| CTO | _pending_ | _pending_ |
| Eng lead | _pending_ | _pending_ |

Sign-off promotes from v0.1 (draft) to v1.0 and closes FTR tracker D2.
