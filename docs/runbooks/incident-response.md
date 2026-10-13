# Incident Response Runbook (v1.0)

> **Status:** v1.0 — closes FTR §13.1 `B9` (IR + paging). v0.1 (2026-06-26) was a partial close covering scope, severity, RACI, lifecycle, customer notification, and PIR template. v1.0 adds the on-call rotation (§3.1), paging chain (§4.1), acknowledge procedure (§4.2), and paging-test procedure (§11) — closing the gap that previously required an external paging SaaS.
>
> **Paging is AWS-only.** Alarms route via CloudWatch → SNS → SMS to primary + backup on-call (parallel) + email copy. No external SaaS, no AWS Marketplace charges — all costs land on the AWS bill where credits apply. The "louder" rescue path (15-min unack → re-page) is deferred to v1.1 (§9).
>
> **Authoritative copy:** this file. Any printed or PDF copy is for reference only.
>
> **Last reviewed:** 2026-06-30 · **Next review:** 2026-09-30 (quarterly).

---

## 1. Scope

This runbook covers security and availability incidents affecting:

- **Pipeline 1 (Fortress Cloud account)** — multi-tenant SaaS control plane. Fortress is fully responsible.
- **Pipeline 2 (bank account)** — single-tenant per-bank deployment. **Joint** with bank IT — bank owns the AWS account, Fortress owns the application code.
- **Marketplace / portal** — Fortress is fully responsible (FTR §11).

It does NOT cover bank-side incidents that do not involve Fortress code or infrastructure (those go through the bank's own IR process).

## 2. Severity definitions

| Severity | Definition | Initial response | Customer notification |
|---|---|---|---|
| **SEV-1** | Confirmed data breach OR P1 sync API down >15min OR any P2 dashboard down >30min OR active exploitation observed | 15 min, all-hands page | **24h SLA** per §6 |
| **SEV-2** | Suspected unauthorized access OR degraded sync for any bank >1h OR HRI control failure (GuardDuty High finding, KMS policy drift) | 30 min, on-call | 24h SLA if customer-impacting |
| **SEV-3** | Single-bank issue, workaround exists; or low-confidence security finding under triage | Business hours | If customer asks |
| **SEV-4** | Internal-only (CI/CD failure, staging) | Best-effort | None |

## 3. RACI

| Role | SEV-1 | SEV-2 | SEV-3 |
|---|---|---|---|
| **Incident Commander (IC)** | CTO (or designated deputy) | On-call engineer | On-call engineer |
| **Comms Lead** | CTO | CTO | IC |
| **Tech Lead** | On-call senior eng | On-call engineer | On-call engineer |
| **Customer Liaison** (per bank) | CTO or Account Owner | Account Owner | Account Owner |
| **Legal / DPO** | Notified at declaration | Notified within 4h | Not required unless escalates |
| **Bank IT (P2 incidents)** | Notified within 1h | Notified within 4h | Loop in if pattern emerges |

Only the IC may **declare** or **resolve** an incident. The IC may delegate any of the above roles in writing in the incident channel.

### 3.1 On-call rotation

| Role | Holder | Phone (E.164) | Coverage | Ack SLA |
|---|---|---|---|---|
| **Primary on-call** | _to be assigned_ | populated via `PrimaryPagingPhoneE164` CFN parameter (never in repo) | 24/7 until rotation expanded | 15 min for SEV-1; 30 min for SEV-2 |
| **Backup on-call** | _to be assigned_ | populated via `BackupPagingPhoneE164` CFN parameter | 24/7 fallback (paged in parallel) | Best-effort; takes over if primary unreachable |

Until the team grows to 4+ engineers, both holders are paged **in parallel** on every alarm. There is no time-based escalation — see §4.2 for the ack procedure and §9 for the planned v1.1 rescue Lambda.

**Rotation changes:** update the corresponding CFN parameter on the `fortress-p1-security-${Environment}` stack, then re-run §11.3 to verify the new number receives a test SMS. Document the change in the IR channel and the FTR tracker.

**Rotation expansion plan:** when headcount allows, this section will be replaced by a weekly rotation schedule (Mon 00:00 UTC handoff) backed by a DynamoDB-table-driven Lambda that resolves the current `Primary` and `Backup` phone numbers at SNS-subscription update time.

## 4. Detection inputs

| Source | Routing | Owner |
|---|---|---|
| GuardDuty High/Critical | EventBridge → SNS `fortress-p1-security-alerts-production` → §4.1 paging chain | Agent 3 |
| Security Hub critical findings | SNS → §4.1 paging chain | Agent 3 |
| CloudWatch alarms (`fortress-*`) | SNS → §4.1 paging chain | Agent 3 |
| IAM policy mutations (CIS 4.4) | EventBridge `IamPolicyChangeRule` → SNS → §4.1 | Agent 3 |
| Access Analyzer ACTIVE findings | EventBridge `AccessAnalyzerFindingRule` → SNS → §4.1 | Agent 3 |
| Customer-reported via `security@fortresslabs.dev` | Inbox monitored 24/7; 24h initial-ack SLA | Comms Lead |
| Customer-reported via Slack Connect with bank | Bank's primary contact pings Account Owner; treated as SEV-2+ until proven otherwise | Account Owner |
| `/.well-known/security.txt` reporter | Same inbox + acknowledgements page | Comms Lead |
| Internal observation (eng sees anomaly) | `#incidents` Slack channel; declare incident before investigating | Discoverer |

### 4.1 Paging chain (AWS-only)

```
┌──────────────────────────┐
│  Alarm source            │   GuardDuty / Security Hub / CW alarm /
│  (CloudWatch / Event-    │   IamPolicyChangeRule / AccessAnalyzer
│   Bridge rule)           │   FindingRule
└────────────┬─────────────┘
             │  publish
             ▼
┌──────────────────────────┐
│  SNS topic               │   fortress-p1-security-alerts-${Environment}
│                          │   (defined in pipeline1-security.yml)
└────────────┬─────────────┘
             │  fan-out
   ┌─────────┼──────────┬──────────────┐
   ▼         ▼          ▼              ▼
 email     SMS        SMS         (future v1.1)
 (audit)   primary    backup      EventBridge 15-min
           paging     paging      ALARM-still-open re-page
           wakes the  wakes the
           on-call    fallback
           up         in parallel
```

All three subscriptions live in `infra/cloudformation/pipeline1-security.yml`:

- `SecurityAlertsTopicSubscription` (Protocol: `email`, parameter `AlertEmail`)
- `SecurityAlertsTopicPrimaryPagingSubscription` (Protocol: `sms`, parameter `PrimaryPagingPhoneE164`)
- `SecurityAlertsTopicBackupPagingSubscription` (Protocol: `sms`, parameter `BackupPagingPhoneE164`)

CI invariant `scripts/verify_ftr_invariants.py` enforces that all three resources stay in the template (gates the FTR §13.1 B9 control against silent regression).

**Cost:** SNS-SMS to US numbers is ~$0.0075/SMS, billed via the AWS bill (first-party AWS service, not Marketplace) — AWS promo credits apply.

### 4.2 Acknowledge procedure

There is no machine-enforced ack in v1.0 (deferred to v1.1 — see §9). The manual procedure is:

1. Page arrives via SMS to both on-call phones in parallel.
2. **Whoever picks up first** posts `ACK INC-{YYYYMMDD-{hh}{mm}}` in `#incidents` Slack within the §3.1 SLA (15 min SEV-1, 30 min SEV-2). The timestamp comes from the SMS body or the alarm's `StateChangeTime`.
3. The other on-call sees the ACK in Slack and stands down.
4. If neither responds within the SLA, **the alarm remains in `ALARM` state** — every state transition fires a fresh SMS, so any flapping alarm naturally re-pages. For non-flapping alarms there is no second page in v1.0 — this is the gap §9's rescue Lambda will close.

The ACK message in Slack is the **authoritative** acknowledgement. Voicemail / "I saw it" replies in DM do not count; the audit trail lives in the `#incidents` channel.

## 5. Incident lifecycle

```
Detect → Triage → Declare → Investigate → Contain → Eradicate → Recover → Post-incident review
```

### 5.1 Declare

1. First responder receives SNS-SMS page (or `#incidents` ping outside of automated detection).
2. ACK in `#incidents` per §4.2.
3. IC assigned within 15 min (SEV-1) / 30 min (SEV-2). For SEV-1 outside business hours, the on-call who acks becomes IC by default until a senior is available.
4. IC opens a dated incident channel `#inc-YYYYMMDD-{slug}` and pins this runbook + severity.
5. IC notifies Legal/DPO if SEV-1 (privileged communications from this point).

### 5.2 Investigate (parallel, IC-coordinated)

- **Tech Lead** preserves evidence: snapshot affected RDS/Aurora; export CloudWatch Logs window; freeze ECS task definitions; capture VPC Flow Logs (B1).
- **Tech Lead** quarantines: revoke compromised credentials, rotate affected secrets (per `docs/runbooks/secrets-rotation.md`), update WAF IPSet if attack source identified.
- **Comms Lead** drafts customer notification (template §6) — does NOT send without IC sign-off.
- **Customer Liaison** notifies affected banks per §6 SLA.

### 5.3 Contain → Eradicate → Recover

Document every action in the incident channel with `>` timestamps. Pattern:

```
> 14:32 UTC — IC declared SEV-2 (suspected credential leak — bank `firstnational` API key)
> 14:35 UTC — Tech Lead revoked HMAC key via scripts/rotate-bank-credentials.sh
> 14:36 UTC — Tech Lead verified P1 sync API now 401s old key — pcap attached
> 14:45 UTC — Customer Liaison emailed firstnational primary + secondary contact
> 14:51 UTC — New HMAC key issued; bank confirmed re-deployment of P2 sync config
```

### 5.4 Resolve

Only the IC closes the incident. Resolution criteria:
- Root cause identified (or explicitly time-boxed for later investigation).
- Containment verified in production.
- Affected customers acknowledged.
- Post-incident review scheduled within 5 business days.

## 6. Customer notification — 24h SLA

For any SEV-1 or SEV-2 incident involving customer data, customer environment, or customer-perceptible outage:

- **Initial notification within 24h of declaration**, even if facts are still emerging.
- Use the template below; do not improvise.

```
Subject: [Fortress Labs Security Notice] — {YYYY-MM-DD} — {severity}

Dear {Primary contact},

We are writing to inform you that Fortress Labs is responding to a
security/availability incident that {may affect | affects | does not affect}
your Pipeline 2 environment.

Summary
-------
- What happened: {1-2 sentences, factual, no speculation}
- When detected: {timestamp UTC}
- Current status: {Active investigation | Contained | Resolved}
- Customer impact: {None observed | {specific impact}}

What we have done
-----------------
{Bullet list of containment actions}

What you should do
------------------
{Bullet list of any customer-side actions — usually "no action required"}

Next update
-----------
We will provide our next update by {timestamp UTC, ≤24h out}.

Questions: reply to this email or contact your Account Owner directly.
For urgent matters: security@fortresslabs.dev / on-call line.

— Fortress Labs Security
```

**Notification cadence after initial:** every 24h until resolution, then a final report within 10 business days containing root cause, remediation, and prevention plan.

## 7. Coordination with bank IT (P2-side incidents)

Joint runbook responsibilities (already in FTR §10.3):

| Signal | Bank | Fortress |
|---|---|---|
| Dashboard outage (CCO can't log in) | Detect, declare on bank side | Notify within 1h; investigate app layer |
| Application exception (Bedrock error, validator failure) | Notify Fortress | Fortress declares + investigates |
| Suspicious activity in CloudTrail | Bank SOC reviews; bank initiates | Fortress provides app-side context within 4h of request |
| RDS/Aurora outage | Joint declaration | Joint investigation |
| WAF block storm | Joint detection | Joint investigation; bank owns IPSet edits |

Bank gets read-only forensic data (app logs windowed to incident scope) within **4h** of a written request during an active incident.

## 8. Post-incident review (PIR)

Scheduled within 5 business days. Template:

```markdown
# PIR — INC-{YYYYMMDD-slug}

## Summary
{2-3 sentences, plain language}

## Timeline (UTC)
{From detection to resolution, bullet list}

## Root cause
{Single sentence + technical detail}

## Customer impact
{Quantified: which banks, how long, what they saw}

## What went well
{2-3 bullets}

## What went poorly
{2-3 bullets — blameless framing; describe systems, not people}

## Action items
| # | Action | Owner | Due | Tracker ID |
|---|---|---|---|---|
| 1 | ... | ... | ... | (link to GH issue / FTR tracker if applicable) |
```

Action items get tracked in `docs/ftr_tracker.md` if they affect FTR posture.

## 9. Known gaps + planned v1.1

- **No machine-enforced ack** — see §4.2. Mitigated by parallel paging to two phones + Slack ack convention. Closing this requires:
  - **v1.1 rescue Lambda** — EventBridge schedule (every 15 min) that scans CloudWatch alarms still in `ALARM` state with no Slack ACK matching `INC-…`; re-publishes to `SecurityAlertsTopic` to wake the on-call again. Estimated <50 lines + a single Lambda + a small DynamoDB ack table.
- **SMS sandbox + US 10DLC delivery gap** (verified 2026-06-30 during the initial paging-test deploy). AWS End User Messaging (formerly Pinpoint SMS) ships every new account at `ACCOUNT_TIER: SANDBOX`. Sandbox restricts delivery to verified destination phone numbers. Even then, in 2026 US carriers (AT&T, Verizon, T-Mobile) aggressively filter all A2P traffic without registered **10DLC** origination identity — the SNS API returns success, the carrier silently drops the SMS before the device. Live-tested 2026-06-30 against `+1714…6907`: AWS published successfully (MessageId `371fe540-3f82-5cf1-bb04-87fd595d9206`), email subscription delivered, SMS never reached the device, follow-up sandbox verification OTP also dropped at the carrier. Email subscription is therefore the **only proven paging endpoint** in v1.0; SMS subscription exists and is wired correctly but is operationally undeliverable to US numbers until the operational follow-on below is closed.
  Two graduation paths, both operational (no code change):
  1. **Email-to-SMS carrier gateway** as a tactical bridge — add a second `email` subscription using the on-call's carrier SMS gateway address (e.g. `+1NXXNXXXXXX@vtext.com` for Verizon, `@txt.att.net` for AT&T, `@tmomail.net` for T-Mobile). AWS sees email; carrier converts to SMS at no AWS cost. Works inside sandbox.
  2. **10DLC registration + production-tier graduation** — register a 10DLC origination identity in AWS End User Messaging, submit a use-case ticket to graduate from sandbox to production. ~$50 one-time + ~$10/mo + per-message fees. Required before paging any bank's on-call directly. Plan to complete before the first real-bank cutover.
- **Voice paging** — not in v1.0. SNS supports SMS but not voice. If voice becomes needed, AWS End User Messaging Voice (formerly Pinpoint Voice) is the AWS-billed path; credits apply.
- **Self-service on-call contact onboarding** — phone numbers and email addresses are currently injected via CFN parameters at stack-update time (operator path). Planned future enhancement: a custom onboarding page in the bank dashboard (or Fortress operator UI) that lets the user add / edit their primary + backup paging contacts, with the form submission writing through to the `PrimaryPagingPhoneE164` / `BackupPagingPhoneE164` parameters via a CFN update or a parameter-store-backed indirection. Defers the operator dependency for routine rotation changes.
- **Tabletop exercise (IR3)** — target 60 days post-v1.0 (i.e. by 2026-08-29).
- **Annual runbook review** every September.
- **Encryption / PGP key** for `security@fortresslabs.dev` (referenced in `security.txt`).

## 10. Sign-off

| Role | Name | Date |
|---|---|---|
| CTO | _pending_ | _pending_ |
| Eng lead | _pending_ | _pending_ |

Sign-off captured here is the source of truth for FTR §13.1 B9. The CI invariant in `scripts/verify_ftr_invariants.py` prevents the paging chain from regressing between sign-off and the next review.

## 11. Paging-test procedure (for FTR evidence pack)

Run after every CFN parameter change to phone numbers, and at least quarterly as part of the runbook review. All output goes into the FTR evidence drive under `evidence/ftr-b9-paging-test-YYYYMMDD/`.

### 11.1 Capture the deployed subscription set

```bash
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-security-production \
  --query "Stacks[0].Outputs[?OutputKey=='SecurityAlertsTopicArn'].OutputValue" \
  --output text)

aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
  --query "Subscriptions[].[Protocol,Endpoint,SubscriptionArn]" \
  --output table | tee "evidence/ftr-b9-paging-test-$(date -u +%Y%m%d)/subscriptions.txt"
```

Expected: at least 1 `email` row (confirmed, not `PendingConfirmation`) and 2 `sms` rows. SMS subscriptions show as `Confirmed` immediately (no opt-in click required).

### 11.2 Capture the CI invariant evidence

```bash
python3 scripts/verify_ftr_invariants.py | \
  tee "evidence/ftr-b9-paging-test-$(date -u +%Y%m%d)/invariants.txt"
```

Expected: the line `pipeline1-security.yml SecurityAlertsTopic carries primary + backup SMS paging subscriptions` is present.

### 11.3 Live SMS delivery test

```bash
aws sns publish \
  --topic-arn "$TOPIC_ARN" \
  --subject "[FTR-B9 paging test] $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --message "FTR §13.1 B9 paging-test ping. No action required. Reply ACK in #incidents to acknowledge receipt." \
  --output json | tee "evidence/ftr-b9-paging-test-$(date -u +%Y%m%d)/publish-result.json"
```

Expected: both phones receive an SMS within ~60s; email subscriber receives the same content. Primary on-call replies `ACK FTR-B9-TEST-{date}` in `#incidents`. Screenshot the SMS receipt on at least one phone and save it next to the JSON output.

### 11.4 Capture SNS delivery-status logs (optional, recommended)

If `SnsDeliveryStatusLogging` is enabled on the topic (CloudWatch Logs), pull the SMS delivery event for the test publish:

```bash
aws logs filter-log-events \
  --log-group-name "sns/us-east-1/$(aws sts get-caller-identity --query Account --output text)/DirectPublishToPhoneNumber" \
  --start-time "$(($(date +%s)*1000 - 600000))" \
  --filter-pattern "\"FTR-B9\"" \
  --output json | tee "evidence/ftr-b9-paging-test-$(date -u +%Y%m%d)/delivery-status.json"
```

`status: SUCCESS` per phone number is the definitive proof of delivery.
