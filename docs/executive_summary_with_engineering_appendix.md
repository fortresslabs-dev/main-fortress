# Fortress Production Readiness Executive Summary

Date: 2026-06-12  
Audience: Leadership, AWS reviewers, engineering

## One-Page Executive Summary

## What we built

Fortress uses a two-pipeline AWS architecture to separate shared regulatory intelligence from bank-specific operations:

1. Pipeline 1 in Fortress account ingests and normalizes regulatory content, then exposes controlled sync APIs.
2. Pipeline 2 in each bank account pulls sync data and executes compliance workflows and dashboard operations.

This model reduces cross-tenant risk and keeps bank-owned operational data inside the bank account boundary.

## Why this architecture is production-capable

- Security layers are present at perimeter, network, identity, and data levels.
- Core services are managed AWS primitives with high operational maturity.
- Audit and threat-detection controls are enabled in both account scopes.
- Infrastructure is codified in CloudFormation with repeatable deployment flow.

## AWS services in scope

VPC, IAM, ECS Fargate, ECR, ALB, WAF, ACM, Aurora PostgreSQL, RDS PostgreSQL, ElastiCache Redis, KMS, Secrets Manager, SSM, S3, CloudWatch, EventBridge Scheduler, SNS, CloudTrail, GuardDuty, Security Hub, CloudFront, Cognito, Bedrock, Route 53 integration, and AWS Marketplace model subscription path.

## Current readiness level

Overall readiness: Strong pilot-to-production baseline with targeted hardening items remaining.

Green:

1. Account isolation model between Fortress and banks
2. Encryption controls and secret management pattern
3. WAF plus ALB perimeter controls
4. CloudTrail, GuardDuty, Security Hub enablement

Amber:

1. Secret rotation mechanism currently disabled pending safe rollout workflow
2. Pipeline 1 Redis still in pilot single-node profile
3. Pipeline 2 MultiAZ database must be enforced in production profile
4. Some dependencies are created out-of-band, increasing configuration drift risk

Red:

1. None identified that blocks architecture review itself, but amber items should be closed before broad production rollout.

## Leadership decisions requested

1. Approve production hardening sprint focused on reliability and secrets lifecycle.
2. Approve shared incident response and compliance ownership model with bank teams.
3. Approve go-live gate criteria tied to specific technical controls in the appendix.

## Proposed go-live gates

1. P1 Redis upgraded to HA configuration.
2. P2 production profile enforces MultiAZ database.
3. Secret rotation design validated in staging with rollback test.
4. Drift detection and evidence collection jobs operational.
5. Joint Fortress and bank incident tabletop completed.

---

## Engineering Appendix

## A. Deployment topology

Pipeline 1 (Fortress account):

- ECS Fargate API service
- ECS scheduled scrape and digest tasks via EventBridge Scheduler
- Aurora PostgreSQL Serverless v2, ElastiCache Redis, S3
- ALB + WAF + ACM for sync API
- GuardDuty, Security Hub, CloudTrail, CloudWatch

Pipeline 2 (bank account):

- ECS Fargate auto-scaling service
- RDS PostgreSQL, ElastiCache Redis
- ALB + WAF + ACM in production; optional CloudFront front door in trial mode
- Dashboard static hosting on S3 + CloudFront + Cognito
- GuardDuty, Security Hub, CloudTrail, CloudWatch

## B. Security model details

1. Private data plane:
  - DB and Redis in private subnets, SG-locked to ECS SG.
2. Public ingress:
  - ALB only, WAF-protected, HTTP redirect to HTTPS in production profiles.
3. Secret and key handling:
  - Secrets Manager for credentials and tokens.
  - KMS CMKs for storage encryption domains.
4. IAM pattern:
  - ECS execution roles for image pull and secret resolution.
  - ECS task roles for runtime API access (Bedrock, S3, logs, SSM, secrets).

## C. Data stores and retrieval

1. PostgreSQL is the system of record.
2. Hybrid retrieval in PostgreSQL using pgvector plus tsvector.
3. Redis used for performance-sensitive caching and runtime acceleration.
4. S3 used for chunk storage, logs, and static content.

## D. Operational controls and observability

1. CloudWatch logs for ECS services and tasks.
2. CloudWatch alarms for API latency and ECS CPU pressure.
3. CloudTrail multi-region trails with log file validation.
4. GuardDuty runtime and RDS login event coverage.
5. Security Hub standards enabled for PCI and foundational best practices.

## E. Critical open actions

1. Build and test secret rotation workflow with fail-safe app config regeneration.
2. Promote P1 Redis from pilot to HA configuration.
3. Enforce P2 production profile controls in deployment automation.
4. Add periodic IAM least-privilege review and drift detection.
5. Define explicit SOC/SRE RACI for finding triage and incident ownership.

## F. Artifact links for reviewers

1. docs/aws_infrastructure_review_pipeline1_pipeline2.md
2. docs/aws_well_architected_review_pipeline1_pipeline2.md
3. docs/pci_ffiec_control_mapping_matrix.md
4. infra/cloudformation/pipeline1-vpc.yml
5. infra/cloudformation/pipeline1-data.yml
6. infra/cloudformation/pipeline1-compute.yml
7. infra/cloudformation/pipeline1-security.yml
8. infra/cloudformation/pipeline2-vpc.yml
9. infra/cloudformation/pipeline2-data.yml
10. infra/cloudformation/pipeline2-compute.yml
11. infra/cloudformation/pipeline2-dashboard.yml
12. infra/cloudformation/pipeline2-security.yml
13. docs/p2_dashboard_api_production_integration.md
