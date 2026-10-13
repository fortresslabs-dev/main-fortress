# Cognito MFA — Bank Dashboard Pool

Covers FTR §13.1 B2. The bank dashboard Cognito user pool (one per bank,
defined in `infra/cloudformation/pipeline2-dashboard.yml`) enforces TOTP-based
MFA via a staged rollout (Option C in the planning notes).

**Why this runbook exists:** MFA enforcement is a one-way change in practice
(users have already enrolled, support load builds up around recovery). This
document is the operating manual for rollout, support, and recovery so the
on-call rotation doesn't reinvent it under pressure.

---

## Decision: Option C (staged OPTIONAL → ON)

| Phase | `MfaConfiguration` | `EnabledMfas` | Behaviour |
|---|---|---|---|
| **A — enrolment window** | `OPTIONAL` | `[SOFTWARE_TOKEN_MFA]` | All users prompted to enrol TOTP on next sign-in; can skip and sign in without MFA. |
| **B — enforcement** | `ON` | `[SOFTWARE_TOKEN_MFA]` | Users without TOTP enrolled are blocked at sign-in; must enrol before they can proceed. |

**Why not Option A (ON from day 1):** any user not enrolled at the moment of
flip is locked out and needs an admin reset before they can sign in. Option C
gives existing users a grace window to self-enrol without help-desk traffic.

**Why not Option B (per-group Lambda):** Cognito's `MfaConfiguration` is
pool-wide. "Enforce on CCO/CRO/CIO only" requires a Pre-Token-Generation
Lambda — strictly more complex than C with no FTR-side benefit. Analyst /
Admin tiers also benefit from TOTP; no reason to exempt them.

**SMS is intentionally not in `EnabledMfas`.** SIM-swap is the dominant
real-world account-takeover vector for MFA; TOTP closes it. The
`scripts/verify_ftr_invariants.py` CI gate rejects any PR that adds
`SMS_MFA` or sets `MfaConfiguration` to `OFF`.

---

## Grace window

Default: **7 days** from Phase A deploy to Phase B flip. FTR doc says 30,
which is the maximum we'd use for a real bank with many users. Testbank has
≤2 users so 7 is enough.

Per-bank: decide at the time of deploy based on user count. Record the chosen
window in the bank's onboarding ticket.

---

## Phase A — deploy `OPTIONAL`

1. CFN template change is already committed (see `pipeline2-dashboard.yml`,
   `MfaConfiguration: OPTIONAL` + `EnabledMfas: [SOFTWARE_TOKEN_MFA]`).
2. Deploy the dashboard stack for the target bank:
   ```bash
   aws cloudformation update-stack \
     --stack-name fortress-p2-dashboard-${BANK_ID} \
     --use-previous-template \
     --capabilities CAPABILITY_IAM \
     --parameters ParameterKey=BankId,UsePreviousValue=true \
                  ParameterKey=Environment,UsePreviousValue=true \
                  ParameterKey=BankDomain,UsePreviousValue=true \
                  ParameterKey=TrialMode,UsePreviousValue=true
   ```
   (Use whatever parameters the bank's stack actually carries — list them
   first with `describe-stacks --query 'Stacks[0].Parameters'`.)

   This is a pure Modify on the existing `CognitoUserPool` resource — no
   Replacement, no group/client/domain churn, no user data loss.

3. Verify:
   ```bash
   aws cognito-idp describe-user-pool \
     --user-pool-id "$(aws cloudformation describe-stacks \
       --stack-name fortress-p2-dashboard-${BANK_ID} \
       --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
       --output text)" \
     --query 'UserPool.{MfaConfiguration:MfaConfiguration,EnabledMfas:EnabledMfas}'
   ```
   Expected: `MfaConfiguration: OPTIONAL`, `EnabledMfas: ["SOFTWARE_TOKEN_MFA"]`.

4. Notify all bank users via the channel the bank uses for security comms.
   Subject suggestion: *"Action required: enable TOTP MFA on the Fortress
   dashboard within N days."* Include the end-user enrolment steps (below).

5. Track enrolment daily — see "Verify enrolment" below. Hold the Phase B
   flip until the count plateaus or the grace window expires.

---

## End-user enrolment (user-facing)

The Amplify `<Authenticator>` component in the dashboard surfaces the TOTP
setup flow natively once the pool has TOTP enabled. Steps the user sees:

1. Sign in with email + password as normal.
2. The Authenticator shows a **"Set up TOTP"** screen with a QR code.
3. User scans the QR with their TOTP app (Authy, 1Password, Google
   Authenticator, etc.) and enters the 6-digit code to confirm.
4. Done — next sign-in will prompt for the TOTP code after password.

**During Phase A only**, the Authenticator allows the user to skip the setup
screen and sign in without MFA. After Phase B (enforcement), the setup
screen becomes mandatory before reaching the dashboard.

---

## Verify enrolment

To check who has enrolled before the Phase B flip:

```bash
POOL_ID=us-east-1_XXXXXXXXX
for u in $(aws cognito-idp list-users --user-pool-id "$POOL_ID" \
            --query 'Users[].Username' --output text); do
  pref=$(aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" \
    --username "$u" \
    --query 'UserMFASettingList' --output text)
  printf "%-40s  %s\n" "$u" "${pref:-NONE}"
done
```

Output: `SOFTWARE_TOKEN_MFA` per user means enrolled. `NONE` (empty) means
not yet enrolled — chase those users before flipping to `ON`.

---

## Phase B — flip to `ON`

When the grace window expires AND the enrolment cohort is acceptable:

1. Open a PR that changes `MfaConfiguration: OPTIONAL` → `MfaConfiguration: ON`
   in `infra/cloudformation/pipeline2-dashboard.yml`. The CI invariant gate
   will pass (both `OPTIONAL` and `ON` are accepted).
2. After merge, deploy with the same `update-stack` invocation as Phase A.
3. Verify with `describe-user-pool` — expect `MfaConfiguration: ON`.
4. After both banks (or all banks once we go multi-tenant) are on `ON`,
   consider tightening the CI invariant to reject `OPTIONAL` — only allow
   `ON`. (Edit `scripts/verify_ftr_invariants.py` `check_p2_dashboard_mfa`.)

---

## Recovery — user lost their device

Lost-device recovery requires an admin reset. Both the bank's IT and
Fortress on-call are authorised. Co-ordinate — the user only needs one
reset, not two.

### Admin reset path (CLI)

```bash
# Identify the user
USER_EMAIL=user@bank.example.com
POOL_ID=us-east-1_XXXXXXXXX
USER_SUB=$(aws cognito-idp list-users --user-pool-id "$POOL_ID" \
  --filter "email=\"$USER_EMAIL\"" --query 'Users[0].Username' --output text)

# Clear all MFA preferences (admin-set-user-mfa-preference, both flags false)
aws cognito-idp admin-set-user-mfa-preference \
  --user-pool-id "$POOL_ID" \
  --username "$USER_SUB" \
  --software-token-mfa-settings Enabled=false,PreferredMfa=false \
  --sms-mfa-settings Enabled=false,PreferredMfa=false
```

After reset, the user signs in normally; the Authenticator surfaces the
TOTP setup flow again.

### Identity verification before reset

**Mandatory** before running the reset commands. The reset bypasses MFA, so
treat it as a high-trust operation:

1. Confirm the request came through the bank's internal ticketing system
   (not via email or unsolicited chat).
2. Verify the requester's identity via a side-channel known to the bank
   (their corporate phone, a manager confirming, etc.).
3. Log the reset in the bank's incident-tracking system. Include: who
   requested, who reset, when, why.

### Who can reset

| Reset path | Who | Auth |
|---|---|---|
| Bank's IT runs the CLI in the bank's AWS account | Bank-side admin role with `cognito-idp:AdminSetUserMFAPreference` on the user pool | The bank's normal IAM access |
| Fortress on-call runs the CLI in the bank's AWS account | Cross-account role assumed from Fortress (existing support path) | MFA-protected role assumption, ticket required |

Both paths log to CloudTrail in the bank's account.

---

## Rollback

If the Phase A deploy breaks sign-in for users (unlikely — the change is
purely additive when going OFF → OPTIONAL — but possible if an Amplify
mismatch surfaces):

1. Open an emergency PR setting `MfaConfiguration` back to `OFF`. The CI
   invariant will FAIL — comment `[FTR-OVERRIDE: rollback B2]` in the PR
   body to flag the deliberate override and force-merge with two reviewers.
2. Deploy. Existing TOTP enrolments are preserved at the Cognito side
   (Amplify just stops prompting).
3. File a post-mortem before re-attempting Phase A.

**Do not roll back from Phase B (`ON`) to `OFF`.** That's a security
regression visible to FTR auditors. If `ON` breaks sign-in for a specific
user, run the admin reset for that user (above), not a pool-wide rollback.

---

## CI gate

`scripts/verify_ftr_invariants.py` asserts on every PR + push to main:

- `pipeline2-dashboard.yml` `CognitoUserPool.MfaConfiguration ∈ {OPTIONAL, ON}`
- `pipeline2-dashboard.yml` `CognitoUserPool.EnabledMfas == [SOFTWARE_TOKEN_MFA]`

A regression fails CI; the failure message points back at this runbook.
