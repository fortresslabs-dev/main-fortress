# Fortress Labs — Pipeline 1 Setup Prompts
# File: infra/setup/PIPELINE1_SETUP.md
#
# Two ways to use this file:
#
# Option A — Fully automated (Claude CLI runs all 8 steps unattended):
#   chmod +x infra/scripts/setup-p1-auto.sh
#   ./infra/scripts/setup-p1-auto.sh
#
# Option B — Semi-automated (paste one prompt at a time into Claude CLI):
#   claude          ← start interactive session in repo root
#   (paste Prompt 1, wait for completion, paste Prompt 2, etc.)
#
# Prerequisites before running anything:
#   export AWS_REGION=us-east-1
#   export AWS_PROFILE=fortress-prod
#   export DOMAIN_NAME=fortresslabs.dev
#   export ALERT_EMAIL=jamie@fortresslabs.dev
#   export ENVIRONMENT=production
#
# All prompts are idempotent — safe to re-run if one fails midway.

---

## Prompt 1 — Verify AWS access and prerequisites

```
You are setting up Pipeline 1 (Fortress Cloud) on AWS.
First verify everything is ready before touching any infrastructure.

Run these checks and report the result of each one:

1. Confirm AWS identity is the Fortress account:
   aws sts get-caller-identity --query "{Account:Account,Arn:Arn}" --output table

2. Confirm AWS_REGION is set:
   echo "Region: $AWS_REGION"
   If not set, run: export AWS_REGION=us-east-1

3. Confirm Bedrock models are enabled in us-east-1:
   aws bedrock list-foundation-models \
     --region us-east-1 \
     --query "modelSummaries[?contains(modelId,'claude-sonnet-4') || contains(modelId,'titan-embed-text-v2')].{Model:modelId,Status:modelLifecycle.status}" \
     --output table

4. Confirm Docker is running:
   docker info --format "{{.ServerVersion}}" 2>&1 | head -1

5. Confirm the repo has all CloudFormation templates:
   ls infra/cloudformation/pipeline1-*.yml

6. Confirm Python and pip are available:
   python3 --version && pip3 --version

If any check fails, stop and report what is missing.
If all pass, print: "All prerequisites met — ready to deploy."
```

---

## Prompt 2 — Deploy VPC stack

```
Deploy the Pipeline 1 VPC CloudFormation stack.

Run this command and wait for it to complete:

aws cloudformation deploy \
  --region ${AWS_REGION:-us-east-1} \
  --profile ${AWS_PROFILE:-fortress-prod} \
  --stack-name fortress-p1-vpc-${ENVIRONMENT:-production} \
  --template-file infra/cloudformation/pipeline1-vpc.yml \
  --parameter-overrides Environment=${ENVIRONMENT:-production} \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

After the command completes:

1. Check the stack status:
   aws cloudformation describe-stacks \
     --stack-name fortress-p1-vpc-${ENVIRONMENT:-production} \
     --query "Stacks[0].{Status:StackStatus,Reason:StackStatusReason}" \
     --output table

2. If status is CREATE_COMPLETE or UPDATE_COMPLETE, list all exported values:
   aws cloudformation list-exports \
     --query "Exports[?starts_with(Name,'FortressP1')].{Name:Name,Value:Value}" \
     --output table

3. Confirm these 9 exports exist (all required by later stacks):
   - FortressP1-production-VpcId
   - FortressP1-production-PublicSubnetA
   - FortressP1-production-PublicSubnetB
   - FortressP1-production-PrivateSubnetA
   - FortressP1-production-PrivateSubnetB
   - FortressP1-production-SgAlb
   - FortressP1-production-SgEcs
   - FortressP1-production-SgAurora
   - FortressP1-production-SgRedis

If any export is missing, describe the stack events to find the failure:
   aws cloudformation describe-stack-events \
     --stack-name fortress-p1-vpc-${ENVIRONMENT:-production} \
     --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
     --output table

Print STEP 2 COMPLETE when all 9 exports are confirmed.
```

---

## Prompt 3 — Deploy data stack (Aurora + OpenSearch + Redis + S3 + KMS)

```
Deploy the Pipeline 1 data stack. Aurora takes 15-20 minutes — wait for it.

Run this command:

aws cloudformation deploy \
  --region ${AWS_REGION:-us-east-1} \
  --profile ${AWS_PROFILE:-fortress-prod} \
  --stack-name fortress-p1-data-${ENVIRONMENT:-production} \
  --template-file infra/cloudformation/pipeline1-data.yml \
  --parameter-overrides \
    Environment=${ENVIRONMENT:-production} \
    AuroraInstanceClass=db.r6g.large \
    RedisNodeType=cache.r7g.medium \
    DbName=fortress_p1 \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

This stack creates: 3 KMS CMKs, Aurora PostgreSQL r6g.large (Graviton, I/O-Optimized, Multi-AZ),
ElastiCache Redis r7g.medium, OpenSearch Serverless collection, S3 bucket, Secrets Manager secrets.

Wait for CREATE_COMPLETE (may take up to 20 minutes). Poll status:
aws cloudformation describe-stacks \
  --stack-name fortress-p1-data-${ENVIRONMENT:-production} \
  --query "Stacks[0].{Status:StackStatus}" --output text

Once complete, retrieve and display all outputs:
aws cloudformation describe-stacks \
  --stack-name fortress-p1-data-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[*].{Key:OutputKey,Value:OutputValue}" \
  --output table

Then verify each service is available:

1. Aurora cluster status:
   aws rds describe-db-clusters \
     --query "DBClusters[?contains(DBClusterIdentifier,'fortress-p1')].{ID:DBClusterIdentifier,Status:Status,Endpoint:Endpoint}" \
     --output table

2. ElastiCache status:
   aws elasticache describe-replication-groups \
     --query "ReplicationGroups[?contains(ReplicationGroupId,'fortress-p1')].{ID:ReplicationGroupId,Status:Status,Node:CacheNodeType}" \
     --output table

3. OpenSearch collection status:
   aws opensearchserverless list-collections \
     --query "collectionSummaries[?name=='fortress-compliance-docs'].{Name:name,Status:status}" \
     --output table

4. KMS keys created:
   aws kms list-aliases \
     --query "Aliases[?starts_with(AliasName,'alias/fortress-')].AliasName" \
     --output text

5. Secrets Manager secrets:
   aws secretsmanager list-secrets \
     --query "SecretList[?starts_with(Name,'fortress/p1')].Name" \
     --output text

If Aurora status is "available", ElastiCache is "available", and OpenSearch is "ACTIVE",
print: STEP 3 COMPLETE — all data services healthy.

If anything is not available, report the issue and wait.
```

---

## Prompt 4 — Create Aurora schema and enable pgvector

```
Set up the Aurora PostgreSQL schema. No Alembic migrations — clean install using Python.

Step 1: Get Aurora credentials from Secrets Manager and SSM.

AURORA_HOST=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-data-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraEndpoint'].OutputValue" \
  --output text)

DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id fortress/p1/db-password \
  --query "SecretString" --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")

echo "Aurora host: $AURORA_HOST"
echo "Password retrieved: ${#DB_PASSWORD} chars"

Step 2: Install asyncpg if not present:
pip3 install asyncpg --quiet

Step 3: Run this Python script to create the complete schema:

python3 << 'PYEOF'
import asyncio, asyncpg, os, boto3, json

async def setup_schema():
    region = os.environ.get("AWS_REGION", "us-east-1")

    # Get credentials
    sm = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(sm.get_secret_value(SecretId="fortress/p1/db-password")["SecretString"])
    password = secret["password"]

    ssm = boto3.client("ssm", region_name=region)
    host = ssm.get_parameter(Name="/fortress/pipeline1/AURORA_HOST")["Parameter"]["Value"]

    print(f"Connecting to: {host}")

    conn = await asyncpg.connect(
        host=host, port=5432,
        user="fortress_p1", password=password,
        database="fortress_p1", ssl="require"
    )

    # Extensions
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    print("Extensions: vector, pg_trgm, uuid-ossp installed")

    # Core compliance rules table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS compliance_rules (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_id         VARCHAR(200) NOT NULL UNIQUE,
            rule_text       TEXT,
            regulator       VARCHAR(50),
            regulation_name VARCHAR(200),
            cfr_citation    VARCHAR(200),
            jurisdiction    VARCHAR(20) DEFAULT 'federal',
            state_code      VARCHAR(20),
            doc_type        VARCHAR(50) DEFAULT 'regulation',
            is_obligation   BOOLEAN DEFAULT FALSE,
            effective_date  TIMESTAMPTZ,
            is_active       BOOLEAN DEFAULT TRUE,
            enforcement_risk_score FLOAT DEFAULT 0.0,
            obligation_count INTEGER DEFAULT 0,
            sync_version    BIGINT DEFAULT 0,
            content_hash    VARCHAR(64),
            source_url      TEXT,
            embedding       vector(1024),
            tsv             tsvector GENERATED ALWAYS AS (
                to_tsvector('english',
                    coalesce(rule_text,'') || ' ' ||
                    coalesce(cfr_citation,'') || ' ' ||
                    coalesce(regulation_name,''))
            ) STORED,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now()
        )
    """)

    # pgvector HNSW index
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cr_hnsw
        ON compliance_rules USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # BM25 GIN index
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_tsv ON compliance_rules USING GIN (tsv)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_rule_id ON compliance_rules (rule_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_regulator ON compliance_rules (regulator)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_sync_version ON compliance_rules (sync_version)")
    print("compliance_rules: table + indexes created")

    # Obligations
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS obligations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_id         VARCHAR(200),
            source_text     TEXT,
            obligation_type VARCHAR(100),
            is_active       BOOLEAN DEFAULT TRUE,
            sync_version    BIGINT DEFAULT 0,
            created_at      TIMESTAMPTZ DEFAULT now()
        )
    """)
    print("obligations: table created")

    # Enforcement
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS enforcement_actions (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            regulator   VARCHAR(50),
            action_type VARCHAR(100),
            penalty_usd NUMERIC(15,2),
            action_date DATE,
            description TEXT,
            is_active   BOOLEAN DEFAULT TRUE,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS enforcement_risk_scores (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            regulation_id VARCHAR(200) UNIQUE,
            risk_score    FLOAT DEFAULT 0.0,
            sync_version  BIGINT DEFAULT 0,
            updated_at    TIMESTAMPTZ DEFAULT now()
        )
    """)
    print("enforcement_actions + enforcement_risk_scores: created")

    # Deadlines
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS compliance_deadlines (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            regulation_id   VARCHAR(200),
            deadline_name   VARCHAR(300),
            deadline_date   TIMESTAMPTZ,
            days_remaining  INTEGER,
            is_active       BOOLEAN DEFAULT TRUE,
            sync_version    BIGINT DEFAULT 0,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now(),
            UNIQUE (regulation_id, deadline_name)
        )
    """)
    print("compliance_deadlines: created")

    # Change records
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS regulatory_change_records (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            change_ref     VARCHAR(100) NOT NULL UNIQUE,
            rule_id        VARCHAR(100),
            regulator      VARCHAR(50),
            change_summary TEXT,
            impact_rating  VARCHAR(20),
            change_type    VARCHAR(20),
            status         VARCHAR(20) DEFAULT 'detected',
            client_id      VARCHAR(100),
            is_active      BOOLEAN DEFAULT TRUE,
            sync_version   BIGINT DEFAULT 0,
            created_at     TIMESTAMPTZ DEFAULT now(),
            updated_at     TIMESTAMPTZ DEFAULT now()
        )
    """)
    print("regulatory_change_records: created")

    # Peer benchmarks
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS peer_benchmarks (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            regulator    VARCHAR(50),
            metric_name  VARCHAR(200),
            metric_value FLOAT,
            peer_group   VARCHAR(100),
            report_date  DATE,
            sync_version BIGINT DEFAULT 0,
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """)
    print("peer_benchmarks: created")

    # OFAC SDN
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ofac_sdn_entries (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sdn_name   VARCHAR(500),
            sdn_type   VARCHAR(100),
            program    VARCHAR(200),
            remarks    TEXT,
            is_active  BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (sdn_name, program)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ofac_name ON ofac_sdn_entries USING GIN (to_tsvector('english', sdn_name))")
    print("ofac_sdn_entries: created + GIN index")

    # Client credentials (bank registry)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS client_credentials (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id  VARCHAR(100) NOT NULL UNIQUE,
            bank_name    VARCHAR(200) NOT NULL,
            api_key_hash VARCHAR(64) NOT NULL,
            is_active    BOOLEAN DEFAULT TRUE,
            tier         VARCHAR(20) DEFAULT 'standard',
            created_at   TIMESTAMPTZ DEFAULT now(),
            last_seen_at TIMESTAMPTZ
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS ix_client_credentials_customer_id ON client_credentials (customer_id)")
    print("client_credentials: created")

    # Sync metadata
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_versions (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            version_num  BIGINT NOT NULL DEFAULT 0,
            updated_at   TIMESTAMPTZ DEFAULT now()
        )
    """)
    await conn.execute("INSERT INTO sync_versions (version_num) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM sync_versions)")
    print("sync_versions: created + seeded with version 0")

    # Deleted rules (for P2 cleanup)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_rules (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_id           VARCHAR(200),
            deleted_at_version BIGINT,
            deleted_at         TIMESTAMPTZ DEFAULT now()
        )
    """)
    print("deleted_rules: created")

    # Scrape runs (migration 018) — MCP scrape monitor audit trail
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_runs (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            connector_name   VARCHAR(100) NOT NULL,
            success          BOOLEAN NOT NULL,
            rules_extracted  INTEGER DEFAULT 0,
            rules_changed    INTEGER DEFAULT 0,
            error_message    TEXT DEFAULT '',
            duration_seconds FLOAT DEFAULT 0,
            s3_key           VARCHAR(500) DEFAULT '',
            created_at       TIMESTAMPTZ DEFAULT now()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sr_connector_run ON scrape_runs (connector_name, run_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sr_success ON scrape_runs (success, run_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sr_run_at ON scrape_runs (run_at DESC)")
    print("scrape_runs: created + indexes (migration 018)")

    # Stale embedding tracker column (migration 019)
    await conn.execute("""
        ALTER TABLE compliance_rules
        ADD COLUMN IF NOT EXISTS embed_updated_at TIMESTAMPTZ
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cr_embed_stale
        ON compliance_rules (updated_at, embed_updated_at)
        WHERE is_active = TRUE AND embedding IS NOT NULL
    """)
    print("compliance_rules.embed_updated_at: added + partial index (migration 019)")

    # Alembic version table (marks schema as managed at migration 019)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL CONSTRAINT alembic_version_pkc PRIMARY KEY
        )
    """)
    await conn.execute("INSERT INTO alembic_version SELECT '019' WHERE NOT EXISTS (SELECT 1 FROM alembic_version)")
    print("alembic_version: stamped at 019")

    # Final verification
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    embedding_col = await conn.fetchrow(
        "SELECT data_type FROM information_schema.columns WHERE table_name='compliance_rules' AND column_name='embedding'"
    )
    hnsw_idx = await conn.fetchrow(
        "SELECT indexname FROM pg_indexes WHERE indexname='idx_cr_hnsw'"
    )

    print(f"\n=== Schema verification ===")
    print(f"Tables created: {len(tables)}")
    print(f"pgvector embedding column: {'OK' if embedding_col else 'MISSING'}")
    print(f"HNSW index: {'OK' if hnsw_idx else 'MISSING'}")
    print(f"Tables: {', '.join(r['tablename'] for r in tables)}")

    await conn.close()
    print("\nSCHEMA SETUP COMPLETE")

asyncio.run(setup_schema())
PYEOF

If the script prints SCHEMA SETUP COMPLETE, print: STEP 4 COMPLETE — Aurora schema ready with pgvector.
If it fails, show the full error and suggest a fix.
```

---

## Prompt 5 — Create OpenSearch index mapping

```
Create the OpenSearch Serverless compliance documents index with BM25 and k-NN vector mapping.

Step 1: Get the OpenSearch endpoint:
OPENSEARCH_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-data-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[?OutputKey=='OpenSearchEndpoint'].OutputValue" \
  --output text)

echo "OpenSearch endpoint: $OPENSEARCH_ENDPOINT"

Step 2: Install opensearch-py if not present:
pip3 install opensearch-py requests-aws4auth boto3 --quiet

(Data access policy is provisioned by the data CFN stack — both the ECS task role
and the account root principal already have aoss:CreateIndex / aoss:WriteDocument
permissions, so the index can be created from a developer machine using the
fortress-prod AWS profile.)

Step 3: Create the index:

python3 << 'PYEOF'
import boto3, json, os
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

region = os.environ.get("AWS_REGION", "us-east-1")
credentials = boto3.Session().get_credentials()
auth = AWSV4SignerAuth(credentials, region, "aoss")

import subprocess
endpoint = subprocess.run(
    ["aws", "cloudformation", "describe-stacks",
     "--stack-name", f"fortress-p1-data-{os.environ.get('ENVIRONMENT','production')}",
     "--query", "Stacks[0].Outputs[?OutputKey=='OpenSearchEndpoint'].OutputValue",
     "--output", "text"],
    capture_output=True, text=True
).stdout.strip().replace("https://", "")

print(f"Creating index at: {endpoint}")

client = OpenSearch(
    hosts=[{"host": endpoint, "port": 443}],
    http_auth=auth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30,
)

index_body = {
    "settings": {
        "index": {
            "knn": True,
            "knn.algo_param.ef_search": 100,
            "number_of_shards": 2,
            "number_of_replicas": 1
        }
    },
    "mappings": {
        "properties": {
            "rule_id":              {"type": "keyword"},
            "rule_text":            {"type": "text", "analyzer": "english"},
            "cfr_citation":         {"type": "keyword"},
            "regulator":            {"type": "keyword"},
            "regulation_name":      {"type": "keyword"},
            "jurisdiction":         {"type": "keyword"},
            "state_code":           {"type": "keyword"},
            "doc_type":             {"type": "keyword"},
            "is_obligation":        {"type": "boolean"},
            "obligation_type":      {"type": "keyword"},
            "effective_date":       {"type": "date"},
            "enforcement_risk":     {"type": "float"},
            "content_hash":         {"type": "keyword"},
            "sync_version":         {"type": "long"},
            "is_active":            {"type": "boolean"},
            "embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "faiss",
                    "parameters": {"ef_construction": 128, "m": 16}
                }
            }
        }
    }
}

index_name = "fortress-compliance-docs"
if not client.indices.exists(index_name):
    response = client.indices.create(index_name, body=index_body)
    print(f"Index created: {response}")
else:
    print(f"Index already exists: {index_name}")

info = client.indices.get(index_name)
props = info[index_name]["mappings"]["properties"]
print(f"Fields mapped: {list(props.keys())}")
print(f"Embedding dim: {props['embedding']['method']['parameters']}")
print("\nOPENSEARCH INDEX READY")
PYEOF

If it prints OPENSEARCH INDEX READY, print: STEP 5 COMPLETE — OpenSearch index created.
If it fails with a 403 error, the data access policy needs to be updated — report the error.
```

---

## Prompt 6 — Build and push Pipeline 1 Docker image

```
Build the Pipeline 1 Docker image and push it to ECR.

Step 1: Get account ID and set ECR URI:
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION:-us-east-1}.amazonaws.com/fortress/pipeline1"
IMAGE_TAG="${IMAGE_TAG:-v5.0}"

echo "ECR URI: $ECR_URI"
echo "Image tag: $IMAGE_TAG"

Step 2: Create ECR repository if it does not exist:
aws ecr create-repository \
  --repository-name fortress/pipeline1 \
  --image-scanning-configuration scanOnPush=true \
  --region ${AWS_REGION:-us-east-1} 2>/dev/null && echo "ECR repo created" || echo "ECR repo already exists"

Step 3: Login to ECR:
aws ecr get-login-password \
  --region ${AWS_REGION:-us-east-1} | \
  docker login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION:-us-east-1}.amazonaws.com"

Step 4: Build the image (for linux/amd64 — required for ECS Fargate):
docker build \
  --platform linux/amd64 \
  -f Dockerfile.pipeline1 \
  -t fortress/pipeline1:${IMAGE_TAG} \
  -t fortress/pipeline1:latest \
  --build-arg BUILDKIT_INLINE_CACHE=1 \
  .

Step 5: Tag and push:
docker tag fortress/pipeline1:${IMAGE_TAG} ${ECR_URI}:${IMAGE_TAG}
docker tag fortress/pipeline1:${IMAGE_TAG} ${ECR_URI}:latest
docker push ${ECR_URI}:${IMAGE_TAG}
docker push ${ECR_URI}:latest

Step 6: Verify the image is in ECR:
aws ecr describe-images \
  --repository-name fortress/pipeline1 \
  --query "imageDetails[*].{Tag:imageTags[0],Pushed:imagePushedAt,Size:imageSizeInBytes}" \
  --output table

Step 7: Store the image tag in SSM so ECS can reference it:
aws ssm put-parameter \
  --name /fortress/pipeline1/LATEST_IMAGE_TAG \
  --value "${IMAGE_TAG}" \
  --type String \
  --overwrite

If the image appears in ECR, print: STEP 6 COMPLETE — Image pushed: ${ECR_URI}:${IMAGE_TAG}
If docker build fails, check Dockerfile.pipeline1 exists and Docker daemon is running.
```

---

## Prompt 7 — Deploy compute stack (ECS + ALB + WAF + EventBridge)

```
Deploy the Pipeline 1 compute stack. Before running, we need the Route53 hosted zone ID.

Step 1: Look up hosted zone for the domain:
HOSTED_ZONE=$(aws route53 list-hosted-zones \
  --query "HostedZones[?Name=='${DOMAIN_NAME:-fortresslabs.dev}.'].[Id,Name]" \
  --output text)

echo "Hosted zones found:"
echo "$HOSTED_ZONE"

If no hosted zone is found for ${DOMAIN_NAME:-fortresslabs.dev}, ask the user to create one
in Route53 first and provide the zone ID before continuing.

Step 2: Extract zone ID (strip /hostedzone/ prefix):
ZONE_ID=$(echo "$HOSTED_ZONE" | awk '{print $1}' | sed 's|/hostedzone/||')
echo "Zone ID: $ZONE_ID"

Step 3: Store zone ID and Bedrock model in SSM:
aws ssm put-parameter \
  --name /fortress/pipeline1/HOSTED_ZONE_ID \
  --value "$ZONE_ID" \
  --type String --overwrite

aws ssm put-parameter \
  --name /fortress/pipeline1/BEDROCK_MODEL \
  --value "anthropic.claude-sonnet-4-20250514" \
  --type String --overwrite

aws ssm put-parameter \
  --name /fortress/pipeline1/PIPELINE_MODE \
  --value "pipeline1" \
  --type String --overwrite

echo "SSM parameters stored"

Step 4: Get image tag from SSM:
IMAGE_TAG=$(aws ssm get-parameter \
  --name /fortress/pipeline1/LATEST_IMAGE_TAG \
  --query Parameter.Value --output text)

echo "Deploying image tag: $IMAGE_TAG"

Step 5: Deploy compute stack:
aws cloudformation deploy \
  --region ${AWS_REGION:-us-east-1} \
  --profile ${AWS_PROFILE:-fortress-prod} \
  --stack-name fortress-p1-compute-${ENVIRONMENT:-production} \
  --template-file infra/cloudformation/pipeline1-compute.yml \
  --parameter-overrides \
    Environment=${ENVIRONMENT:-production} \
    ImageTag=${IMAGE_TAG} \
    DomainName=${DOMAIN_NAME:-fortresslabs.dev} \
    AlertEmail=${ALERT_EMAIL:-ops@fortresslabs.dev} \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

Step 6: Wait for stack to complete and show outputs:
aws cloudformation describe-stacks \
  --stack-name fortress-p1-compute-${ENVIRONMENT:-production} \
  --query "Stacks[0].{Status:StackStatus,Outputs:Outputs[*]}" \
  --output json

Step 7: Check ECS service is running:
aws ecs describe-services \
  --cluster fortress-pipeline1-${ENVIRONMENT:-production} \
  --services fortress-pipeline1-api-${ENVIRONMENT:-production} \
  --query "services[0].{Running:runningCount,Desired:desiredCount,Status:status,Health:healthCheckGracePeriodSeconds}" \
  --output table

Step 8: Get the ALB DNS name and tell the user what CNAME to create:
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-compute-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDns'].OutputValue" \
  --output text)

echo ""
echo "=== ACTION REQUIRED ==="
echo "Create this DNS record at your domain registrar:"
echo "  Type: CNAME"
echo "  Name: api.${DOMAIN_NAME:-fortresslabs.dev}"
echo "  Value: $ALB_DNS"
echo ""
echo "ACM will auto-validate once DNS propagates (5-10 min)."
echo "ECS service will show Running=1 once the health check passes."

Print: STEP 7 COMPLETE — Compute stack deployed. Create the CNAME above then proceed.
```

---

## Prompt 8 — Deploy security stack, enable scraper, verify full system

```
Final step: deploy security stack, enable EventBridge scheduler, run first scrape.

Step 1: Get S3 bucket name from data stack:
S3_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-data-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text)

echo "S3 bucket: $S3_BUCKET"

Step 2: Deploy security stack:
aws cloudformation deploy \
  --region ${AWS_REGION:-us-east-1} \
  --profile ${AWS_PROFILE:-fortress-prod} \
  --stack-name fortress-p1-security-${ENVIRONMENT:-production} \
  --template-file infra/cloudformation/pipeline1-security.yml \
  --parameter-overrides \
    Environment=${ENVIRONMENT:-production} \
    S3BucketName=${S3_BUCKET} \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

Step 3: Check ECS service health (wait for Running=1):
for i in 1 2 3 4 5; do
  STATUS=$(aws ecs describe-services \
    --cluster fortress-pipeline1-${ENVIRONMENT:-production} \
    --services fortress-pipeline1-api-${ENVIRONMENT:-production} \
    --query "services[0].{Running:runningCount,Desired:desiredCount}" \
    --output json)
  echo "Check $i: $STATUS"
  RUNNING=$(echo $STATUS | python3 -c "import sys,json; print(json.load(sys.stdin)['Running'])")
  if [ "$RUNNING" = "1" ]; then
    echo "ECS service healthy"
    break
  fi
  sleep 30
done

Step 4: Test the sync API health endpoint via ALB DNS (before CNAME propagates):
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-compute-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDns'].OutputValue" \
  --output text)

curl -sf http://${ALB_DNS}/health && echo "Health check via ALB DNS: OK" || echo "Health check failed — ECS may still be starting"

Step 5: Enable the EventBridge scheduler (was created DISABLED for safety):
SCHEDULE_ARN=$(aws scheduler list-schedules \
  --query "Schedules[?Name=='fortress-p1-daily-scrape-${ENVIRONMENT:-production}'].Arn" \
  --output text)

aws scheduler update-schedule \
  --name fortress-p1-daily-scrape-${ENVIRONMENT:-production} \
  --state ENABLED \
  --schedule-expression "cron(0 2 * * ? *)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "$(aws scheduler get-schedule \
    --name fortress-p1-daily-scrape-${ENVIRONMENT:-production} \
    --query Target --output json)"

echo "EventBridge scheduler: ENABLED — will run daily at 02:00 UTC"

Step 6: Trigger the FIRST scrape manually right now (do not wait until 02:00):
TASK_DEF=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-compute-${ENVIRONMENT:-production} \
  --query "Stacks[0].Outputs[?OutputKey=='EcsTaskDefArn'].OutputValue" \
  --output text)

SUBNET=$(aws cloudformation list-exports \
  --query "Exports[?Name=='FortressP1-${ENVIRONMENT:-production}-PrivateSubnetA'].Value" \
  --output text)

SG=$(aws cloudformation list-exports \
  --query "Exports[?Name=='FortressP1-${ENVIRONMENT:-production}-SgEcs'].Value" \
  --output text)

echo "Launching scraper task..."
TASK_ARN=$(aws ecs run-task \
  --cluster fortress-pipeline1-${ENVIRONMENT:-production} \
  --task-definition "$TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --query "tasks[0].taskArn" \
  --output text)

echo "Scraper task launched: $TASK_ARN"
echo "Watching CloudWatch logs (Ctrl+C to stop):"
aws logs tail /fortress/pipeline1 --follow --since 1m

Step 7: After 5 minutes, verify rules are in Aurora:
python3 << 'PYEOF'
import asyncio, asyncpg, boto3, json, os

async def check():
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION","us-east-1"))
    pw = json.loads(sm.get_secret_value(SecretId="fortress/p1/db-password")["SecretString"])["password"]
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION","us-east-1"))
    host = ssm.get_parameter(Name="/fortress/pipeline1/AURORA_HOST")["Parameter"]["Value"]
    conn = await asyncpg.connect(host=host, port=5432, user="fortress_p1",
                                  password=pw, database="fortress_p1", ssl="require")
    rules = await conn.fetchval("SELECT COUNT(*) FROM compliance_rules")
    embedded = await conn.fetchval("SELECT COUNT(*) FROM compliance_rules WHERE embedding IS NOT NULL")
    deadlines = await conn.fetchval("SELECT COUNT(*) FROM compliance_deadlines")
    print(f"compliance_rules total: {rules}")
    print(f"compliance_rules with embeddings: {embedded}")
    print(f"compliance_deadlines: {deadlines}")
    await conn.close()
asyncio.run(check())
PYEOF

Step 8: Print final status summary:
echo ""
echo "======================================================"
echo "  PIPELINE 1 SETUP COMPLETE"
echo "======================================================"
echo ""
echo "  Stacks deployed:"
aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'fortress-p1')].{Name:StackName,Status:StackStatus}" \
  --output table
echo ""
echo "  Sync API: https://api.${DOMAIN_NAME:-fortresslabs.dev}/health"
echo "  Scraper: EventBridge fires daily at 02:00 UTC"
echo ""
echo "  Next: register pilot banks using:"
echo "  ./scripts/register-bank.sh --bank-id <id> --bank-name <name> --bank-ip <nat-eip> --bank-profile <aws-profile>"
echo "======================================================"

Print: PIPELINE 1 FULLY OPERATIONAL
```
