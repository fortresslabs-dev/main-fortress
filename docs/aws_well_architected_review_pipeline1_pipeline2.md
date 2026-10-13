# Fortress Platform AWS Well-Architected Review

Date: 2026-06-12  
Scope: Pipeline 1 (Fortress account) and Pipeline 2 (bank account)  
Method: AWS Well-Architected Framework six-pillar assessment

## 1. Workload Summary

Fortress runs a dual-account architecture:

1. Pipeline 1 in the Fortress AWS account ingests public regulatory data and exposes sync APIs.
2. Pipeline 2 in each bank AWS account consumes sync data, runs validation and agent workflows, and serves dashboard/API endpoints.

Primary components include ECS Fargate, ALB, WAF, Aurora/RDS PostgreSQL, ElastiCache Redis, S3, CloudWatch, CloudTrail, GuardDuty, Security Hub, KMS, Secrets Manager, SSM, CloudFront, Cognito, and Bedrock.

## 2. Pillar-by-Pillar Assessment

## 2.1 Operational Excellence

Current state:

- IaC-driven deployment via CloudFormation templates for network, data, compute, and security stacks.
- Runbooks exist for infra debugging, sync monitoring, and operations workflows.
- CloudWatch logs, alarms, ECS Container Insights, and scheduler-based jobs are in place.

Strengths:

- Clear stack segmentation by concern and by account boundary.
- Repeatable deployment flow for P1 and P2.
- Explicit one-shot initialization and scraper tasks reduce hidden runtime behavior.

Risks:

- Secret rotation workflows are intentionally disabled pending safe automation design.
- Some critical resources are created out-of-band (ECR repository, some secret values), increasing operational drift risk.

Priority recommendations:

1. Implement drift detection and periodic conformance checks for out-of-band resources.
2. Add pipeline-level deployment scorecards (change failure rate, MTTR, rollback rate).
3. Add automated post-deploy validation runbook execution in CI/CD.

## 2.2 Security

Current state:

- Defense-in-depth with WAF, SG isolation, private subnets, and endpoint-based private AWS API access.
- Encryption at rest with KMS and runtime secret retrieval from Secrets Manager.
- CloudTrail, GuardDuty, and Security Hub enabled in both pipelines.

Strengths:

- Strong account boundary: bank data and keys remain in bank account.
- Least-privilege direction is visible in task role scoping for SSM/Secrets paths.
- Managed WAF rules plus rate limits on public entry points.

Risks:

- Rotation disabled for DB secrets in current state.
- Bank and Fortress responsibility boundaries for Security Hub/GuardDuty triage require explicit RACI and SLA definitions.

Priority recommendations:

1. Implement safe secret rotation with tested application cutover behavior.
2. Add formal security incident ownership matrix across Fortress and bank SOCs.
3. Add mandatory periodic IAM Access Analyzer review of task and execution roles.

## 2.3 Reliability

Current state:

- Multi-AZ subnet topology exists in both pipelines.
- Pipeline 2 Redis is MultiAZ with failover; Pipeline 1 Redis is currently pilot single-node.
- Health checks configured for ECS tasks and ALB targets.

Strengths:

- Auto scaling in Pipeline 2 ECS service.
- RDS/Aurora backups and retention set.
- EventBridge scheduler retries configured for scheduled tasks.

Risks:

- Pipeline 1 Redis single-node is a single point of failure.
- Pipeline 2 database MultiAZ defaults to false in trial profile.

Priority recommendations:

1. Upgrade Pipeline 1 Redis to HA profile for production.
2. Enforce production guardrails that require DbMultiAz=true for go-live banks.
3. Add game-day tests for scraper failure, sync lag, and dependency outages.

## 2.4 Performance Efficiency

Current state:

- Graviton-based classes used for RDS and Redis defaults.
- Aurora Serverless v2 auto-scales based on load in Pipeline 1.
- Endpoint-local AWS API access reduces some network path variability.

Strengths:

- Workload decomposition into API, scrape, init, and digest tasks supports right-sizing.
- Hybrid retrieval strategy in PostgreSQL (pgvector + tsvector) consolidates operational complexity.

Risks:

- Potential noisy-neighbor effects in shared Pipeline 1 if onboarding grows faster than baseline capacity policy.

Priority recommendations:

1. Define per-tenant and global SLOs with saturation alarms.
2. Add periodic load test profile for sync burst and webhook burst traffic.
3. Track Bedrock latency/cost per model and adjust model routing by task type.

## 2.5 Cost Optimization

Current state:

- Pilot defaults are tuned for lower cost in several places (for example P1 Redis single node, P2 trial settings).
- VPC endpoints reduce NAT egress for AWS service calls.

Strengths:

- Explicit trial-versus-production controls in templates.
- Avoidance of duplicate managed search service by using PostgreSQL-based hybrid retrieval.

Risks:

- NAT gateway and CloudFront trial/prod combinations can create hidden recurring cost if not tracked per tenant.
- Out-of-band resources can escape budget tagging controls.

Priority recommendations:

1. Enforce mandatory cost allocation tags at stack and resource level.
2. Publish monthly unit economics: cost per bank, per sync cycle, per workflow.
3. Add AWS Budgets and anomaly detection alarms per account and workload.

## 2.6 Sustainability

Current state:

- Use of managed serverless/container services and Graviton defaults generally aligns with efficiency.

Strengths:

- Auto scaling and right-sized profiles reduce idle overprovisioning.

Risks:

- Trial settings can persist into production if promotion controls are weak.

Priority recommendations:

1. Enforce production profile checks in CI/CD before deployment.
2. Track utilization-driven rightsizing quarterly.

## 3. High-Priority Risk Register

1. Secret rotation disabled for production secrets.
Status: Open  
Impact: High  
Target date: Before production cutover

2. Pipeline 1 Redis single-node pilot profile.
Status: Open  
Impact: High  
Target date: Before production cutover

3. Pipeline 2 DB MultiAZ not mandatory by template default.
Status: Open  
Impact: High  
Target date: Before production cutover

4. Out-of-band resources and values increase drift risk.
Status: Open  
Impact: Medium  
Target date: 30 days

5. Shared SOC ownership model not explicitly codified with runbook SLA.
Status: Open  
Impact: Medium  
Target date: 30 days

## 4. 30-60-90 Day Action Plan

30 days:

1. Lock production guardrails for P2 MultiAZ and P1 Redis HA requirements.
2. Define security and incident RACI across Fortress and bank teams.
3. Add drift detection and conformance checks for out-of-band resources.

60 days:

1. Deploy safe secret rotation mechanism and validate runtime cutover.
2. Execute resilience game days and recovery drills.
3. Implement monthly cost and SLO reporting by pipeline and by bank.

90 days:

1. Complete Well-Architected reassessment with AWS SA.
2. Validate evidence package for compliance and customer audits.
3. Finalize continuous governance dashboard across security, reliability, and cost KPIs.

## 5. Evidence References

Primary evidence source files:

1. infra/cloudformation/pipeline1-vpc.yml
2. infra/cloudformation/pipeline1-data.yml
3. infra/cloudformation/pipeline1-compute.yml
4. infra/cloudformation/pipeline1-security.yml
5. infra/cloudformation/pipeline2-vpc.yml
6. infra/cloudformation/pipeline2-data.yml
7. infra/cloudformation/pipeline2-compute.yml
8. infra/cloudformation/pipeline2-dashboard.yml
9. infra/cloudformation/pipeline2-security.yml
10. docs/aws_infrastructure_review_pipeline1_pipeline2.md
