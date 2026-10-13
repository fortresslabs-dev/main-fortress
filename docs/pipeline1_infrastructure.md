# Pipeline 1 — AWS Infrastructure Reference

**Account:** 520385696621
**Region:** us-east-1 (N. Virginia)
**Last verified:** 2026-04-23
**Last updated:** 2026-04-23 (ECS credentials proxy fix, CloudWatch logging fix, scraper startup)

---

## Overview

Pipeline 1 is the Fortress Cloud side of the dual-pipeline architecture. It scrapes 18+ US regulatory sources (OCC, OFAC, FinCEN, FDIC, CFPB, FFIEC, FRB) on a 24-hour cycle, stores compliance rules in PostgreSQL, and serves a sync API that Pipeline 2 (customer VPC) pulls from.

The entire stack runs as a single ECS task (4 containers) on one EC2 instance in us-east-1. Customer data never enters this environment — only rule diffs flow outward.

---

## Architecture Diagram

```
Internet
    │
    │ :8000 (API)   :22 (SSH – single IP)
    ▼
┌─────────────────────────────────────────────────────────┐
│  Security Group: sg-0bbdf3e8493a167ff (launch-wizard-3) │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │  EC2 t3.xlarge  (i-076b2f61782c38f06)            │   │
│  │  fortress_Server · us-east-1a · 54.196.96.184    │   │
│  │                                                  │   │
│  │  ┌────────────────────────────────────────────┐  │   │
│  │  │  ECS Task: fortress-pipeline1 (rev 9)      │  │   │
│  │  │  bridge network                             │  │   │
│  │  │                                            │  │   │
│  │  │  [postgres:15-alpine]  256cpu  512MB       │  │   │
│  │  │  [redis:7-alpine]      128cpu  256MB       │  │   │
│  │  │  [weaviate:1.28.2]     512cpu  1024MB      │  │   │
│  │  │  [pipeline1 app]      1024cpu  3072MB      │  │   │
│  │  └────────────────────────────────────────────┘  │   │
│  │                                                  │   │
│  │  ECS Agent (Docker container, --net host)        │   │
│  │  CloudWatch Agent                                │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  EBS gp3 100 GB  (vol-0eda50f872f305910)  /dev/sda1     │
└─────────────────────────────────────────────────────────┘

External AWS Services (called from containers via task role)
  ├── ECR        — pull app image
  ├── SSM        — runtime config/secrets
  ├── Bedrock    — LLM inference (claude-sonnet-4)
  ├── S3         — postgres backups (fortress-backups-prod)
  └── CloudWatch — logs + metrics
```

---

## 1. EC2 Instance

| Field | Value |
|---|---|
| Instance ID | `i-076b2f61782c38f06` |
| Name tag | `fortress_Server` |
| Type | `t3.xlarge` (4 vCPU, 16 GB RAM) |
| AMI | `ami-0ec10929233384c7f` |
| Availability Zone | `us-east-1a` |
| Public IP | `54.196.96.184` (dynamic — no Elastic IP) |
| Private IP | `172.31.42.35` |
| Key pair | `fortress_server` |
| IMDSv2 | Enabled, required (hop limit 2) |
| Monitoring | CloudWatch detailed monitoring enabled |
| EBS optimised | Yes |
| Launched | 2026-04-18 |

**Persistent storage:**

| Volume | ID | Size | Type | Encrypted | Mount |
|---|---|---|---|---|---|
| Root | `vol-0eda50f872f305910` | 100 GB | gp3 (3000 IOPS, 125 MB/s) | No | `/dev/sda1` |

> **Open issue:** root volume is unencrypted. Postgres data and ECS agent data persist here. Encrypt on next AMI refresh.

**Data paths on instance:**

| Path | Contents |
|---|---|
| `/mnt/fortress-data/postgres` | PostgreSQL data (bind-mounted into ECS task) |
| `/mnt/fortress-data/weaviate` | Weaviate data (bind-mounted into ECS task) |
| `/var/lib/ecs/data` | ECS agent state |
| `/var/log/ecs/` | ECS agent logs |
| `/etc/ecs/ecs.config` | ECS agent configuration |
| `/opt/aws/amazon-cloudwatch-agent/` | CloudWatch agent |

---

## 2. Networking

### VPC

| Field | Value |
|---|---|
| VPC ID | `vpc-04c1ec439a944d31d` |
| CIDR | `172.31.0.0/16` |
| Type | Default VPC |
| Internet Gateway | Block public access OFF (internet-accessible) |

### Subnet

| Field | Value |
|---|---|
| Subnet ID | `subnet-0870202bd573f4223` |
| AZ | `us-east-1a` |
| CIDR | `172.31.32.0/20` |
| Auto-assign public IP | Yes |

### Security Group: `sg-0bbdf3e8493a167ff` (`launch-wizard-3`)

**Inbound rules:**

| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 8000 | TCP | `0.0.0.0/0` | Pipeline 1 API (sync endpoint, health check) |
| 22 | TCP | `23.243.249.22/32` | SSH — single trusted IP only |

**Outbound rules:**

| Port | Protocol | Destination | Purpose |
|---|---|---|---|
| All | All | `0.0.0.0/0` | Regulatory scraping, ECR pull, AWS API calls |

> **Open issue:** security group name `launch-wizard-3` was auto-generated. Rename to `fortress-pipeline1-sg` and manage via IaC. Use `infra/ecs/create-launch-template-asg.sh` to apply tags on first ASG run.

---

## 3. ECS

### Cluster

| Field | Value |
|---|---|
| Cluster name | `fortress-cluster` |
| ARN | `arn:aws:ecs:us-east-1:520385696621:cluster/fortress-cluster` |
| Launch type | EC2 |
| Registered instances | 1 |
| Capacity providers | None (direct EC2 launch type) |

### Service

| Field | Value |
|---|---|
| Service name | `fortress-pipeline1-service` |
| Scheduling | REPLICA |
| Desired / Running | 1 / 1 |
| Launch type | EC2 |
| Task definition | `fortress-pipeline1:13` |
| Deployment strategy | Rolling update |
| Min healthy % | 0 (allows full replacement) |
| Max % | 100 (at most 1 task at any time) |
| Circuit breaker | Disabled |
| Placement constraints | None |
| Health check grace | 0 s |

### Task Definition: `fortress-pipeline1:13`

**Network mode:** bridge (inter-container links)
**Task role:** `fortress-pipeline1-task-role`
**Execution role:** `fortress-pipeline1-execution-role`

#### Containers

| Container | Image | CPU units | Memory | Essential | Port |
|---|---|---|---|---|---|
| `postgres` | `postgres:15-alpine` | 256 | 512 MB | Yes | — |
| `redis` | `redis:7-alpine` | 128 | 256 MB | Yes | — |
| `weaviate` | `semitechnologies/weaviate:1.28.2` | 512 | 1024 MB | Yes | — |
| `pipeline1` | `520385696621.dkr.ecr.us-east-1.amazonaws.com/fortress/pipeline1:latest` | 1024 | 3072 MB | Yes | 8000→8000 |

**Total reserved:** 1920 CPU units, 4864 MB (fits comfortably in t3.xlarge: 4096 CPU, 15785 MB available)

#### Startup order (dependsOn)

`postgres HEALTHY` → `redis HEALTHY` → `weaviate HEALTHY` → `pipeline1` starts

#### Health checks

| Container | Command | Interval | Timeout | Retries | Start period |
|---|---|---|---|---|---|
| postgres | `pg_isready -U fortress -d compliance_rules` | 30 s | 5 s | 5 | 30 s |
| redis | `redis-cli ping` | 30 s | 5 s | 3 | 10 s |
| weaviate | `wget -qO- http://localhost:8080/v1/.well-known/ready` | 30 s | 10 s | 3 | 60 s |
| pipeline1 | `curl -sf http://localhost:8000/health` | 30 s | 10 s | 3 | 60 s |

#### Secrets injected at launch (from SSM via execution role)

| Env var | SSM path |
|---|---|
| `POSTGRES_PASSWORD` | `/fortress/POSTGRES_PASSWORD` |
| `DATABASE_URL` | `/fortress/DATABASE_URL` |
| `SECRET_KEY` | `/fortress/SECRET_KEY` |
| `JWT_SECRET` | `/fortress/JWT_SECRET` |
| `FORTRESS_INTERNAL_SECRET` | `/fortress/FORTRESS_INTERNAL_SECRET` |

All SSM parameters are `SecureString` (AES-256 encrypted with AWS-managed key).

#### Static environment variables (non-secret)

| Container | Variable | Value |
|---|---|---|
| postgres | `POSTGRES_USER` | `fortress` |
| postgres | `POSTGRES_DB` | `compliance_rules` |
| weaviate | `QUERY_DEFAULTS_LIMIT` | `25` |
| weaviate | `ENABLE_MODULES` | `text2vec-aws` |
| weaviate | `DEFAULT_VECTORIZER_MODULE` | `text2vec-aws` |
| weaviate | `AWS_REGION` | `us-east-1` |
| weaviate | `CLUSTER_HOSTNAME` | `node1` |
| pipeline1 | `REDIS_URL` | `redis://redis:6379/0` |
| pipeline1 | `WEAVIATE_URL` | `http://weaviate:8080` |
| pipeline1 | `ENVIRONMENT` | `production` |
| pipeline1 | `AWS_REGION` | `us-east-1` |
| pipeline1 | `CLIENT_NAME` | `fortress-labs` |
| pipeline1 | `SCRAPER_RUN_ON_STARTUP` | `true` |

AWS credentials are **not** injected as environment variables. All AWS API calls use the task IAM role via the instance metadata service.

#### Logging

All containers → CloudWatch Logs driver → log group `/fortress/pipeline1` (30-day retention)

| Container | Stream prefix |
|---|---|
| postgres | `postgres/<task-id>` |
| redis | `redis/<task-id>` |
| weaviate | `weaviate/<task-id>` |
| pipeline1 | `app/<task-id>` |

---

## 4. ECR (Elastic Container Registry)

| Field | Value |
|---|---|
| Repository | `fortress/pipeline1` |
| URI | `520385696621.dkr.ecr.us-east-1.amazonaws.com/fortress/pipeline1` |
| Tag mutability | Mutable |
| Scan on push | Enabled (vulnerability scanning) |
| Encryption | AES-256 (AWS-managed) |
| Current image | `latest` tag |

Images are pushed by `scripts/ecs_deploy.sh` after a local `docker build`.

---

## 5. IAM

### Role 1: `fortress-pipeline1-task-role`

**Used by:** running app containers (pipeline1, weaviate, postgres, redis)
**Trust:** `ecs-tasks.amazonaws.com`
**ARN:** `arn:aws:iam::520385696621:role/fortress-pipeline1-task-role`

Inline policy: `fortress-task-policy`

| Sid | Actions | Resource |
|---|---|---|
| `ECRReadOnly` | `ecr:GetAuthorizationToken`, `BatchCheckLayerAvailability`, `GetDownloadUrlForLayer`, `BatchGetImage`, `DescribeRepositories`, `ListImages` | `*` |
| `S3BackupReadWrite` | `s3:PutObject`, `GetObject`, `DeleteObject`, `ListBucket` | `arn:aws:s3:::fortress-backups-prod` and `/*` |
| `CloudWatchLogs` | `logs:CreateLogGroup`, `CreateLogStream`, `PutLogEvents`, `DescribeLogStreams`, `DescribeLogGroups` | `arn:aws:logs:*:*:log-group:/fortress/*` |
| `CloudWatchMetrics` | `cloudwatch:PutMetricData`, `GetMetricStatistics`, `ListMetrics` | `*` |
| `EC2DescribeSelf` | `ec2:DescribeInstances`, `DescribeTags` | `*` |
| `SecretsManagerRead` | `secretsmanager:GetSecretValue`, `DescribeSecret` | `arn:aws:secretsmanager:us-east-1:520385696621:secret:fortress/pipeline1/*` |
| `SSMGetParameters` | `ssm:GetParameter`, `GetParameters`, `GetParametersByPath` | `arn:aws:ssm:us-east-1:520385696621:parameter/fortress/*` |
| `BedrockInvoke` | `bedrock:InvokeModel`, `InvokeModelWithResponseStream`, `ListFoundationModels` | `*` |

### Role 2: `fortress-pipeline1-execution-role`

**Used by:** ECS agent (at task launch time — to pull images and inject secrets)
**Trust:** `ecs-tasks.amazonaws.com`
**ARN:** `arn:aws:iam::520385696621:role/fortress-pipeline1-execution-role`

Attached managed policy:

| Policy | Purpose |
|---|---|
| `AmazonECSTaskExecutionRolePolicy` | ECR image pull, CloudWatch log stream creation |

Inline policies:

| Policy name | Actions | Resource |
|---|---|---|
| `fortress-ssm-read` | `ssm:GetParameter`, `GetParameters` | `arn:aws:ssm:us-east-1:520385696621:parameter/fortress/*` |
| `fortress-secretsmanager-read` | `secretsmanager:GetSecretValue`, `DescribeSecret` | `arn:aws:secretsmanager:us-east-1:520385696621:secret:fortress/pipeline1/*` |
| `fortress-cwlogs` | `logs:CreateLogGroup`, `DescribeLogGroups` | `arn:aws:logs:us-east-1:520385696621:log-group:/fortress/*` |
| `fortress-cwlogs` | `logs:CreateLogStream`, `PutLogEvents`, `DescribeLogStreams` | `arn:aws:logs:us-east-1:520385696621:log-group:/fortress/*:log-stream:*` |

> **Note:** `fortress-cwlogs` was added 2026-04-23. The `awslogs` log driver runs under the execution role (not the task role). Without this policy, CloudWatch log streams are never created even though the task role has log permissions.

### Role 3: `fortress-ecs-instance-role`

**Used by:** EC2 instance (host-level — ECS agent registration, SSM access, CloudWatch agent)
**Trust:** `ec2.amazonaws.com`
**Instance profile:** `fortress-ecs-instance-profile`
**ARN:** `arn:aws:iam::520385696621:role/fortress-ecs-instance-role`

Attached managed policies:

| Policy | Purpose |
|---|---|
| `AmazonEC2ContainerServiceforEC2Role` | ECS agent — cluster registration, task placement reporting |
| `AmazonSSMManagedInstanceCore` | SSM Session Manager (remote shell without SSH) |
| `CloudWatchAgentServerPolicy` | CloudWatch agent — emit host metrics and logs |
| `AmazonBedrockFullAccess` | Host-level Bedrock access (legacy — superseded by task role) |

> **Note:** `AmazonBedrockFullAccess` on the instance role is broader than needed now that the task role has scoped Bedrock permissions. Can be removed after confirming containers use only the task role credential chain.

---

## 6. SSM Parameter Store

All parameters are in `us-east-1`, under the `/fortress/` prefix, encrypted as `SecureString`.

### App secrets (consumed by ECS execution role at task launch)

| Parameter | Consumed by |
|---|---|
| `/fortress/POSTGRES_PASSWORD` | postgres + pipeline1 containers |
| `/fortress/DATABASE_URL` | pipeline1 container |
| `/fortress/SECRET_KEY` | pipeline1 container |
| `/fortress/JWT_SECRET` | pipeline1 container |
| `/fortress/FORTRESS_INTERNAL_SECRET` | pipeline1 container |

### Legacy parameters (no longer injected into containers — task role handles AWS auth)

| Parameter | Status |
|---|---|
| `/fortress/AWS_ACCESS_KEY_ID` | Orphaned — was injected into pipeline1; removed from task def |
| `/fortress/AWS_SECRET_ACCESS_KEY` | Orphaned — was injected into pipeline1; removed from task def |
| `/fortress/WEAVIATE_AWS_ACCESS_KEY_ID` | Orphaned — was injected into weaviate; removed from task def |
| `/fortress/WEAVIATE_AWS_SECRET_ACCESS_KEY` | Orphaned — was injected into weaviate; removed from task def |

> These four parameters can be deleted once confirmed no other system references them.

### Client-scoped parameters

| Parameter | Type | Purpose |
|---|---|---|
| `/fortress/acme-bank/PIPELINE1_API_URL` | String | Pipeline 2 endpoint URL for acme-bank |
| `/fortress/acme-bank/PIPELINE1_API_KEY` | SecureString | API key for acme-bank Pipeline 2 |
| `/fortress/acme-bank/PIPELINE2_API_KEY` | SecureString | API key for acme-bank Pipeline 1→2 sync |

---

## 7. Amazon Bedrock

**Purpose:** LLM inference for the 6-agent test generation pipeline.
**Model:** `claude-sonnet-4-20250514` (default; set in `src/utils/config.py`)
**Auth:** IAM task role (`BedrockInvoke` policy) — no static keys used.
**Region:** `us-east-1`

The Weaviate container uses Bedrock's `text2vec-aws` module for vector embeddings. It inherits AWS credentials from the instance metadata service via the ECS task role.

---

## 8. S3

| Bucket | Purpose | Access |
|---|---|---|
| `fortress-labs` | General Fortress assets | Task role has no explicit policy for this bucket |
| `fortress-backups-prod` | PostgreSQL daily backups (via `scripts/backup_postgres.sh` cron at 02:00 UTC) | Task role: `PutObject`, `GetObject`, `DeleteObject`, `ListBucket` |

> **Open issue:** `fortress-backups-prod` bucket policy and public access block settings should be verified. The `ec2_bootstrap.sh` script creates it with `BlockPublicAcls=true` but this was a manual step.

---

## 9. CloudWatch

### Log Groups

| Log group | Retention | Source |
|---|---|---|
| `/fortress/pipeline1` | 30 days | ECS task containers (all 4) via awslogs driver |
| `/fortress/acme-bank/pipeline2/apigw-access` | 90 days | Pipeline 2 API Gateway (customer side) |
| `/fortress/acme-bank/pipeline2/app` | 90 days | Pipeline 2 app logs |

### Log Streams (within `/fortress/pipeline1`)

| Stream prefix | Source |
|---|---|
| `postgres/<task-id>` | PostgreSQL container |
| `redis/<task-id>` | Redis container |
| `weaviate/<task-id>` | Weaviate container |
| `app/<task-id>` | Pipeline 1 FastAPI app |
| `{instance_id}/app` | Host-level app log via CloudWatch agent |
| `{instance_id}/cloudwatch-agent` | CloudWatch agent self-log |
| `{instance_id}/syslog` | System syslog |

### Metrics namespace: `Fortress/Pipeline1`

Collected by CloudWatch Agent at 60-second intervals:

| Metric category | Metrics |
|---|---|
| CPU | `cpu_usage_idle`, `cpu_usage_user`, `cpu_usage_system`, `cpu_usage_iowait` |
| Memory | `mem_used_percent`, `mem_available_percent`, `mem_used`, `mem_available`, `mem_total` |
| Disk | `used_percent`, `used`, `free` for `/`, `/mnt/fortress-data`, `/var/lib/docker` |
| Disk I/O | `reads`, `writes`, `read_bytes`, `write_bytes` |
| Network | `bytes_sent`, `bytes_recv`, `drop_in`, `drop_out` on `eth0` |

---

## 10. ECS Agent Configuration

File on instance: `/etc/ecs/ecs.config`

```ini
ECS_CLUSTER=fortress-cluster
ECS_LOGLEVEL=info
ECS_ENABLE_TASK_IAM_ROLE=true
ECS_ENABLE_TASK_IAM_ROLE_NETWORK_HOST=true
ECS_AVAILABLE_LOGGING_DRIVERS=["awslogs","json-file"]
ECS_ENABLE_CONTAINER_METADATA=true
ECS_CONTAINER_STOP_TIMEOUT=30s
ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE=true
```

The ECS agent runs as a Docker container (`amazon/amazon-ecs-agent:latest`, `--restart unless-stopped`, `--net host`).

### ECS Credentials Proxy — `169.254.170.2` Fix

When the ECS agent runs as a Docker container (not via an ECS-optimized AMI), it binds the task IAM role credentials service to `127.0.0.1:51679` but does **not** automatically assign `169.254.170.2` to the host or create iptables redirect rules. Without this, all containers using bridge networking fail to obtain AWS credentials, causing:

- CloudWatch log streams never created (awslogs driver silently drops logs)
- Secrets Manager calls timing out
- All AWS SDK calls from the app failing

**Fix applied 2026-04-23** — added to `infra/ecs/cluster-bootstrap.sh` and persisted via `/etc/sysctl.conf` + `/etc/rc.local`:

```bash
# 1. Allow Docker bridge traffic to reach 127.0.0.1 after DNAT
#    (without this the kernel silently drops the packets — route_localnet=0 by default)
sudo sysctl -w net.ipv4.conf.all.route_localnet=1
sudo sysctl -w net.ipv4.conf.docker0.route_localnet=1

# 2. Assign link-local address to loopback
sudo ip addr add 169.254.170.2/32 dev lo

# 3. Redirect container traffic to ECS credentials service on 127.0.0.1:51679
sudo iptables -t nat -A PREROUTING -d 169.254.170.2/32 -p tcp --dport 80 \
  -j DNAT --to-destination 127.0.0.1:51679
sudo iptables -t nat -A OUTPUT -d 169.254.170.2/32 -p tcp --dport 80 \
  -j REDIRECT --to-port 51679
```

> `169.254.170.2` is a hardcoded ECS spec address — it never changes across deployments or instances.
> `route_localnet=1` is required because the DNAT target is `127.0.0.1` (loopback). Without it the kernel rejects forwarded packets from the Docker bridge to loopback, causing a silent connect timeout.

---

## 11. Infrastructure Scripts

| Script | Run from | Purpose |
|---|---|---|
| `infra/ec2_bootstrap.sh` | EC2 instance (sudo) | One-time: Docker, AWS CLI, CW agent, systemd service |
| `infra/setup_ebs.sh` | EC2 instance (sudo) | Format and mount `/mnt/fortress-data` EBS volume |
| `infra/ecs/cluster-bootstrap.sh` | EC2 instance (sudo) | Install ECS agent, register with cluster |
| `infra/ecs/create-service.sh` | Local machine | One-time: SSM secrets, IAM roles, log group, task def, ECS service |
| `infra/ecs/create-launch-template-asg.sh` | Local machine | Create Launch Template + ASG for auto-recovery |
| `scripts/ecs_deploy.sh` | Local machine | Build image, push to ECR, register new task def, update service |
| `scripts/ecs_trigger_scrape.py` | Local machine | Manually trigger scrape cycle on running task |

---

## 12. Known Open Issues

| Priority | Issue | Action |
|---|---|---|
| High | No Elastic IP — public IP changes on stop/start | Allocate EIP and associate with `i-076b2f61782c38f06` |
| High | No ASG / Launch Template — no auto-recovery if instance terminates | Run `infra/ecs/create-launch-template-asg.sh` with `SUBNET_ID`, `SECURITY_GROUP_ID`, `INSTANCE_PROFILE_ARN` |
| Medium | Root EBS volume unencrypted | Re-launch from encrypted AMI or snapshot → encrypt → restore |
| Medium | Security group named `launch-wizard-3` — unmanaged, no tag | Rename to `fortress-pipeline1-sg`; the ASG script tags it |
| Medium | 4 orphaned SSM parameters for static AWS keys | Delete `/fortress/AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `WEAVIATE_AWS_ACCESS_KEY_ID`, `WEAVIATE_AWS_SECRET_ACCESS_KEY` |
| Low | `AmazonBedrockFullAccess` on instance role is redundant | Remove after confirming task role is the sole credential source |
| Low | No deployment circuit breaker | Enable in service update to auto-rollback failed deployments |
| Low | Single AZ — no availability zone redundancy | Acceptable for single-instance; ASG can be configured multi-AZ later |

### Resolved Issues

| Resolved | Issue | Fix |
|---|---|---|
| 2026-04-23 | CloudWatch logs not appearing — log streams never created | Added `fortress-cwlogs` inline policy to execution role; `awslogs` driver uses execution role, not task role |
| 2026-04-23 | Secrets Manager + AWS SDK calls timing out from containers | Added `169.254.170.2` to loopback + iptables DNAT + `route_localnet=1` in `cluster-bootstrap.sh`; DNAT to `127.0.0.1` silently dropped without `route_localnet` enabled |
| 2026-04-23 | Scraper waiting 24h on cold start — `compliance_rules` empty after deploy | Added `SCRAPER_RUN_ON_STARTUP=true` to task definition (rev 13) |

---

## 13. Deployment Flow

```
Developer machine
    │
    ├─ docker build -t fortress/pipeline1:$TAG .
    ├─ docker push 520385696621.dkr.ecr.us-east-1.amazonaws.com/fortress/pipeline1:$TAG
    ├─ aws ecs register-task-definition (envsubst infra/ecs/task-definition.json)
    └─ aws ecs update-service --force-new-deployment
           │
           ▼
    ECS scheduler places new task on i-076b2f61782c38f06
    ECS agent pulls image from ECR
    ECS agent reads secrets from SSM (execution role)
    Containers start in order: postgres → redis → weaviate → pipeline1
    Health checks pass → service reaches steady state
```

Zero-downtime deploys are not currently configured (minimumHealthyPercent=0). The old task stops before the new one starts, causing a brief outage of ~60–90 seconds while containers start and pass health checks.
