# Quick Start Guide

## Prerequisites
- Docker & Docker Compose installed
- JIRA instance with admin access
- AWS Bedrock OR OpenAI API access

## Installation

### 1. Clone Repository
```bash
git clone https://github.com/your-org/textmind-backend.git
cd textmind-backend
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Start Services
```bash
docker-compose up -d
```

### 4. Initialize Weaviate
```bash
docker-compose exec backend python scripts/setup/initialize_weaviate.py
```

### 5. Run Phase 1 Analysis
```bash
docker-compose exec backend python scripts/phase1/analyze_system.py \
  --repo-path /path/to/banking-app \
  --interactive
```

### 6. Configure JIRA
```bash
docker-compose exec backend python scripts/phase2/configure_jira.py --interactive
```

## Verify Installation

```bash
# Check health
curl http://localhost:8000/health

# Check Weaviate
curl http://localhost:8080/v1/.well-known/ready

# View API docs
open http://localhost:8000/docs
```

## Next Steps

1. Create a JIRA story
2. Watch TEXT MIND generate tests
3. Review and approve in approval UI
4. See PR created automatically

For detailed documentation, see [Architecture Overview](../architecture/system-design.md).
