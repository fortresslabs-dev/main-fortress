# Getting Started with TEXT MIND

TEXT MIND uses a dual-pipeline architecture. **Pipeline 1** runs in Fortress Cloud (scraping + sync API). **Pipeline 2** runs inside the customer's VPC (validation + test automation). Both are deployed as Docker containers.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Docker & Docker Compose v2 | [Install Docker](https://docs.docker.com/get-docker/) |
| Python 3.11+ | For local development |
| AWS Bedrock | Claude model access enabled |
| JIRA instance | For Pipeline 2 test automation |

---

## Option A: Deploy Pipeline 1 (Fortress Cloud)

Pipeline 1 scrapes regulatory sources and serves compliance rules via sync API.

### 1. Clone and Configure

```bash
git clone https://github.com/your-org/textmind_banking_backend.git
cd textmind_banking_backend

cat > .env << 'EOF'
RULES_DB_PASSWORD=<strong_password>
FORTRESS_INTERNAL_SECRET=<random_32_char_secret>
AWS_REGION=us-east-1
EOF
```

### 2. Start Services

```bash
docker compose -f docker-compose.pipeline1.yml up -d --build
```

This starts PostgreSQL, Redis, Weaviate, and the Pipeline 1 API.

### 3. Run Migrations and Seed

```bash
docker exec -it fortress-pipeline1 alembic upgrade head
docker exec -it fortress-pipeline1 python -m scripts.seed_weaviate
```

### 4. Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","pipeline":"fortress-cloud"}
```

The ScraperScheduler runs automatically in the background, scraping regulatory sources every 24 hours.

---

## Option B: Deploy Pipeline 2 (Customer VPC)

Pipeline 2 validates transactions locally and syncs compliance rules from Fortress Cloud.

### 1. Configure

```bash
cat > .env << 'EOF'
CUSTOMER_DB_PASSWORD=<strong_password>
FORTRESS_API_URL=https://<pipeline1-host>:8000
FORTRESS_API_KEY=<your_api_key>
CUSTOMER_ID=<your_customer_id>
JIRA_URL=https://your-company.atlassian.net
JIRA_EMAIL=automation@your-company.com
JIRA_API_TOKEN=<your_jira_token>
EOF
```

### 2. Start Services

```bash
docker compose -f docker-compose.pipeline2.yml up -d --build
```

This starts PostgreSQL, Redis, Weaviate, and the Pipeline 2 API.

### 3. Run Migrations

```bash
docker exec -it customer-pipeline2 alembic upgrade head
```

### 4. Verify

```bash
# Health check
curl http://localhost:8000/health

# Sync status (first sync runs ~15 seconds after boot)
curl http://localhost:8000/health/sync
```

### 5. Test Validation Endpoints

```bash
# Wire transfer validation
curl -s -X POST http://localhost:8000/validate/wire-transfer \
  -H "Content-Type: application/json" \
  -d '{"amount": 15000, "currency": "USD", "sender_name": "Alice", "beneficiary_name": "Bob", "is_international": false}'

# OFAC screening
curl -s -X POST http://localhost:8000/validate/ofac-screen \
  -H "Content-Type: application/json" \
  -d '{"party_name": "John Smith"}'

# Loan application (Section 1071)
curl -s -X POST http://localhost:8000/validate/loan-application \
  -H "Content-Type: application/json" \
  -d '{"race_of_principal_owner": "White", "sex_of_principal_owner": "Male", "ethnicity_of_principal_owner": "Not Hispanic", "naics_code": "522110", "business_gross_revenue": 500000, "time_in_business": "5 years", "number_of_workers": 10}'
```

---

## Process Your First JIRA Story

Once Pipeline 2 is running and synced, create a JIRA story:

```
Title: BANK-123 — Daily Transfer Limit
Description: Implement $3,000 daily transfer limit

Acceptance Criteria:
- System enforces $3,000 daily limit
- Cumulative transfers tracked in 24-hour window
- Clear error message when limit exceeded
```

TEXT MIND will:
1. Receive the webhook from JIRA
2. Run the 6-agent pipeline (Discovery → Understanding → Framework → Generation → Validation → Execution)
3. Request human approval via UI
4. Execute tests in sandbox
5. Update JIRA with results

---

## Next Steps

- [Deployment Guide](DEPLOYMENT.md) — Full AWS EC2 deployment instructions with security groups, production hardening
- [Architecture](docs/architecture/system-design.md) — System design, data flows, sync protocol
- [API Docs](http://localhost:8000/docs) — Interactive Swagger UI (when running)
