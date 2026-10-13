# OFAC Screening — Customer UAT Checklist

Purpose: Provide bank-side CCO/CRO/UAT teams a practical, auditable sign-off checklist for OFAC screening controls in Pipeline 2.

Scope: Customer deployment inside VPC using POST /validate/ofac-screen and related workflow evidence.

## 1. Business Outcomes This UAT Must Prove

1. Sanctions risk is reduced because true SDN matches are blocked before payment execution.
2. Manual review load is controlled by strong match quality across aliases and misspellings.
3. Screening controls are auditable with decision reason, timestamp, and supporting evidence.
4. Customer data residency is preserved (screening runs in customer VPC).

## 2. System Behavior to Validate (Current Implementation)

1. Endpoint: POST /validate/ofac-screen
2. Match threshold: 85% similarity (runtime default)
3. Decision values currently returned by OFAC screener:
- block: one or more matches at or above threshold
- clear: no qualifying match
4. Freshness gate:
- If SDN dataset exceeds staleness limit, screening should fail closed with runtime error and alerting path.

Note: Some docs mention review/hold outcomes for borderline matches. Current validator response contract returns block or clear for OFAC. If policy requires explicit review in production, log as a gap and track remediation.

## 3. Entry Criteria (Before UAT Starts)

- [ ] Customer environment deployed in VPC.
- [ ] OFAC vector table populated and embeddings complete.
- [ ] Latest SDN refresh timestamp visible in dashboard/ops telemetry.
- [ ] UAT test identities approved by Compliance and Legal.
- [ ] Runbook owner assigned for incident handling.

## 4. Core UAT Scenarios (Bank Must Execute)

## 4.1 Exact SDN Match -> BLOCK

Objective: Prove high-confidence sanctioned party is blocked.

Input:
- party_name exactly matches known SDN entry.

Expected:
- action == block
- violations has at least one OFAC rule id (OFAC-SDN-*)
- recommendation indicates block/escalation workflow
- screening_timestamp and latency_ms present

Evidence to capture:
- API request/response payload (redacted as required)
- audit event id/log correlation id
- case management ticket or exception record

Pass/Fail:
- Pass only if transaction would be prevented from release in downstream flow.

## 4.2 Alias / Variant Match -> BLOCK

Objective: Prove alias coverage catches non-primary names.

Input:
- party_name using known alias/transliteration variant for SDN entry.

Expected:
- action == block
- violation description indicates variant/alias match context

Evidence:
- matched sdn_number
- alias indicator in rationale (if available)

## 4.3 Clean Name -> CLEAR

Objective: Prove non-sanctioned names do not get blocked.

Input:
- benign synthetic identity with no SDN overlap.

Expected:
- action == clear
- violations is empty

Business threshold:
- false positives during UAT sample remain below bank-approved tolerance.

## 4.4 Borderline Similarity Governance

Objective: Prove policy handling when name is close but not true sanctioned identity.

Input:
- names intentionally similar to SDN entries.

Expected:
- If score >= threshold and true risk: block
- If policy requires manual review for borderline but system returns clear/block only: record gap, define compensating control.

Evidence:
- compliance decision memo for threshold behavior
- approved policy text from CCO

## 4.5 SDN Data Freshness Breach

Objective: Prove stale list protection works and is operationally handled.

Setup:
- Simulate stale SDN dataset beyond policy limit.

Expected:
- screening call fails closed (not silently clear)
- incident/alert is raised
- operations runbook triggered

Pass/Fail:
- Pass only if stale data cannot silently approve transactions.

## 4.6 Throughput / Latency Under Load

Objective: Prove production readiness for payment volumes.

Input:
- representative batch and burst test.

Expected:
- service meets bank SLA latency and error budgets
- no degradation in decision correctness

Evidence:
- p50/p95/p99 latency
- error rate
- queue/backpressure behavior

## 5. Required Controls Around OFAC Decisioning

- [ ] Payment orchestration enforces block outcomes before settlement.
- [ ] Clear outcomes are logged with traceable request id.
- [ ] All decisions are immutable/auditable for examiner review.
- [ ] Incident path defined for stale data or screening outage.
- [ ] Role-based access controls limit overrides.
- [ ] Override actions require dual approval and are fully logged.

## 6. Suggested API Test Vectors

Use POST /validate/ofac-screen with payload:

{
  "party_name": "<test-name>",
  "party_country": "US"
}

Minimum vectors:
- exact_sdn_name
- known_alias_name
- transliterated_name
- typo_close_name
- benign_control_name

For each vector, capture:
- expected_action
- actual_action
- matched_rule_ids
- reviewer decision (if any)

## 7. CCO Sign-Off Template

Bank: ____________________
Environment: ____________________
UAT Window: ____________________

1. Exact match block control validated: [ ] Yes [ ] No
2. Alias/variant block control validated: [ ] Yes [ ] No
3. Clean-name clear control validated: [ ] Yes [ ] No
4. Freshness fail-closed behavior validated: [ ] Yes [ ] No
5. Audit evidence package complete: [ ] Yes [ ] No
6. Policy alignment (threshold/override) approved: [ ] Yes [ ] No

Residual risks / exceptions:
- __________________________________________
- __________________________________________

CCO Name/Signature: ____________________ Date: __________
CRO Name/Signature: ____________________ Date: __________
Fortress Compliance Lead: ____________________ Date: __________

## 8. Go-Live Recommendation Rules

Recommend GO-LIVE only if all critical controls pass:
- exact match block
- alias/variant block
- stale data fail-closed
- auditable evidence completeness

If any critical control fails:
- status = NO-GO
- define remediation owner and due date
- rerun failed UAT scenarios before production approval
