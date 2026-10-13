# Authentication & Identity Security Baseline

Scope: the two auth surfaces in this repo.

| Pipeline | What it authenticates | Mechanism | Store |
|---|---|---|---|
| **P1** (Fortress server, Aurora) | Bank backends calling `/api/sync/*` to pull rules/embeddings | HMAC-style API key + WAF IP allowlist | `client_credentials` table — SHA-256 hash only |
| **P2** (Per-bank dashboard, Aurora per bank) | Human users (CCO / CRO / CIO / Analyst / Admin) | Cognito user pool per bank → JWT bearer | Cognito (passwords hashed inside Cognito); no `users` table in our DB |

This document is the single source of truth for what we follow and what is still missing. Update it whenever an item moves.

---

## 1. Reference standards we map to

- **NIST SP 800-63B** — digital identity (authenticator strength, password rules, MFA).
- **FFIEC Authentication Guidance (2021)** — risk-based + layered controls for financial institutions; MFA expected for high-risk transactions.
- **GLBA Safeguards Rule (16 CFR Part 314)** — protect customer info; MFA, access controls, encryption, monitoring, written program.
- **SOC 2 (CC6.x)** — logical access controls, MFA, privileged access, monitoring.
- **PCI DSS 4.0 §8** — only if/when we touch card PANs; mostly NOT in scope today, but write code as if it could be.

Every control below cites the framework section it covers, so vendor-risk reviewers can map fast.

---

## 2. Pipeline 1 — Fortress server, machine-to-machine

### What's in place
- 64-char hex API keys, generated with `secrets.token_hex(32)` (`src/services/client_credential_service.py:48`).
- Only SHA-256 hash stored; raw key shown **once** at provisioning (`client_credential_service.py:7`, `:68`).
- Constant-time validation prevents timing attacks (`client_credential_service.py:80` comment).
- Two-factor request shape: `X-Customer-Id` + `X-Api-Key`, both required (`src/api/sync_auth.py:44`).
- 401 on any failure, error reason never leaked (`sync_auth.py:42`).
- WAF IP allowlist per bank NAT EIP (out-of-band, `sync_auth.py:7-8` comment).
- TLS 1.2+ at ALB.

### Baseline guidelines (P1)
1. **Never log raw keys.** Audit `logger.info` calls in provisioning paths; `raw_key` must not appear in any structured field.
2. **One key per `(customer_id, environment)`.** Never let a prod key validate against staging or vice versa.
3. **Rotation.** Default lifetime 365 days. Force rotation on:
   - Suspected compromise (immediate revoke + new key)
   - Bank personnel changes affecting the key holder
   - Offboarding / contract end
4. **No key in source, env defaults, or task definition.** Bank stores its key in *their* secrets manager and injects per request.
5. **Audit every sync request** to CloudWatch with `customer_id`, source IP, endpoint, status, response time. Retain 7 years.
6. **WAF rule must list NAT EIPs explicitly** — no `0.0.0.0/0` even temporarily. Use `infra/scripts/register-bank-ip.sh`.

### Gaps (P1) — open work
| # | Gap | Impact | Priority | Action |
|---|---|---|---|---|
| P1-1 | No automated rotation reminder; no expiry on `client_credentials` rows | Stale keys live forever | Med | Add `expires_at` column + nightly Lambda that flags rows < 30d to expiry; alarm on CW |
| P1-2 | No list/get/update endpoints for keys (memory `project_internal_admin_endpoints_stubs`) | Ops blind to inventory | Med | Implement `list_clients` / `get_client` / `update_status` in `ClientCredentialService` |
| P1-3 | `revoked_at` / revocation flow not surfaced in `validate()` | Revoked keys may still pass | High | Add `is_active AND revoked_at IS NULL` to validation query |
| P1-4 | No rate limit on `/api/sync/*` | Stolen key could exfiltrate full corpus | High | API Gateway throttling per `customer_id`; WAF rate-based rule as backstop |
| P1-5 | No anomaly detection (geo-velocity, off-hours, volume spike) | Stolen key looks identical to legit until exfil complete | Med | CW Anomaly Detection alarm on per-customer request rate + GuardDuty findings → SNS |

---

## 3. Pipeline 2 — Bank dashboard, human auth

### What's in place
- One Cognito user pool per bank: `fortress-dashboard-${BankId}` (`infra/cloudformation/pipeline2-dashboard.yml:110-114`).
- `UsernameAttributes: [email]` (`pipeline2-dashboard.yml:114`), `AutoVerifiedAttributes: [email]`.
- `PasswordPolicy.MinimumLength: 12` (`pipeline2-dashboard.yml:118`).
- `AllowAdminCreateUserOnly: true` — no self-signup (`pipeline2-dashboard.yml:124`).
- 5 groups: CCO, CRO, CIO, Analyst, Admin → role enum at request time (`src/api/auth.py:90-124`).
- JWT verified with RS256, `iss`, `aud`, `exp` checked (`src/api/auth.py:74-83`).
- Per-bank tenancy claim `custom:bank_id` carried in JWT (`src/api/auth.py:152`).
- No `users` table; no password material in app DB.

### Baseline guidelines (P2)
1. **MFA required** for all roles handling money or governance (CCO, CRO, Admin); offered + prompted for the rest.
2. **Federate to bank IdP** (SAML or OIDC) the moment we onboard a real bank. Cognito is just a broker.
3. **No shared accounts.** Each human gets one user; "demo" patterns like the `TempPass!2026` reset across 5 accounts are testbank-only and must never appear in a prod pool.
4. **Tokens:** access ≤ 60 min, refresh ≤ 30 d, revocation on, `token_use=access` enforced in `_decode_cognito_token`.
5. **Audit every authentication event** (success, failure, MFA challenge, password change, group change) → CloudWatch → SIEM → 7y retention.
6. **Privileged role isolation.** Admin (vector intel + threshold tuning) and CCO actions logged with before/after diff + actor + timestamp; immutable audit table.
7. **No plaintext credentials anywhere off-Cognito.** Not in tickets, not in Slack, not in `bank.env.<bank-id>` files, not in screenshots.
8. **Break-glass admin** lives in a corporate vault (1Password Business / Vault), MFA required, all uses audited and reviewed weekly.

### Gaps (P2) — open work
| # | Gap | Impact | Priority | Action |
|---|---|---|---|---|
| P2-1 | `_DEV_TOKEN` fallback (`src/api/auth.py:31, 137-141`) grants CCO on any Bearer match — defaults to `changeme-token` | Critical bypass if env var ships unchanged | **Critical** | Gate behind `ENV in ("local","dev")`; refuse if `COGNITO_USER_POOL_ID` is set |
| P2-2 | `MfaConfiguration` not set on UserPool — MFA effectively off | Phished password = full account takeover | **Critical** | CFN: `MfaConfiguration: ON`, `EnabledMfas: [SOFTWARE_TOKEN_MFA]` (add `WEBAUTHN` next) |
| P2-3 | `token_use` claim not verified in `_decode_cognito_token` | ID token could be swapped for access token | High | Add `claims["token_use"] == "access"` check after `aud`/`iss` |
| P2-4 | No external IdP federation configured | Banks won't accept "users live in your Cognito" | High (per-bank, on onboard) | Add `CognitoIdentityProvider` resource per bank with their SAML/OIDC metadata |
| P2-5 | Cognito Advanced Security Features not enabled (`UserPoolAddOns.AdvancedSecurityMode`) | No compromised-credential check, no adaptive auth, no risk events | High | Set `AdvancedSecurityMode: ENFORCED` in CFN; wire risk events to CW |
| P2-6 | Password policy missing complexity requirements (only `MinimumLength: 12`) | Weak-but-long passwords accepted | Med | Add `RequireUppercase/Lowercase/Numbers/Symbols: true` |
| P2-7 | No token revocation flag (`EnableTokenRevocation`) on client | Lost laptop = active refresh until expiry | Med | Set on `CognitoUserPoolClient`; expose admin "revoke all tokens" action |
| P2-8 | No idle session timeout in dashboard SPA | CCO walks away from desk = open governance console | Med | 15-min idle → re-auth in SPA; backend enforces short access-token TTL |
| P2-9 | No row-level enforcement of `bank_id` outside auth layer | Mis-routed query = cross-bank data leak | High | Add `bank_id = :user_bank_id` predicate to every multi-tenant query; add CI lint |
| P2-10 | Cognito user/auth events not shipped off-CloudWatch | No SIEM correlation, no long-term retention | Med | EventBridge rule → Firehose → S3 (7y, KMS) + optional SIEM feed |
| P2-11 | No documented break-glass admin flow | Recovery = chaos under pressure | Med | Vault-stored credential, weekly review, runbook |
| P2-12 | `PIPELINE2_API_TOKEN` env default `"changeme-token"` (`src/api/auth.py:31`) survives into built images if unset | Same root cause as P2-1 — defense in depth | High | Remove default; raise at startup if missing in non-local env |

---

## 4. Aurora & data-layer baseline (both pipelines)

| Control | Status | Action if missing |
|---|---|---|
| Storage encryption at rest (KMS) | Verify in `infra/cloudformation/pipeline*-data.yml` | `StorageEncrypted: true` + `KmsKeyId` |
| TLS in transit to Aurora | Required | `sslmode=require` in connection string; reject non-TLS at SG |
| Per-bank DB / per-bank schema isolation | P2 — one Aurora per bank | Confirm naming per `register-bank.sh` |
| Secrets in Secrets Manager / SSM SecureString only | Mixed — check P2 SSM coverage | Memory `project_p1_secret_rotation_gap`: P1 rotation disabled; reopen with manual cadence until Aurora-compatible rotator lands |
| Backup retention ≥ 35 days + PITR | Verify | `BackupRetentionPeriod: 35`, `EnableCloudwatchLogsExports: [postgresql]` |
| Aurora audit logging (pgaudit) | Likely off | Enable for `compliance_rules`, `client_credentials`, audit tables; ship to CW |
| Row-level multi-tenant filter | App-side only today | See gap P2-9 |

---

## 5. Logging, audit & monitoring (both pipelines)

Must capture, at minimum, for **7-year retention** (GLBA/SOX baseline):

- Every successful and failed authentication (P1 sync + P2 dashboard).
- Every privileged action: rule edit, threshold change, sign-off, override, key provision/rotate/revoke, user role change.
- Every cross-tenant access attempt (auto-deny + alarm).
- Every Cognito user-pool admin event (group change, password reset, MFA disable).

Pipeline:
- App → structured `structlog` JSON → CloudWatch.
- CloudWatch → EventBridge filter → Firehose → S3 (KMS-encrypted, Object Lock compliance mode, 7y).
- Optional: SIEM feed (bank's choice — Splunk, Sentinel, Chronicle).

Alarms (per memory `feedback_cw_alarm_dimension_split`, emit dimensioned AND dimensionless heartbeats):
- Auth failure spike (per `customer_id` and global).
- New IP for an existing customer key.
- Cognito risk event = High.
- Threshold change outside business hours.
- Any access to `client_credentials` table outside `ClientCredentialService`.

---

## 6. What we explicitly do NOT do

These are common anti-patterns; documenting so reviewers don't have to ask:

- We do **not** store dashboard user passwords (or hashes of them) in our DB. Cognito is the only password store.
- We do **not** store raw P1 API keys; SHA-256 hash only.
- We do **not** support password retrieval — only reset.
- We do **not** allow self-signup on any pool.
- We do **not** mirror Cognito user data to a local `users` table beyond Cognito `sub` + cached display fields (and even that is not implemented yet — currently no `users` table at all).
- We do **not** use the dev-token fallback in production (see gap P2-1; this must hold).

---

## 7. Prioritized punch list (next 30 days)

Critical (do this week):
- [ ] **P2-1 + P2-12**: kill dev-token bypass; raise on missing env in non-local. One PR, ~30 LoC in `src/api/auth.py`.
- [ ] **P2-2**: enable MFA on per-bank user pools in `pipeline2-dashboard.yml`. CFN change, redeploy per bank.

High (do this sprint):
- [ ] **P2-3**: enforce `token_use=access`.
- [ ] **P2-5**: enable Cognito Advanced Security Features.
- [ ] **P2-9**: row-level `bank_id` predicate on multi-tenant queries + CI lint.
- [ ] **P1-3**: validate `revoked_at IS NULL` in `client_credential_service.validate()`.
- [ ] **P1-4**: API Gateway throttling per customer.

Medium (this quarter):
- [ ] **P2-4**: SAML/OIDC IdP federation flow + runbook for bank onboarding.
- [ ] **P2-6, P2-7, P2-8, P2-10, P2-11**.
- [ ] **P1-1, P1-2, P1-5**.

---

## 8. Onboarding checklist (per new bank)

When standing up a real bank (not testbank), every item below must be green before "go live":

- [ ] P2 Cognito pool created with MFA ON + Advanced Security ENFORCED.
- [ ] External IdP federation configured against bank's SSO; password auth disabled.
- [ ] First admin user created via bank's IT, not via shared credential.
- [ ] WAF IP allowlist updated with bank NAT EIPs.
- [ ] P1 API key provisioned, delivered via secure channel (bank's secrets manager direct, not email/Slack).
- [ ] Audit pipeline confirmed shipping to bank's SIEM (if requested) and to our S3 audit bucket.
- [ ] Break-glass account vaulted; bank countersigns runbook.
- [ ] Vendor-risk packet sent (this doc + SOC 2 mappings).

---

## 9. Change log

- 2026-06-07 — initial draft. Pipelines mapped, gaps inventoried against current `src/api/auth.py`, `src/api/sync_auth.py`, and `infra/cloudformation/pipeline2-dashboard.yml`.
