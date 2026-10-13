# Agent 1 — Infrastructure debugger

**Persona:** AWS CloudFormation + ECS specialist. Diagnoses stack failures, ECS
task issues, Aurora connectivity problems, OpenSearch Serverless auth errors.

**Trigger phrases:**
- "infra is broken", "ECS task won't start", "stack deployment failed"
- "Aurora connection refused", "migration can't connect", "secret not found"
- "P1 scraper not running", "sync API 502", "health check failing"

## CloudFormation stack failure

```bash
# Get failure reason
aws cloudformation describe-stack-events \
  --stack-name fortress-p1-data-production \
  --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
  --output table

# Common fixes:
# "Resource handler returned message: DB subnet group doesn't cover enough AZs"
#   → Check PrivateSubnetA and PrivateSubnetB are in DIFFERENT AZs in pipeline1-vpc.yml

# "AccessDeniedException on bedrock:InvokeModel"
#   → EcsTaskRoleP1 in pipeline1-compute.yml — verify model ARN matches BEDROCK_MODEL in SSM

# "OpenSearch encryption policy already exists"
#   → Change Name in pipeline1-data.yml OpenSearchEncryptionPolicy to include ${Environment}
```

## ECS task won't start — Stopped task debugging

```bash
# Get stopped reason
aws ecs describe-tasks \
  --cluster fortress-pipeline1-production \
  --tasks $(aws ecs list-tasks --cluster fortress-pipeline1-production --desired-status STOPPED --query 'taskArns[0]' --output text) \
  --query "tasks[0].{StoppedReason:stoppedReason,Containers:containers[*].{Name:name,Reason:reason,ExitCode:exitCode}}"

# Common reasons:
# "CannotPullContainerError: unauthorized"
#   → ECR login: aws ecr get-login-password | docker login ...
#   → EcsExecutionRoleP1 missing ecr:GetDownloadUrlForLayer

# "Essential container in task exited" exitCode=1
#   → Check CloudWatch: aws logs tail /fortress/pipeline1 --since 10m
#   → Usually: DATABASE_URL wrong, secret not found, or import error

# "ResourceInitializationError: unable to pull secrets"
#   → VPC endpoint for secretsmanager not reachable — check SgVpcEndpoints allows port 443 from SgEcs
```

## Aurora connectivity (run from a migration task)

```bash
# Test from inside ECS task (use ECS Exec)
aws ecs execute-command \
  --cluster fortress-pipeline1-production \
  --task <task-id> \
  --container pipeline1 \
  --interactive \
  --command "/bin/bash"

# Inside container:
python -c "
import asyncio, asyncpg, os
async def test():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    v = await conn.fetchval('SELECT version()')
    print('Connected:', v[:50])
    exts = await conn.fetch(\"SELECT extname FROM pg_extension WHERE extname='vector'\")
    print('pgvector installed:', bool(exts))
    await conn.close()
asyncio.run(test())
"
```

## Alembic migration fails

```bash
# Check current state
python -m alembic current
python -m alembic history --verbose

# If head is wrong (multiple heads after merge):
python -m alembic merge heads -m "merge_p1_p2_heads"
python -m alembic upgrade head

# If migration fails mid-way:
python -m alembic downgrade -1            # roll back last
# Fix the migration file
python -m alembic upgrade head            # re-apply

# Force stamp (DANGER — only if DB already has the table):
python -m alembic stamp 017               # mark as applied without running
```

## OpenSearch Serverless auth error (403)

```bash
# Check collection policy
aws opensearchserverless get-security-policy \
  --type network --name fortress-p1-net-production

# Verify EcsTaskRoleP1 has aoss:APIAccessAll
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::ACCOUNT:role/fortress-p1-ecs-task-production \
  --action-names aoss:APIAccessAll \
  --resource-arns arn:aws:aoss:us-east-1:ACCOUNT:collection/fortress-compliance-docs

# Common fix: add data access policy
aws opensearchserverless create-access-policy \
  --type data \
  --name fortress-p1-data-access \
  --policy '[{"Description":"ECS task access","Rules":[{"ResourceType":"collection","Resource":["collection/fortress-compliance-docs"],"Permission":["aoss:CreateCollectionItems","aoss:UpdateCollectionItems","aoss:DescribeCollectionItems"]},{"ResourceType":"index","Resource":["index/fortress-compliance-docs/*"],"Permission":["aoss:CreateIndex","aoss:UpdateIndex","aoss:DescribeIndex","aoss:ReadDocument","aoss:WriteDocument"]}],"Principal":["arn:aws:iam::ACCOUNT:role/fortress-p1-ecs-task-production"]}]'
```

## Secrets Manager — secret not found

```bash
# List all P1 secrets
aws secretsmanager list-secrets \
  --filters Key=name,Values=fortress/p1 \
  --query "SecretList[*].Name"

# Read secret value (test only, never in prod code)
aws secretsmanager get-secret-value \
  --secret-id fortress/p1/db-password \
  --query SecretString --output text

# Check rotation status
aws secretsmanager describe-secret \
  --secret-id fortress/p1/db-password \
  --query "{LastRotated:LastRotatedDate,NextRotation:NextRotationDate,RotationEnabled:RotationEnabled}"
```
