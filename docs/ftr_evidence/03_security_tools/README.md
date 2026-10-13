# 03 — Security tools evidence

Screenshots and JSON exports proving the security controls in FTR §2–§7 are **live**, not just templated.

## What goes here

| Artifact | Source | Refresh cadence |
|---|---|---|
| `guardduty_enabled_{YYYY-MM-DD}.png` | GuardDuty console → Settings → Detectors | Quarterly |
| `guardduty_runtime_monitoring_{YYYY-MM-DD}.png` | GuardDuty → Runtime monitoring (Fargate agent, RDS login) | Quarterly |
| `securityhub_standards_{YYYY-MM-DD}.png` | Security Hub → Security standards (PCI DSS + AWS FSBP enabled) | Quarterly |
| `securityhub_findings_summary_{YYYY-MM-DD}.png` | Security Hub → Summary, with HIGH+ count visible | Quarterly |
| `inspector_enhanced_scan_{YYYY-MM-DD}.png` | Inspector → ECR scan results (closes V1) | On enable + quarterly |
| `access_analyzer_findings_{YYYY-MM-DD}.png` | IAM Access Analyzer (closes S3/L5) | On enable + quarterly |
| `waf_p1_rules_{YYYY-MM-DD}.png` | WAFv2 console → P1 web ACL | On rule change |
| `waf_p2_rules_{YYYY-MM-DD}.png` | WAFv2 console → P2 web ACL (per bank) | On rule change |
| `kms_key_rotation_{YYYY-MM-DD}.json` | `aws kms describe-key --key-id …` for each CMK, `KeyRotationStatus` field visible | Annually |
| `kms_bank_key_policy_{YYYY-MM-DD}.json` | `aws kms get-key-policy` showing **no Fortress principal** in bank-side CMK | Per onboarding |
| `cloudtrail_log_validation_{YYYY-MM-DD}.png` | CloudTrail → trail detail, `LogFileValidationEnabled: true` | Annually |
| `vpc_flow_logs_enabled_{YYYY-MM-DD}.png` | VPC console → Flow Logs tab (closes B1) | On enable |
| `elb_access_logs_{YYYY-MM-DD}.png` | ALB attributes showing `access_logs.s3.enabled = true` (closes S2/L3) | On enable |
| `secrets_rotation_status_{YYYY-MM-DD}.json` | `aws secretsmanager describe-secret` per secret — auto-rotation field state | After D2 runbook signoff |

## Redaction rules

Before commit: blur or remove **AWS account IDs, bank EIPs, internal email addresses, ARNs containing bank slugs**. Replace with `<REDACTED-ACCOUNT>`, `<BANK_EIP>`, etc. in JSON exports.

## Status (2026-06-26)

⬜ Not started. Capture once Phase-2 / Phase-3 items in `docs/ftr_tracker.md` land — there's no point screenshotting Flow Logs disabled.
