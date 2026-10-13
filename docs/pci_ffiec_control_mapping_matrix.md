# Fortress PCI and FFIEC Control Mapping Matrix

Date: 2026-06-12  
Scope: Pipeline 1 (Fortress) and Pipeline 2 (bank account)

Notes:

- This is a technical mapping matrix for architecture review, not a formal attestation.
- Security Hub enables PCI DSS v3.2.1 and AWS FSBP controls in both pipelines.
- FFIEC mapping is aligned to commonly reviewed domains (governance, security, resilience, monitoring, third-party risk) and should be validated with bank compliance teams.

## 1. PCI Control Mapping

| Control ID | Requirement Summary | Current Implementation | Evidence Location | Status | Gap and Action |
|---|---|---|---|---|---|
| PCI 1.x | Network security controls and segmentation | VPC segmentation, private subnets, SG-restricted DB/cache access, ALB front door, WAF on ALB | infra/cloudformation/pipeline1-vpc.yml, infra/cloudformation/pipeline2-vpc.yml, infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml | Partial | Add formal network data flow diagrams approved by security architecture board |
| PCI 2.x | Secure configurations and hardening | IaC-based resource baselines, controlled ingress/egress, managed service defaults | infra/cloudformation/*.yml | Partial | Add CIS benchmark evidence collection and immutable baseline checks in CI/CD |
| PCI 3.x | Protect stored account/cardholder data | KMS encryption for RDS/Aurora, Redis, S3; secrets in Secrets Manager | infra/cloudformation/pipeline1-data.yml, infra/cloudformation/pipeline2-data.yml | Partial | Validate key policy separation and retention standards against bank data classification policy |
| PCI 4.x | Protect data in transit | TLS at ALB/CloudFront, Redis transit encryption required, private service endpoints | infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml, infra/cloudformation/pipeline1-data.yml, infra/cloudformation/pipeline2-data.yml | Partial | Add TLS policy evidence and cipher baseline verification report |
| PCI 5.x | Protect systems from malware and threats | GuardDuty runtime monitoring and threat detections enabled | infra/cloudformation/pipeline1-security.yml, infra/cloudformation/pipeline2-security.yml | Partial | Add endpoint and container image scanning operating procedure with remediation SLA |
| PCI 6.x | Develop and maintain secure systems | CloudFormation-controlled deployments, role separation, managed services | infra/cloudformation/*.yml, docs/setup/operations.md | Partial | Add secure SDLC gates, dependency vulnerability policy, and signed release process |
| PCI 7.x | Restrict access by business need | Task and execution IAM role separation, scoped SSM/Secrets paths | infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml | Partial | Run IAM Access Analyzer and least-privilege review on wildcard resources |
| PCI 8.x | Identify and authenticate access | Cognito user pool for dashboard users, password policy, role groups | infra/cloudformation/pipeline2-dashboard.yml | Partial | Add MFA policy evidence for console/admin and user lifecycle runbook |
| PCI 9.x | Physical access controls | Inherited from AWS managed data center controls | AWS shared responsibility model | Inherited | Capture AWS Artifact evidence in compliance package |
| PCI 10.x | Log and monitor access and events | CloudTrail multi-region with log validation, CloudWatch logs and alarms | infra/cloudformation/pipeline1-security.yml, infra/cloudformation/pipeline2-security.yml, infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml | Partial | Add centralized log retention policy and alert triage runbook with owner and SLA |
| PCI 11.x | Test security of systems and networks | WAF managed rules and rate limits, Security Hub standards enabled | infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml, infra/cloudformation/pipeline1-security.yml, infra/cloudformation/pipeline2-security.yml | Partial | Add regular penetration testing and external vulnerability scan cadence |
| PCI 12.x | Support information security with policy | Security tooling enabled and account boundary model defined | docs/aws_infrastructure_review_pipeline1_pipeline2.md | Partial | Formalize policy set, RACI, and annual policy review evidence |

## 2. FFIEC Domain Mapping

| FFIEC Domain | Expectation Summary | Current Implementation | Evidence Location | Status | Gap and Action |
|---|---|---|---|---|---|
| Governance and risk management | Defined ownership, controls, and decision rights | Dual-account design with separation of responsibilities between Fortress and banks | docs/aws_infrastructure_review_pipeline1_pipeline2.md, docs/setup/operations.md | Partial | Publish formal shared responsibility RACI and escalation matrix |
| Threat intelligence and collaboration | Detect and respond to emerging threats | GuardDuty and Security Hub enabled in both account scopes | infra/cloudformation/pipeline1-security.yml, infra/cloudformation/pipeline2-security.yml | Partial | Define routine threat intel review and control tuning cycle |
| Cybersecurity controls | Layered preventive controls across network, identity, and data | WAF, SG segmentation, IAM role separation, KMS, Secrets Manager | infra/cloudformation/pipeline1-*.yml, infra/cloudformation/pipeline2-*.yml | Partial | Document exception process and periodic control effectiveness testing |
| External dependency management | Manage third-party and cloud dependencies | Bedrock and AWS managed services consumed through IAM-scoped access | infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml | Partial | Maintain third-party risk register and annual reassessment artifacts |
| Incident response and resilience | Detect, triage, recover with tested procedures | CloudWatch alarms, CloudTrail audit, runbooks for infra and sync troubleshooting | docs/runbooks/agent-1-infra.md, docs/runbooks/agent-4-sync.md | Partial | Add incident tabletop exercise records and tested RTO/RPO evidence |
| Situational awareness and monitoring | Continuous logging and alerting | CloudWatch log groups, ALB/ECS alarms, Security Hub findings | infra/cloudformation/pipeline1-compute.yml, infra/cloudformation/pipeline2-compute.yml, infra/cloudformation/pipeline1-security.yml, infra/cloudformation/pipeline2-security.yml | Partial | Add SOC dashboard with KPIs, MTTD, MTTR, and exception tracking |

## 3. Control Ownership Model (Recommended)

| Control Area | Fortress Owner | Bank Owner | Shared |
|---|---|---|---|
| Pipeline 1 network and app security | Yes | No | No |
| Pipeline 2 account security posture | No | Yes | Yes |
| WAF and API sync trust model | Yes | Yes | Yes |
| Data encryption and key ownership in bank account | No | Yes | No |
| Security Hub and GuardDuty finding triage | No | Yes | Yes |
| Compliance evidence package assembly | Yes | Yes | Yes |

## 4. Priority Compliance Actions Before Production

1. Finalize secret rotation architecture and runbook with rollback plan.
2. Enforce production configuration guardrails (for example P2 MultiAZ and P1 Redis HA profile).
3. Produce signed RACI for detection, triage, and incident response obligations.
4. Establish recurring evidence collection for audit logs, IAM reviews, and control test results.
5. Run joint Fortress and bank tabletop exercise and capture outcomes.

## 5. Evidence Bundle Checklist

1. CloudFormation templates and stack outputs by environment.
2. IAM policies for ECS execution and task roles.
3. WAF ACL configuration export and change history.
4. CloudTrail trail status and S3 bucket policy evidence.
5. GuardDuty and Security Hub enabled-standards evidence.
6. Database and backup configuration evidence for Aurora/RDS.
7. Runbooks and incident response contact matrix.
