# Developer AWS Onboarding

How an admin provisions AWS access for a new developer, and how that developer
configures the `fortress-local` profile. Pair this with
[`local-dev.md`](local-dev.md) (running the stack) and
[`operations.md`](operations.md) (IDE/CLI + deploy).

> **TL;DR for the admin:** decide the developer's access *tier* (below). Most
> developers only need **Tier 0** (no AWS) or **Tier 1** (Bedrock only). Full
> SSM/Secrets access (Tier 2) is only for pulling real synced config.

---

## 0 · First decide the access tier

The app has two run modes, toggled by `SECRETS_SOURCE` in `.env` (see
`.env.example` lines 7–14, 26–27). That split maps directly onto how much AWS a
developer actually needs:

| Tier | What they do | Set | AWS access required |
|---|---|---|---|
| **0 — Offline** | Run P1/P2/dashboard locally with stubbed LLM | `LLM_MOCK_MODE=true` | **None** |
| **0b — Real LLM, no AWS** | Real test-gen **and** extraction via the **direct Claude API** | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (`make p2-live`) | **None** — needs an Anthropic API key, not AWS |
| **1 — Local + Bedrock LLM** | Real LLM via Bedrock (prod parity) | `LLM_PROVIDER=bedrock`, `SECRETS_SOURCE=` empty | **Bedrock invoke only** |
| **2 — Local + prod config** | Pull synced reference data / secrets from AWS | `SECRETS_SOURCE=aws` | Bedrock **+** SSM Parameter Store **+** Secrets Manager read |

**Start every developer at the lowest tier that unblocks them.** A new hire can
be productive on day one at Tier 0 with no AWS footprint at all —
`LLM_MOCK_MODE=true` returns deterministic stubs for every LLM call, and
docker-compose supplies Postgres + Redis.

**Most developers never need AWS.** Tier **0b** gives *real* Claude output for
test generation using only an **Anthropic API key** (console.anthropic.com),
no AWS at all — `BedrockService` calls the direct Claude API when
`LLM_PROVIDER=anthropic`. See `docs/setup/local-dev.md` § *Real test generation
via the direct Claude API*. Both LLM call sites — `BedrockService`
(generation) and `BedrockExtractor` (extraction) — honor the switch. Grant
Tier 1/2 (AWS) only when the work needs genuinely Bedrock-only features:
**Titan embeddings / vector search** (no Claude-API equivalent) or real synced
prod config.

---

## 1 · Admin: provision the developer in IAM Identity Center (SSO)

> This assumes the org uses **IAM Identity Center** (the `aws configure sso`
> flow). If you instead hand out IAM access keys, skip to
> [§4 — IAM access-key alternative](#4--iam-access-key-alternative).

### 1a. Create the user
IAM Identity Center → **Users** → *Add user* (their work email). They receive an
activation email to set a password and register MFA.

### 1b. Create / reuse a permission set
IAM Identity Center → **Permission sets**. Reuse the developer permission set you
already use, or create a dedicated `FortressDeveloper` one. Scope its inline
policy to the tier you granted:

**Tier 1 (Bedrock only)** — the minimum for live LLM work:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "Bedrock", "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "*" }
  ]
}
```

**Tier 2 (add prod config pull)** — append these statements:
```json
    { "Sid": "ReadConfig", "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath",
                 "secretsmanager:GetSecretValue"],
      "Resource": ["arn:aws:ssm:us-east-1:520385696621:parameter/fortress/testbank/*",
                   "arn:aws:secretsmanager:us-east-1:520385696621:secret:/fortress/testbank/*"] },
    { "Sid": "S3Data", "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::fortress-p1-data-520385696621-production",
                   "arn:aws:s3:::fortress-p1-data-520385696621-production/*"] }
```
`CLIENT_NAME=testbank` is the only populated SSM namespace (`.env.example:22`),
so the SSM/Secrets scope is `/fortress/testbank/*`.

> Bedrock models are the `us.anthropic.*` **inference-profile** IDs in
> `us-east-1` (`.env.example:58–59`). A permission set without `bedrock:InvokeModel`
> is the documented cause of local test-gen failing even when the profile
> otherwise works.

### 1c. Assign user → account → permission set
IAM Identity Center → **AWS accounts** → select account **`520385696621`** →
*Assign users* → pick the new user → pick the permission set.

> **Confirm the account.** `520385696621` is the Fortress account per
> `docs/runbooks/trial-vs-client.md:15`. If your org uses a separate dev/sandbox
> account for local work, assign that one instead and tell the developer to use
> its ID.

---

## 2 · Admin: hand the developer three values

As admin you can read all three straight out of Identity Center:

| Value | Where to find it |
|---|---|
| **SSO start URL** | IAM Identity Center → **Dashboard** → "AWS access portal URL" (`https://<org>.awsapps.com/start`) |
| **AWS account ID** | `520385696621` (the account assigned in §1c) |
| **Permission-set name** | the one assigned in §1c (e.g. `FortressDeveloper`) |
| **SSO region** | your Identity Center region (typically `us-east-1`) |

These are intentionally **not** committed to the repo — SSO config is
per-developer. The profile *name* (`fortress-local`) is the only committed part.

---

## 3 · Developer: configure the `fortress-local` profile

The profile **must** be named `fortress-local` — that is the committed default
in `.env.example:24` (`AWS_PROFILE=fortress-local`) and what the code reads via
`AWS_PROFILE`. A missing/misnamed profile produces the error documented in
`docs/design/6-agent-test-generation-pipeline.md:130`:
`config profile (fortress-local) could not be found`.

```bash
aws configure sso --profile fortress-local
#   SSO start URL:        <portal URL from admin>
#   SSO region:           us-east-1
#   Account:              520385696621          # or your dev account
#   Permission set/role:  FortressDeveloper     # name from admin
#   CLI default region:   us-east-1
#   CLI output format:    json

# each working session:
aws sso login --profile fortress-local
export AWS_PROFILE=fortress-local
export AWS_REGION=us-east-1

# verify:
aws sts get-caller-identity                         # prints the assumed role
aws bedrock list-foundation-models --region us-east-1 | head   # Tier 1+: confirms Bedrock
```

Then set up the app itself per [`local-dev.md`](local-dev.md):
```bash
cp .env.example .env            # keep SECRETS_SOURCE empty for Tier 0/1
make up                         # Postgres + Redis
python -m alembic upgrade head
make p1                         # :8001
make p2                         # :8002
```
- **Tier 0:** leave `LLM_MOCK_MODE=true` (the `make p1`/`make p2` default) — no
  AWS creds are touched.
- **Tier 1/2:** set `LLM_MOCK_MODE=false` so Bedrock is actually invoked through
  the `fortress-local` profile.
- **Tier 2 only:** set `SECRETS_SOURCE=aws` to pull config from SSM/Secrets
  Manager instead of `.env`.

---

## 4 · IAM access-key alternative

If the org does **not** use Identity Center and instead issues long-lived keys:

1. IAM → **Users** → create user → attach the same tier policy from §1b.
2. Create an access key for the user (Security credentials → *Create access key*).
3. Developer adds a `fortress-local` profile to `~/.aws/credentials`:
   ```ini
   [fortress-local]
   aws_access_key_id = AKIA...
   aws_secret_access_key = ...
   region = us-east-1
   ```
4. `export AWS_PROFILE=fortress-local` and verify with `aws sts get-caller-identity`.

> SSO (§1–3) is preferred — short-lived credentials, central revocation, MFA.
> Prefer access keys only where Identity Center isn't available.

---

## 5 · Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `config profile (fortress-local) could not be found` | No `~/.aws` profile by that name | Run §3 `aws configure sso` |
| `ExpiredToken` / `token has expired` | SSO session lapsed | `aws sso login --profile fortress-local` |
| `AccessDenied` on `bedrock:InvokeModel` | Permission set lacks Bedrock | Add the Tier 1 statement (§1b) |
| Bedrock "model not found" / inference-profile error | Bare model ID used | Use `us.anthropic.*` IDs; region `us-east-1` |
| SSM/Secrets `AccessDenied` at startup (`SECRETS_SOURCE=aws`) | Tier 1 user hit Tier 2 config | Grant Tier 2 statements, or keep `SECRETS_SOURCE=` empty + `LLM_MOCK_MODE=true` |
| Want to work with no AWS at all | — | Tier 0: `SECRETS_SOURCE=` empty, `LLM_MOCK_MODE=true` |
