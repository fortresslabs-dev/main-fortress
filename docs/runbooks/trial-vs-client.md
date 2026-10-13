# Trial vs Real-Client Deployment — Pipeline 2

How `deploy-p2.sh` behaves differently for an internal trial (testbank in our
own AWS account) vs onboarding a real bank in their own AWS account.

> Companion to `dashboard_integration_p2_action.md` (the per-step runbook)
> and `infra/scripts/deploy-p2.sh` (the one-command driver).

---

## TL;DR — the two commands

**Trial (single account, no real domain, no Security stack):**
```bash
export FORTRESS_ACCOUNT_ID=520385696621
TRIAL_MODE=true \
NON_INTERACTIVE=true \
WAF_AUTO=true \
MIGRATIONS_AUTO=true \
  ./infra/scripts/deploy-p2.sh testbank production
```

**Real client (cross-account, real domain, full prod stack):**
```bash
# 1. Fortress account — provision P1 API key + WAF allowlist
AWS_PROFILE=fortress-prod \
FORTRESS_INTERNAL_SECRET=… \
  ./scripts/register-bank.sh \
    --bank-id firstnational \
    --bank-name "First National Bank" \
    --bank-ip <NAT-EIP-from-step-2-VPC> \
    --bank-profile bank-firstnational

# 2. Bank account — full P2 deploy
export FORTRESS_ACCOUNT_ID=520385696621
export BANK_DOMAIN=firstnationalbank.com
export ALERT_EMAIL=security@firstnationalbank.com
export CLOUDTRAIL_BUCKET=firstnational-cloudtrail
export COGNITO_CCO_EMAIL=cco@firstnationalbank.com
export COGNITO_CRO_EMAIL=cro@firstnationalbank.com
export COGNITO_CIO_EMAIL=cio@firstnationalbank.com

AWS_PROFILE=bank-firstnational \
NON_INTERACTIVE=true \
MIGRATIONS_AUTO=true \
  ./infra/scripts/deploy-p2.sh firstnational production
```

---

## What changes, knob by knob

| Concern | Trial (testbank) | Real client (firstnational) |
|---|---|---|
| **AWS accounts** | One account; `AWS_PROFILE=default` | Two — `AWS_PROFILE=bank-<id>` for deploy, `fortress-prod` for cross-account ops |
| **`TRIAL_MODE`** | `true` — no ACM, no DNS aliases, served via `*.cloudfront.net` | `false` — real ACM cert + bank-owned CNAME on ALB |
| **Bank env file** | `infra/setup/bank.env.testbank` (placeholders OK for some) | `infra/setup/bank.env.<id>` with real JIRA + GitHub creds |
| **`BANK_DOMAIN`** | placeholder `testbank.local` (RFC-unroutable; script auto-sets) | real domain, e.g. `firstnationalbank.com` |
| **`ALERT_EMAIL`** | optional fake | bank's security team address |
| **CloudFront fronting the API** | yes (default `*.cloudfront.net` cert) | no — ALB serves HTTPS directly via ACM on `fortressapi.${BANK_DOMAIN}` |
| **DNS CNAMEs** | none needed | `fortressapi.${BANK_DOMAIN} → ALB` and `dashboard.${BANK_DOMAIN} → CF` (script prints both at the end) |
| **`SKIP_SECURITY`** | defaults `true` (auto in trial mode) | `false` — Security stack required; needs `CLOUDTRAIL_BUCKET` env var (bank pre-creates the S3 bucket) |
| **`WAF_AUTO`** | `true` — same account, script runs `register-bank-ip.sh` inline | not yet supported cross-account; run `register-bank.sh` from Fortress profile beforehand (it also adds the IP to WAF). See gap #1 below |
| **P1 API key** | placeholder pre-set by data.yml; we leave it (no real sync) | `register-bank.sh` populates `fortress/<bank>/pipeline1-api-key` in the bank's Secrets Manager; deploy-p2.sh sees a non-placeholder value and skips the prompt |
| **`MIGRATIONS_AUTO`** | `true` — alembic via ECS RunTask on the compute taskdef | same — works identically |
| **Cognito users** | 5 users default to `{cco,cro,cio,analyst,admin}@testbank.local` | override via `COGNITO_{CCO,CRO,CIO,ANALYST,ADMIN}_EMAIL` env vars, or add to bank env file |
| **`TEMP_PASSWORD`** | default `Fortress2026!` | override per-bank if security policy requires |
| **JIRA webhook auth** | Bearer token, set up after deploy via Automation rule | identical — `JIRA_AUTH_MODE=bearer` is the default |

---

## All script env knobs (cheat-sheet)

| Knob | Default | Purpose |
|---|---|---|
| `TRIAL_MODE` | `false` | Skip ACM + DNS aliases; default Security skip |
| `NON_INTERACTIVE` | `false` | No `read -p` prompts; fail if env vars missing |
| `WAF_AUTO` | `false` | Auto-call `register-bank-ip.sh` after VPC stack |
| `MIGRATIONS_AUTO` | `false` | Run `alembic upgrade head` via ECS RunTask |
| `SKIP_SECURITY` | `=TRIAL_MODE` | Skip the Security stack (GuardDuty/SecHub/CloudTrail) |
| `SKIP_POSTDEPLOY` | `false` | Stop after Security stack — no sync, no Cognito, no SPA |
| `ENV_FILE` | `infra/setup/bank.env.<bank-id>` | Override credential env file path |
| `IMAGE_TAG` | `latest` | Docker image tag |
| `TEMP_PASSWORD` | `Fortress2026!` | Cognito user temporary password |
| `PIPELINE1_API_KEY` | unset | Provide P1 API key non-interactively |
| `BANK_DOMAIN` | (prompt or `testbank.local` in trial) | Bank's public domain |
| `ALERT_EMAIL` | (prompt) | CloudWatch alarm subscriber |
| `VPC_CIDR` | `10.2.0.0/16` | P2 VPC CIDR (must not overlap P1) |
| `CLOUDTRAIL_BUCKET` | (prompt) | Required when Security stack is deployed |
| `COGNITO_CCO_EMAIL`     | `cco@${BANK_DOMAIN}`     | CCO dashboard user email (sign-off, executive view) |
| `COGNITO_CRO_EMAIL`     | `cro@${BANK_DOMAIN}`     | CRO dashboard user email (approver + vector intel read) |
| `COGNITO_CIO_EMAIL`     | `cio@${BANK_DOMAIN}`     | CIO dashboard user email (tech read-only) |
| `COGNITO_ANALYST_EMAIL` | `analyst@${BANK_DOMAIN}` | Frontline analyst — SDN search, case decisions |
| `COGNITO_ADMIN_EMAIL`   | `admin@${BANK_DOMAIN}`   | Model governance — vector intel, threshold tuning |
| `FORTRESS_ACCOUNT_ID` | (required) | Fortress AWS account id (sanity-check the deploy context) |

---

## Known gaps before first real-client deploy

These are intentional — they keep the trial flow lean, but you'll want to
close them before signing the first bank.

1. **`WAF_AUTO` is single-account today.** The script calls
   `register-bank-ip.sh` with whatever `AWS_PROFILE` is active, which for a
   real bank deploy will be `bank-<id>` and won't have permission on the
   Fortress P1 WAF. Two fixes:
   - Add a `FORTRESS_PROFILE` env var that the script uses just for that one
     call (~10 lines).
   - Or: keep `WAF_AUTO=false` for real clients and use the
     `register-bank.sh` flow from the Fortress account instead (it also
     handles the WAF allowlist, so this duplicates nicely).

2. **`register-bank.sh` is a separate prerequisite.** It must run from the
   Fortress P1 account *after* the bank's VPC exists (so we have the NAT
   EIP) but *before* `deploy-p2.sh` reaches Step 3 (load credentials). The
   neatest workflow today is to split the deploy in two passes:
   - First pass: `SKIP_POSTDEPLOY=true` to stop after VPC; capture the
     NAT EIP from the stack output.
   - Run `register-bank.sh` from Fortress account.
   - Second pass: full `deploy-p2.sh` (skips already-deployed stacks).

3. **Cross-account alembic.** `MIGRATIONS_AUTO=true` always runs in the bank
   account (uses the compute taskdef), so this works identically for both
   trial and real-client. No change needed.

4. **Dashboard SPA build needs Node.js locally** on the machine running
   `deploy-p2.sh`. For automated CI we'd containerise this; for the manual
   onboarding flow it's fine.

---

## What the script does, step-by-step (current)

```
Step 1   VPC                                — idempotent
Step 2   Data stack (Aurora, secrets)       — idempotent
Step 3   Load credentials                   — calls load-bank-credentials.sh
         P1 API key                         — skips if already set
Step 5   Build + push image to ECR
Step 6   Compute (ECS, ALB, [CF in trial])
Step 7   Dashboard (S3, CF, Cognito)
Step 7.5 Alembic                            — ECS RunTask, uses compute taskdef
Step 8   Security                           — skipped in trial
Step 9   Wait for /health
Step 10  Trigger first P1→P2 sync
Step 11  Create 3 Cognito users
Step 12  Build + upload dashboard SPA
```

Pauses (skipped when `NON_INTERACTIVE=true`):
- After VPC — wait for P1 WAF allowlist update (auto with `WAF_AUTO=true`)
- After Data — wait for P1 API key (auto if `PIPELINE1_API_KEY` env set or
  the secret already holds a non-placeholder value)
- The "wait for migrations" pause is gone — migrations run inline via
  `MIGRATIONS_AUTO=true`, or are skipped entirely (with a warning) in
  `NON_INTERACTIVE=true` when `MIGRATIONS_AUTO=false`.

---

## Reference

- Driver script: `infra/scripts/deploy-p2.sh`
- Credential loader: `scripts/load-bank-credentials.sh`
- WAF helper (Fortress account): `infra/scripts/register-bank-ip.sh`
- P1 key provisioner (Fortress account): `scripts/register-bank.sh`
- Dashboard SPA upload: `dashboard/deploy.sh`
- Step-by-step manual runbook: `dashboard_integration_p2_action.md`
- CFN templates: `infra/cloudformation/pipeline2-*.yml`
