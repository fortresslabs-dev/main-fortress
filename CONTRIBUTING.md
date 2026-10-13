# Contributing to TEXT MIND

## Development Setup

```bash
# Clone
git clone https://github.com/your-org/textmind_banking_backend.git
cd textmind_banking_backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v
```

## Project Layout

- `src/main_pipeline1.py` — Fortress Cloud entry point (scraping + sync)
- `src/main_pipeline2.py` — Customer VPC entry point (validators + agents)
- `src/api/` — FastAPI routers
- `src/services/` — Business logic
- `src/models/database.py` — All SQLAlchemy models
- `src/connectors/` — Regulatory source connectors
- `src/agents/` — AI agents (6-agent pipeline + LangGraph)
- `alembic/versions/` — Database migrations

See [PROJECT_STRUCTURE.txt](PROJECT_STRUCTURE.txt) for the full directory tree.

## Code Standards

- Python 3.11+
- Follow PEP 8
- Use type hints for function signatures
- Use `structlog` for logging (not `print`)
- Async functions for all database/HTTP operations
- Write tests for new features in `tests/`

## Database Changes

1. Create a new migration: `alembic revision --autogenerate -m "description"`
2. Review the generated file in `alembic/versions/`
3. Test: `alembic upgrade head` then `alembic downgrade -1` then `alembic upgrade head`

## Adding a New Regulatory Connector

1. Create `src/connectors/regulatory/<geography>/<name>.py`
2. Implement the `BaseConnector` interface (`get_compliance_rules()`)
3. Register in `src/connectors/regulatory/<geography>/__init__.py`
4. Add config to `config/connectors/<geography>.yaml`
5. Add validation script to `scripts/connectors/`

## Commit Messages

Use conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `test:` Tests
- `refactor:` Code refactoring
- `chore:` Build/config changes

## Pull Request Process

1. Create a feature branch from `main`
2. Make changes and ensure `pytest tests/ -v` passes
3. Run `python -m py_compile src/main_pipeline1.py src/main_pipeline2.py`
4. Submit PR with description of changes
5. Address review feedback

## Security

- Never commit `.env` files, API keys, or credentials
- Use `FortressAPIClient._check_pii()` pattern for any outgoing HTTP calls
- Customer data must never leave Pipeline 2
- Report security issues to security@textmind.ai (not public issues)

## Questions?

Contact: dev@textmind.ai
