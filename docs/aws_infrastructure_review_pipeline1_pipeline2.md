# Fortress AWS Infrastructure Review (Pipeline 1 + Pipeline 2)

Last updated: 2026-06-12  
Audience: AWS review team, platform engineering, security/compliance

## 1. Executive Summary

This document reverse-engineers the deployed and Infrastructure-as-Code defined AWS stack for:

- Pipeline 1 (Fortress cloud account): regulatory scraping, compliance data processing, sync API
- Pipeline 2 (bank account): bank-isolated agent pipeline, validation APIs, dashboard backend/frontend

Design intent is strict account separation:

- Pipeline 1 holds shared regulatory intelligence only
- Pipeline 2 runs in each bank account and stores bank-specific operational data
- Data exchange is pull-based from Pipeline 2 to Pipeline 1 over authenticated sync APIs

## 2. Source of Truth Used

This review is based on these templates and runbooks:

- infra/cloudformation/pipeline1-vpc.yml
- infra/cloudformation/pipeline1-data.yml
- infra/cloudformation/pipeline1-compute.yml
- infra/cloudformation/pipeline1-security.yml
- infra/cloudformation/pipeline2-vpc.yml
- infra/cloudformation/pipeline2-data.yml
- infra/cloudformation/pipeline2-compute.yml
- infra/cloudformation/pipeline2-dashboard.yml
- infra/cloudformation/pipeline2-security.yml
- docs/setup/operations.md
- docs/runbooks/agent-1-infra.md

## 3. Complete AWS Service Inventory (All Services Used)

The platform currently uses the following AWS services:

1. Amazon VPC (VPC, subnets, route tables, IGW, NAT, EIP, security groups, VPC endpoints)
2. AWS Identity and Access Management (IAM)
3. Amazon ECS on AWS Fargate
4. Amazon Elastic Container Registry (ECR) (repository created out-of-band)
5. Elastic Load Balancing v2 (Application Load Balancer)
6. AWS WAF v2
7. AWS Certificate Manager (ACM)
8. Amazon RDS for PostgreSQL (Pipeline 2)
9. Amazon Aurora PostgreSQL Serverless v2 (Pipeline 1)
10. Amazon ElastiCache for Redis
11. AWS KMS
12. AWS Secrets Manager
13. AWS Systems Manager Parameter Store (SSM)
14. Amazon S3
15. Amazon CloudWatch (metrics, alarms, log groups)
16. Amazon EventBridge Scheduler
17. Amazon SNS
18. AWS CloudTrail
19. Amazon GuardDuty
20. AWS Security Hub
21. Amazon CloudFront
22. Amazon Cognito
23. Amazon Bedrock
24. AWS Marketplace (model subscription path required by Bedrock usage)
25. Amazon Route 53 (pre-existing hosted zones and DNS records used for certificate validation/CNAMEs)

## 4. Architecture Boundary and Data Residency

### 4.1 Pipeline 1 (Fortress account)

- Purpose: ingest public regulatory sources and serve sync APIs
- Compute: ECS Fargate API task + one-shot scraper/init/digest tasks
- Data: Aurora PostgreSQL + pgvector/tsvector, Redis, S3
- Exposure: internet-facing ALB with WAF protections

### 4.2 Pipeline 2 (Bank account)

- Purpose: bank-side compliance validation and 6-agent workflow execution
- Compute: ECS Fargate service with autoscaling
- Data: bank-owned RDS PostgreSQL + Redis, dashboard assets in S3/CloudFront
- Exposure: internet-facing ALB (and optional CloudFront in trial mode), Cognito-authenticated dashboard

### 4.3 Cross-account interaction

- Pipeline 2 pulls from Pipeline 1 sync endpoints
- Pipeline 1 WAF allowlists bank NAT EIP(s)
- App-level API key and HMAC checks gate sync operations
- No direct DB-level trust or shared VPC between accounts

## 5. Pipeline 1 Stack (Fortress Account)

## 5.1 Network and Perimeter (pipeline1-vpc.yml)

- VPC CIDR default 10.1.0.0/16
- 2 public + 2 private subnets across two AZs
- NAT Gateway for private-subnet egress to regulatory sites
- Security groups:
  - ALB SG: 443/80 inbound from internet
  - ECS SG: inbound 8000 from ALB only; egress 443 + controlled egress to DB/Redis
  - Aurora SG: 5432 only from ECS SG
  - Redis SG: 6379 only from ECS SG
  - VPC endpoint SG: 443 from ECS SG
- VPC endpoints:
  - Gateway: S3
  - Interface: Bedrock Runtime, SSM, Secrets Manager, CloudWatch Logs, ECR DKR, ECR API, STS

## 5.2 Data Layer (pipeline1-data.yml)

- Aurora PostgreSQL 15.10 (Serverless v2)
  - Scaling: 2-16 ACU default
  - Encryption at rest via CMK
  - CloudWatch log export enabled
  - DeletionProtection true
  - BackupRetentionPeriod 7 days
- Redis 7.1 (ElastiCache replication group)
  - Pilot mode single node (no failover)
  - TLS in transit required
  - At-rest encryption with CMK
  - AUTH token from Secrets Manager
- S3 bucket for chunks/backups/audit artifacts
  - Versioning enabled
  - KMS encryption
  - Public access block enabled
  - Lifecycle transition and non-current version cleanup rules
- KMS keys:
  - Separate CMKs for RDS, Redis, S3
  - Rotation enabled
- Secrets Manager:
  - DB password
  - Redis auth token
  - App config bundle secret including DATABASE_URL, REDIS_URL, model IDs, S3 settings
- SSM Parameter Store:
  - Aurora host/port
  - Redis host
  - DB name
  - S3 bucket

## 5.3 Compute and API (pipeline1-compute.yml)

- ECS cluster with Container Insights enabled
- Fargate task definitions:
  - API service task
  - One-shot scraper task
  - One-shot schema init task
  - One-shot daily digest task
- ALB:
  - HTTP listener redirects to HTTPS
  - HTTPS listener with ACM certificate
  - Target group health check on /health/
- WAF:
  - Bank NAT IP allowlist IPSet
  - Rate limit on /api/sync path
  - AWS managed common rule set
- EventBridge Scheduler:
  - Daily scraper schedule
  - Daily digest schedule
- SNS:
  - Alarm topic
  - Separate digest topic
- CloudWatch:
  - Log group /fortress/pipeline1
  - ECS CPU alarm
- IAM roles:
  - ECS execution role for image pull/secrets resolution
  - ECS task role for Bedrock, S3, secrets/SSM reads, logs, metrics, SNS publish

## 5.4 Security and Audit (pipeline1-security.yml)

- GuardDuty detector enabled
  - Runtime monitoring
  - ECS Fargate agent management
  - RDS login events
- Security Hub enabled
  - PCI DSS v3.2.1
  - AWS Foundational Security Best Practices v1.0.0
- CloudTrail:
  - Multi-region trail
  - Log validation enabled
  - Logs written to S3 with explicit bucket policy

## 6. Pipeline 2 Stack (Bank Account)

## 6.1 Network and Perimeter (pipeline2-vpc.yml)

- VPC CIDR default 10.2.0.0/16 (customizable to avoid overlap)
- 2 public + 2 private subnets across two AZs
- NAT Gateway in public subnet for egress and sync calls to Pipeline 1
- Security groups:
  - ALB SG: 443/80 inbound from internet
  - ECS SG: inbound 8000 from ALB only; egress 443 and controlled DB/Redis egress
  - RDS SG: 5432 from ECS SG only
  - Redis SG: 6379 from ECS SG only
  - Endpoint SG: 443 from ECS SG
- VPC endpoints:
  - Gateway: S3
  - Interface: Bedrock Runtime, SSM, Secrets Manager, CloudWatch Logs, ECR DKR, ECR API, STS

## 6.2 Data Layer (pipeline2-data.yml)

- RDS PostgreSQL 15.8
  - Default class db.t4g.medium
  - gp3 storage with max autoscale
  - Encrypted with bank-owned CMK
  - MultiAZ configurable (default false in trial)
  - CloudWatch exports for postgresql and upgrade logs
- Redis 7.1 (ElastiCache replication group)
  - 2 cache nodes
  - Automatic failover enabled
  - MultiAZ enabled
  - Transit + at-rest encryption with bank-owned CMK
- Secrets Manager (bank namespace fortress/{BankId}/...):
  - DB password
  - Redis auth token
  - JWT secret
  - JIRA API token
  - JIRA webhook secret
  - Internal sync trigger secret
  - Pipeline 1 API key
- SSM Parameter Store:
  - PIPELINE_MODE, CLIENT_NAME, host/port values, DB name, redis host, P1 API URL, environment

## 6.3 Compute and API (pipeline2-compute.yml)

- ECS cluster with Container Insights
- Fargate service with autoscaling
  - Desired count min/max parameters (default 2-10)
  - Application Auto Scaling target-tracking CPU policy
- ALB:
  - HTTPS listener in prod mode via ACM
  - HTTP->HTTPS redirect in prod
  - Trial mode supports HTTP target + CloudFront HTTPS front door
- WAF:
  - Global rate limit rule
  - AWS managed common rule set
- CloudFront (trial mode only) for HTTPS without custom domain
- CloudWatch:
  - Log group /fortress/pipeline2/{bank}
  - ALB p99 latency alarm
- SNS alarm topic
- IAM roles:
  - Execution role resolves Secrets Manager and SSM secret refs for ECS
  - Task role invokes Bedrock and reads runtime secrets/parameters

## 6.4 Dashboard and Identity (pipeline2-dashboard.yml)

- S3 bucket for dashboard static assets
- CloudFront distribution with OAC to S3
- ACM cert + custom alias in prod mode
- Cognito user pool
  - Admin-created users only
  - Strong password policy
  - Role groups: CCO, CRO, CIO, Analyst, Admin
- Cognito app client and hosted UI domain
- SSM parameters for dashboard and Cognito IDs

### 6.4.1 Production Dashboard to P2 API Connection

- Dashboard origin in production: https://dashboard.<BankDomain>
- API origin in production: https://fortressapi.<BankDomain>
- Dashboard reads P2 API URL from SSM key /fortress/<BankId>/P2_API_URL during deploy
- Cognito OAuth callbacks/logout target /auth/callback and /auth/logout on dashboard origin
- Dashboard sends Authorization Bearer tokens to P2 API endpoints
- P2 API validates Cognito JWT and enforces role-based access
- CORS allowlist in P2 must explicitly include dashboard production origin

Detailed implementation runbook:

- docs/p2_dashboard_api_production_integration.md

## 6.5 Security and Audit (pipeline2-security.yml)

- GuardDuty detector enabled (runtime, ECS Fargate, RDS login events)
- Security Hub with PCI DSS + FSBP standards
- CloudTrail multi-region with log validation to bank-owned S3 bucket

## 7. Security Posture Summary

## 7.1 Network controls

- Ingress to app containers only through ALB SGs
- DB and Redis are private-only, SG-restricted to ECS SGs
- Private subnet workloads use NAT + VPC endpoints
- WAF attached to both P1 and P2 ALBs

## 7.2 Identity and secrets

- Execution roles and task roles are separated
- Runtime secrets supplied via Secrets Manager (not plaintext env in templates)
- Static non-secret config from SSM
- KMS CMKs used for DB/cache/storage encryption

## 7.3 Data protection

- Encryption at rest: Aurora/RDS/Redis/S3 with KMS
- Encryption in transit: TLS on ALB/CloudFront and Redis transit encryption
- RDS/Aurora not publicly accessible

## 7.4 Detection and audit

- CloudTrail + log integrity validation enabled
- GuardDuty runtime and RDS login detections enabled
- Security Hub standards explicitly enabled
- CloudWatch alarms on key service signals

## 8. Data Stores and Search Strategy

- Primary transactional + retrieval store: PostgreSQL (Aurora in P1, RDS in P2)
- Vector + lexical retrieval: pgvector + tsvector in PostgreSQL
- Cache layer: ElastiCache Redis
- Object store: S3 for chunks, audit, static content
- OpenSearch Serverless is intentionally omitted in current architecture
- Weaviate is deprecated and not part of current production stack

## 9. External Dependencies and Out-of-Band Items

These are used but not fully created in the shown templates:

- ECR repositories are created by deployment scripts before compute stacks
- Route 53 hosted zones/records are expected to exist for ACM validation and CNAME mapping
- Some bank secrets are seeded post-deploy (JIRA token, GitHub token, P1 API key)
- Bedrock model access requires AWS Marketplace subscription permissions

## 10. Production Review Notes for AWS Team

## 10.1 Current hardening strengths

- Strong account separation between Fortress and bank environments
- Private data plane for DB/cache with SG-only access
- End-to-end encryption and centralized secret management
- WAF + ALB + managed rule sets on internet-exposed surfaces
- Continuous audit/detection services enabled by default

## 10.2 Important gaps or decisions to confirm before prod cutover

1. Pipeline 1 Redis is single-node pilot config; consider Multi-AZ + failover for production SLA.
2. Pipeline 2 default sets DbMultiAz=false in trial; enable true for production banks.
3. Secrets rotation schedules are currently disabled; align on safe rotation design and operational runbook.
4. Confirm Route 53, certificate validation, and DNS ownership workflows in each bank account.
5. Validate WAF thresholds against expected sync and webhook traffic to avoid false positives.
6. Confirm backup retention/RPO/RTO targets meet policy (Aurora/RDS/S3/CloudTrail).
7. Confirm incident routing for SNS alarms and Security Hub findings (bank SOC vs Fortress SOC responsibilities).

## 11. Suggested Attachments for AWS Architecture Review Meeting

Attach the following alongside this document:

1. All nine CloudFormation templates listed in Section 2
2. Current CloudFormation stack outputs per environment/bank
3. IAM role policy exports for ECS execution/task roles (P1/P2)
4. WAF Web ACL exported JSON policies
5. Security Hub enabled standards screenshots or API exports
6. CloudTrail trail status and S3 bucket policies
7. RDS/Aurora parameter groups and backup configuration evidence

---

If needed, a follow-up version can include a formal control matrix mapped to PCI DSS and FFIEC controls (control objective, AWS service, implementation evidence, owner, and review cadence).