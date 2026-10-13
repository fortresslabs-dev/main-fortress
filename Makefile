# Fortress Labs — Makefile
# Single entry point for local dev, testing, and deployment.
# Usage: make <target>

.PHONY: help install p1 p2 p1-debug p2-debug test test-demo lint migrate \
        build-p1 build-p2 push-p1 push-p2 deploy-p1 deploy-p2-bank \
        schema-check logs-p1 logs-p2 clean up down \
        extraction-eval extraction-backfill-penalties \
        demo-profile-up demo-profile-down \
        demo-seed demo-reset demo-reset-s3 demo-dryrun

# ── Defaults ──────────────────────────────────────────────────────────
# BANK_ID drives every per-bank P2 path (ECR repo, ECS cluster, SSM prefix,
# RDS instance id). Default is `testbank` per memory project_p2_test_bank_id —
# matches the CFN stack names used by the demo deploy (./deploy.sh testbank
# production). Override per pilot bank: `make push-p2 BANK_ID=firstnational`.
BANK_ID      ?= testbank
IMAGE_TAG    ?= dev
AWS_REGION   ?= us-east-1
AWS_PROFILE  ?= fortress-prod
P1_ENV       ?= production
ACCOUNT_ID   := $(shell aws sts get-caller-identity --profile $(AWS_PROFILE) --query Account --output text 2>/dev/null)
ECR_BASE     := $(ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com

# ── Help ──────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  Fortress Labs — make targets"
	@echo ""
	@echo "  Local development:"
	@echo "    make install        Install Python dependencies"
	@echo "    make up             Start postgres + redis (docker compose)"
	@echo "    make down           Stop docker compose"
	@echo "    make migrate        Run all Alembic migrations"
	@echo "    make p1             Run Pipeline 1 (hot-reload)"
	@echo "    make p2             Run Pipeline 2 (hot-reload)"
	@echo "    make p1-debug       Run P1 with debug logging"
	@echo "    make p2-debug       Run P2 with debug logging"
	@echo "    make schema-check   Verify all 17 migrations + key columns"
	@echo "    make audit-urls     Probe every connector _DEFAULT_URLS for 404/rot"
	@echo "    make extraction-eval"
	@echo "                        Run extraction accuracy harness against golden set"
	@echo "    make extraction-backfill-penalties"
	@echo "                        Re-extract penalty_amount_usd on suspect enforcement_actions rows"
	@echo ""
	@echo "  Testing:"
	@echo "    make test           Run full test suite"
	@echo "    make test-p1        Run P1 tests only"
	@echo "    make test-p2        Run P2 tests only"
	@echo "    make test-demo      Run demo-readiness API contract suite (<90s)"
	@echo "    make lint           ruff + black check"
	@echo ""
	@echo "  Docker:"
	@echo "    make build-p1       Build Pipeline 1 image"
	@echo "    make build-p2       Build Pipeline 2 image (strips P1 code)"
	@echo "    make build-both     Build both images"
	@echo ""
	@echo "  Deploy — Pipeline 1 (Fortress account):"
	@echo "    make push-p1        Push P1 image to Fortress ECR"
	@echo "    make deploy-p1      Rolling deploy P1 ECS service"
	@echo ""
	@echo "  Deploy — Pipeline 2 (bank account):"
	@echo "    make push-p2        Push P2 image to bank ECR   BANK_ID=firstnational"
	@echo "    make deploy-p2-bank Deploy P2 ECS at bank        BANK_ID=firstnational"
	@echo ""
	@echo "  Demo fixtures + reset (see docs/runbooks/demo-readiness.md §4):"
	@echo "    make demo-seed          Seed testbank-demo state locally (idempotent)"
	@echo "    make demo-reset         Truncate volatile + reseed (target <90s)"
	@echo "    make demo-reset-s3      pg_restore baseline.dump from S3 + reseed"
	@echo "    make demo-dryrun        Timed pre-demo dry run (§6 checklist + report)"
	@echo ""
	@echo "  Demo profile (per-bank P2 RDS resize, in-place):"
	@echo "    make demo-profile-up    BANK_ID=testbank  Bump P2 RDS to demo size"
	@echo "    make demo-profile-down  BANK_ID=testbank  Revert P2 RDS to pilot size"
	@echo ""
	@echo "  Logs:"
	@echo "    make logs-p1        Tail P1 CloudWatch logs"
	@echo "    make logs-p2        Tail P2 CloudWatch logs      BANK_ID=firstnational"
	@echo ""

# ── Local services ────────────────────────────────────────────────────
up:
	docker compose up -d postgres redis
	@echo "✓ postgres (pgvector:pg15) + redis running"
	@echo "  postgres: localhost:5432  redis: localhost:6379"

down:
	docker compose down

install:
	pip install -r requirements.txt -r requirements-dev.txt
	@echo "✓ Dependencies installed"

migrate: up
	@sleep 3
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	python -m alembic upgrade head
	@echo "✓ All migrations applied"

schema-check:
	@DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	python scripts/verify_schema.py

audit-urls:
	@python3 scripts/audit_source_urls.py

# ── Extraction (LLM + Pydantic) ───────────────────────────────────────
extraction-eval:
	@python3 -m scripts.eval_extraction

# Re-extracts penalty_amount_usd on rows above $1B (very rare for real
# penalties — almost all such rows are the asset-confusion bug). Pass
# DRY_RUN=1 to preview without writing. Requires Bedrock credentials.
extraction-backfill-penalties:
	@DRY_RUN=$${DRY_RUN:-1} python3 -m scripts.backfill_enforcement_penalties

# ── Run pipelines locally ─────────────────────────────────────────────
p1: up
	@echo "Starting Pipeline 1 on http://localhost:8001"
	PIPELINE_MODE=pipeline1 \
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://:fortress_dev@localhost:6379" \
	LLM_MOCK_MODE=true \
	uvicorn src.main_pipeline1:app --reload --host 0.0.0.0 --port 8001

p2: up
	@echo "Starting Pipeline 2 on http://localhost:8002"
	PIPELINE_MODE=pipeline2 \
	CLIENT_NAME=devbank \
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://:fortress_dev@localhost:6379" \
	PIPELINE1_API_URL=http://localhost:8001 \
	LLM_MOCK_MODE=true \
	uvicorn src.main_pipeline2:app --reload --host 0.0.0.0 --port 8002

p1-debug: up
	PIPELINE_MODE=pipeline1 \
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://:fortress_dev@localhost:6379" \
	LLM_MOCK_MODE=true \
	LOG_LEVEL=debug \
	uvicorn src.main_pipeline1:app --reload --host 0.0.0.0 --port 8001 --log-level debug

p2-debug: up
	PIPELINE_MODE=pipeline2 \
	CLIENT_NAME=devbank \
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://:fortress_dev@localhost:6379" \
	PIPELINE1_API_URL=http://localhost:8001 \
	LLM_MOCK_MODE=true \
	LOG_LEVEL=debug \
	uvicorn src.main_pipeline2:app --reload --host 0.0.0.0 --port 8002 --log-level debug

# ── Testing ───────────────────────────────────────────────────────────
test: up
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://:fortress_dev@localhost:6379" \
	LLM_MOCK_MODE=true \
	pytest tests/ -v --tb=short

test-p1: up
	PIPELINE_MODE=pipeline1 \
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	LLM_MOCK_MODE=true \
	pytest tests/pipeline1/ -v --tb=short

test-p2: up
	PIPELINE_MODE=pipeline2 \
	CLIENT_NAME=devbank \
	DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
	LLM_MOCK_MODE=true \
	pytest tests/pipeline2/ -v --tb=short

# Demo-readiness API contract suite. See docs/runbooks/demo-readiness.md §2.
# Real Postgres + Redis required (run `make up` first). Target wall: <90s.
test-demo: up
	@sleep 2
	PIPELINE_MODE=pipeline2 \
	CLIENT_NAME=testbank-demo \
	ENVIRONMENT=test \
	DATABASE_URL="postgresql://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://:fortress_dev@localhost:6379/0" \
	PIPELINE2_API_TOKEN=test-dev-token \
	JIRA_WEBHOOK_SECRET=test-webhook-secret \
	LLM_MOCK_MODE=true \
	pytest tests/integration/api/ -v --tb=short --maxfail=3

lint:
	ruff check src/ --fix
	black src/
	python -m py_compile src/main_pipeline1.py
	python -m py_compile src/main_pipeline2.py
	python -m py_compile src/utils/config.py
	@echo "✓ Lint passed"

# ── Docker build ──────────────────────────────────────────────────────
build-p1:
	docker build -f Dockerfile.pipeline1 \
	  -t fortress/pipeline1:$(IMAGE_TAG) \
	  -t fortress/pipeline1:latest .
	@echo "✓ Pipeline 1 image built: fortress/pipeline1:$(IMAGE_TAG)"

build-p2:
	docker build -f Dockerfile.pipeline2 \
	  -t fortress/pipeline2:$(IMAGE_TAG) \
	  -t fortress/pipeline2:latest .
	@echo "✓ Pipeline 2 image built (P1 code stripped)"

build-both: build-p1 build-p2

# ── Push to ECR ───────────────────────────────────────────────────────
ecr-login:
	aws ecr get-login-password --region $(AWS_REGION) --profile $(AWS_PROFILE) | \
	  docker login --username AWS --password-stdin $(ECR_BASE)

push-p1: ecr-login build-p1
	docker tag fortress/pipeline1:$(IMAGE_TAG) $(ECR_BASE)/fortress/pipeline1:$(IMAGE_TAG)
	docker push $(ECR_BASE)/fortress/pipeline1:$(IMAGE_TAG)
	@echo "✓ Pushed: $(ECR_BASE)/fortress/pipeline1:$(IMAGE_TAG)"

push-p2: ecr-login build-p2
	@echo "Pushing P2 image for bank: $(BANK_ID)"
	docker tag fortress/pipeline2:$(IMAGE_TAG) $(ECR_BASE)/fortress/pipeline2-$(BANK_ID):$(IMAGE_TAG)
	docker push $(ECR_BASE)/fortress/pipeline2-$(BANK_ID):$(IMAGE_TAG)
	@echo "✓ Pushed: $(ECR_BASE)/fortress/pipeline2-$(BANK_ID):$(IMAGE_TAG)"

# ── ECS Deploy ────────────────────────────────────────────────────────
deploy-p1: push-p1
	aws ecs update-service \
	  --profile $(AWS_PROFILE) \
	  --region $(AWS_REGION) \
	  --cluster fortress-pipeline1-production \
	  --service fortress-pipeline1-api-production \
	  --force-new-deployment \
	  --query "service.{Status:status,Running:runningCount}" \
	  --output table
	@echo "✓ P1 ECS rolling deploy triggered"

deploy-p2-bank: push-p2
	@echo "Deploying P2 to bank: $(BANK_ID)"
	AWS_PROFILE=bank-$(BANK_ID) aws ecs update-service \
	  --region $(AWS_REGION) \
	  --cluster fortress-pipeline2-$(BANK_ID) \
	  --service fortress-pipeline2-$(BANK_ID) \
	  --force-new-deployment \
	  --query "service.{Status:status,Running:runningCount}" \
	  --output table
	@echo "✓ P2 ECS rolling deploy triggered for $(BANK_ID)"

# ── CloudWatch logs ───────────────────────────────────────────────────
logs-p1:
	aws logs tail /fortress/pipeline1 --follow --profile $(AWS_PROFILE) --region $(AWS_REGION)

logs-p2:
	aws logs tail /fortress/pipeline2/$(BANK_ID) --follow \
	  --profile bank-$(BANK_ID) --region $(AWS_REGION)

# ── Demo-profile DB resize (P2 only) ──────────────────────────────────
# P1 Aurora ACU floor is set in the CFN template (2–16 ACU baseline) — the
# shared cluster only ever needs more capacity as we onboard more banks, so
# there's no revert step for P1. If a demo needs a temporary further bump on
# top of the multi-tenant baseline, do it via `aws rds modify-db-cluster`
# directly and remember to ratchet the CFN floor up rather than back down.
#
# P2 is per-bank and tears down with the demo stack, so the bump-and-revert
# pattern is appropriate there. Override BANK_ID / P1_ENV / P2_DB_INSTANCE if
# the deployed instance id doesn't match the default naming.
P2_DB_INSTANCE     ?= fortress-p2-pg-$(BANK_ID)-$(P1_ENV)
DEMO_P2_INSTANCE   ?= db.t4g.large
PILOT_P2_INSTANCE  ?= db.t4g.medium

demo-profile-up:
	@echo "P2: bumping $(P2_DB_INSTANCE) to $(DEMO_P2_INSTANCE)"
	AWS_PROFILE=bank-$(BANK_ID) aws rds modify-db-instance \
	  --region $(AWS_REGION) \
	  --db-instance-identifier $(P2_DB_INSTANCE) \
	  --db-instance-class $(DEMO_P2_INSTANCE) \
	  --apply-immediately \
	  --query "DBInstance.{Class:DBInstanceClass,Status:DBInstanceStatus}" --output table
	@echo "✓ P2 demo profile applied for $(BANK_ID)."
	@echo "  REVERT after demo: make demo-profile-down BANK_ID=$(BANK_ID)"

demo-profile-down:
	@echo "P2: reverting $(P2_DB_INSTANCE) to $(PILOT_P2_INSTANCE)"
	AWS_PROFILE=bank-$(BANK_ID) aws rds modify-db-instance \
	  --region $(AWS_REGION) \
	  --db-instance-identifier $(P2_DB_INSTANCE) \
	  --db-instance-class $(PILOT_P2_INSTANCE) \
	  --apply-immediately \
	  --query "DBInstance.{Class:DBInstanceClass,Status:DBInstanceStatus}" --output table
	@echo "✓ P2 pilot profile restored for $(BANK_ID)"

# ── Demo seed + reset (see docs/runbooks/demo-readiness.md §4) ────────
# The seed is idempotent — run it twice in a row and the DB is identical.
# Reset truncates volatile tables then re-seeds; S3 reset restores a known
# baseline dump first. Both require docker-compose Postgres up (make up).
demo-seed: up
	@sleep 2
	DATABASE_URL="postgresql://fortress:fortress_dev@localhost:5432/fortress_dev" \
	REDIS_URL="redis://localhost:6379/0" \
	python3 -m scripts.demo.seed

demo-reset: up
	@sleep 2
	./scripts/demo/reset.sh --local

demo-reset-s3: up
	@sleep 2
	./scripts/demo/reset.sh --s3

# Scripted pre-demo dry run — implements demo-readiness.md §6.
# Times make demo-reset + every Playwright F0–F5 journey against the §6
# budgets, then prints a pass/fail report. Override E2E_BASE_URL for
# staging, or DEMO_BUDGET_* to retune budgets.
demo-dryrun:
	./scripts/demo/dryrun.sh

# ── Clean ─────────────────────────────────────────────────────────────
clean:
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	@echo "✓ Cleaned"
