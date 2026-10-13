# Inspector v2 — CVE Triage Runbook

> Owner: Security on-call. Used after `infra/scripts/enable-ecr-enhanced-scanning.sh`
> turns on ECR Enhanced Scanning. Covers the first-scan backlog and the
> ongoing weekly triage cadence.

## When findings arrive

- **First scan after enable** — every existing ECR image is scanned.
  Expect dozens to hundreds of findings per image (mostly base-image
  CVEs). This is normal; do *not* page anyone.
- **New push** — Inspector scans on push. CRITICAL/HIGH on the
  scraper or P2 API images is the only PagerDuty-worthy case.
- **Continuous re-scan** — CONTINUOUS_SCAN mode re-evaluates whenever
  Inspector ingests a new CVE upstream. New findings on already-deployed
  images show up here.

## Severity SLAs

| Severity | Action | SLA |
|---|---|---|
| CRITICAL | Patch or compensating control | 7 days |
| HIGH | Patch or accept-with-justification | 30 days |
| MEDIUM | Track; bundle into next base-image bump | next quarterly bump |
| LOW / INFORMATIONAL | Suppress unless exploitable in our context | n/a |

## Where to look

- **Console:** AWS Inspector → Findings, filtered by `Image registry`
  for ECR scope. Use the `Vulnerability ID` column to dedupe across images.
- **Security Hub:** all findings flow here via FSBP. Useful for cross-
  account aggregation if we federate accounts later.
- **SNS:** Findings DO NOT alert via SNS by default — Inspector pushes
  to EventBridge, not SNS. If we want SNS-on-CRITICAL, add an
  EventBridge rule on `aws.inspector2` filtered to `severity: [CRITICAL]`
  → `SecurityAlertsTopic` (out of scope for #16; tracked separately).

## Triage steps

1. **List unique CVE IDs across all ECR images** — Inspector console,
   group by Vulnerability ID. Don't triage per-image; same CVE on N
   images = one decision.
2. **For each CVE, decide one of three outcomes:**
   - **Patch** — bump the base image in `Dockerfile.pipeline{1,2}`, rebuild,
     push, redeploy. Inspector auto-rescans on push.
   - **Accept with justification** — open an Inspector finding
     suppression rule (`AWS::InspectorV2::Filter`) with a JSON expression
     scoping to the CVE ID, and a comment explaining why (e.g.
     "CVE only triggers via local socket, no exposure on Fargate").
     Filter resource lifecycle in CFN.
   - **Compensating control** — note in finding comment; track until next
     base-image bump.
3. **Document** — every CRITICAL or HIGH closed without a patch goes in
   `docs/ftr_evidence/03_security_tools/inspector_suppressions.md` with
   CVE, image, justification, date, signer.

## Cadence

- **Weekly:** review new CRITICAL + HIGH findings (Mon 09:00 ET).
- **Monthly:** review MEDIUM aggregate.
- **Quarterly:** bump base images even if nothing CRITICAL is open
  (preempts known-CVE accrual).

## When to escalate

- Any CRITICAL on the **production-deployed** P1 scraper or P2 API image
  that exists in a release tag pulled by an ECS service task definition.
- Any HIGH unresolved past 30 days.
- Suppression count > 25 per image (drift signal — base image is stale).

## Related

- Enable script: `infra/scripts/enable-ecr-enhanced-scanning.sh`
- Tracker entry: `docs/ftr_tracker.md` Phase 3 #16 (S4/V1)
- Adjacent FTR control: §13.1 V1 (vulnerability management)
