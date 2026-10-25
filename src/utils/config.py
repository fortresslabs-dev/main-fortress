"""Configuration Management — unified for Pipeline 1 and Pipeline 2.

Pipeline 1 (Fortress Cloud):
  - PIPELINE_MODE=pipeline1
  - Secrets sourced from AWS Secrets Manager (single secret JSON blob)
  - SECRETS_SOURCE=aws env var enables injection; falls back to .env locally

Pipeline 2 (Bank account / on-premises):
  - PIPELINE_MODE=pipeline2
  - Secrets sourced from AWS SSM Parameter Store under /fortress/{CLIENT_NAME}/
  - Falls back to env vars / .env file for local dev

Both pipelines share a single Settings class; P2-specific fields are Optional.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Resolved once at import time — controls injection and defaults below
PIPELINE_MODE: str = os.environ.get("PIPELINE_MODE", "pipeline2")  # "pipeline1" | "pipeline2"


# ── Secret injection helpers ──────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_secrets_manager(secret_name: str, region: str) -> dict:
    """Fetch JSON blob from Secrets Manager once per process (P1 only)."""
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_name)
        return json.loads(resp["SecretString"])
    except Exception as exc:
        logger.warning("secrets_manager_unavailable error=%s — using env vars", exc)
        return {}


def _inject_secrets_manager() -> None:
    """P1: pull from Secrets Manager and inject into os.environ.

    Only runs when SECRETS_SOURCE=aws. Env vars set at container level
    (ECS task definition) take priority over Secrets Manager values.
    """
    if os.environ.get("SECRETS_SOURCE", "") != "aws":
        return
    secret_name = os.environ.get("APP_SECRET_NAME", "fortress/pipeline1/app-config")
    region = os.environ.get("AWS_REGION", "us-east-1")
    secrets = _load_secrets_manager(secret_name, region)
    for key, value in secrets.items():
        if key not in os.environ:
            os.environ[key] = str(value)


def _inject_ssm_parameters() -> None:
    """P2: pull all params under /fortress/{CLIENT_NAME}/ into os.environ.

    Uses CLIENT_NAME env var as the namespace key. Falls back to
    /fortress/pipeline2/ for local dev without a named client.
    """
    client_name = os.environ.get("CLIENT_NAME", "")
    prefix = f"/fortress/{client_name}/" if client_name else "/fortress/pipeline2/"
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
            for param in page["Parameters"]:
                key = param["Name"].replace(prefix, "")
                if key not in os.environ:  # container-level env vars take priority
                    os.environ[key] = param["Value"]
        logger.debug("ssm_parameters_injected prefix=%s", prefix)
    except Exception as exc:
        logger.warning("ssm_injection_failed error=%s — continuing with env vars", exc)


def _compose_p2_connection_urls() -> None:
    """P2: build DATABASE_URL and REDIS_URL from pieces injected by ECS.

    ECS task def injects DB_USER/DB_PASSWORD/REDIS_AUTH_TOKEN from Secrets
    Manager; AURORA_HOST/AURORA_PORT/DB_NAME/REDIS_HOST arrive via SSM.
    Aurora secrets are auto-generated so the URL can't be templated in CFN.
    """
    if "DATABASE_URL" not in os.environ:
        user = os.environ.get("DB_USER")
        pw = os.environ.get("DB_PASSWORD")
        host = os.environ.get("AURORA_HOST")
        port = os.environ.get("AURORA_PORT", "5432")
        db = os.environ.get("DB_NAME")
        if user and pw and host and db:
            os.environ["DATABASE_URL"] = (
                f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{db}?ssl=require"
            )

    if "REDIS_URL" not in os.environ:
        token = os.environ.get("REDIS_AUTH_TOKEN")
        host = os.environ.get("REDIS_HOST")
        if token and host:
            os.environ["REDIS_URL"] = f"rediss://:{token}@{host}:6379/0"


def _alias_sync_env_vars() -> None:
    # UpdateService reads CUSTOMER_ID / FORTRESS_API_KEY; the task def ships
    # the same values under CLIENT_NAME / PIPELINE1_API_KEY. Mirror them so
    # both name pairs stay valid without touching CFN.
    if not os.environ.get("CUSTOMER_ID") and os.environ.get("CLIENT_NAME"):
        os.environ["CUSTOMER_ID"] = os.environ["CLIENT_NAME"]
    if not os.environ.get("FORTRESS_API_KEY") and os.environ.get("PIPELINE1_API_KEY"):
        os.environ["FORTRESS_API_KEY"] = os.environ["PIPELINE1_API_KEY"]


# Inject before Settings is instantiated so Pydantic picks up injected values
if PIPELINE_MODE == "pipeline1":
    _inject_secrets_manager()
else:
    _inject_ssm_parameters()
    _compose_p2_connection_urls()
    _alias_sync_env_vars()


# ── Unified Settings ──────────────────────────────────────────────────────────

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Read .env for P1 (as before) and for LOCAL P2 (so a single .env drives
        # LLM_PROVIDER/keys for `make p2-live`). Prod P2 sets SECRETS_SOURCE=aws
        # and injects via SSM → no .env there. Real env vars still take
        # precedence over .env, so inline Makefile vars win.
        env_file=".env" if (PIPELINE_MODE == "pipeline1"
                            or os.environ.get("SECRETS_SOURCE", "") != "aws") else None,
        case_sensitive=True,
        extra="ignore",
    )

    # ── Common ──────────────────────────────────────────────────────────
    ENVIRONMENT: str = Field(default="development")
    DEBUG: bool = Field(default=False)
    PIPELINE_MODE: str = Field(default="pipeline2")

    # Database — required for both pipelines
    DATABASE_URL: str = Field(..., description="PostgreSQL connection URL (use ssl=require in prod)")
    REDIS_URL: str = Field(..., description="Redis URL (use rediss:// in prod)")

    # ── Vector DB — Weaviate (interim, replaced by OpenSearch/pgvector) ──
    # Pipeline 1 → OpenSearch Serverless
    # Pipeline 2 → pgvector on Aurora (already provisioned)
    WEAVIATE_URL: str = Field(default="http://weaviate:8080")
    WEAVIATE_API_KEY: str = Field(default="")

    # ── OpenSearch Serverless (P1 — future managed service) ─────────────
    OPENSEARCH_ENDPOINT: Optional[str] = Field(default=None,
        description="OpenSearch Serverless collection endpoint (P1 only)")
    OPENSEARCH_COLLECTION: str = Field(default="fortress-compliance-docs")

    # ── LLM / Bedrock ───────────────────────────────────────────────────
    LLM_PROVIDER: str = Field(default="bedrock")
    LLM_MOCK_MODE: bool = Field(default=False)
    LLM_CACHE_TTL: int = Field(default=86400)
    LLM_MAX_TOKENS: int = Field(default=2000)
    ANTHROPIC_API_KEY: str = Field(default="")
    ANTHROPIC_MODEL: str = Field(default="claude-sonnet-4-20250514")
    GPT_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")
    OPENAI_MODEL: str = Field(default="gpt-4o")  # chat-completions model when LLM_PROVIDER=openai/gpt
    AWS_REGION: str = Field(default="us-east-1")
    # Bedrock model IDs MUST be valid inference profile IDs in the target region.
    # Sonnet 4/4.5/4.6 and Haiku 4.5 are INFERENCE_PROFILE-only — they require the
    # `us.` (or `global.`) prefix and a `-v1:0` suffix. Bare model IDs like
    # `anthropic.claude-sonnet-4-20250514` yield ValidationException at invoke time.
    # Override per-environment via SSM (`/fortress/{client}/BEDROCK_MODEL`) or
    # Secrets Manager (P1 app-config blob).
    BEDROCK_MODEL: str = Field(default="us.anthropic.claude-sonnet-4-5-20250929-v1:0")

    # Per-agent model selection (cost-optimised; cheap classification → Haiku, generation → Sonnet).
    # Values must be valid Bedrock inference profile IDs (see BEDROCK_MODEL note above).
    P1_OBLIGATION_EXTRACTION_MODEL: str = Field(default="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    P2_AGENT_DISCOVERY_MODEL: str = Field(default="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    P2_AGENT_UNDERSTANDING_MODEL: str = Field(default="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    P2_AGENT_FRAMEWORK_MODEL: str = Field(default="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    P2_AGENT_GENERATION_MODEL: str = Field(default="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    P2_AGENT_VALIDATION_MODEL: str = Field(default="us.anthropic.claude-haiku-4-5-20251001-v1:0")

    # ── P1-specific ─────────────────────────────────────────────────────
    # Internal secret name for Secrets Manager injection (P1 only)
    APP_SECRET_NAME: str = Field(default="fortress/pipeline1/app-config")
    SECRETS_SOURCE: str = Field(default="")   # set to "aws" in ECS task def

    # Scraper scheduler (P1) — in production EventBridge → run_scrape.py owns
    # the daily cron. SCRAPER_IN_PROCESS is the legacy in-uvicorn scheduler;
    # keep it off in prod to avoid double-scraping.
    SCRAPER_IN_PROCESS: bool = Field(default=False,
        description="Run the in-process daily scheduler inside the API container")
    SCRAPER_RUN_ON_STARTUP: bool = Field(default=False)
    SCRAPER_STARTUP_DELAY: int = Field(default=300,
        description="Seconds to wait after boot before the first startup scrape")
    SCRAPER_CONNECTOR_DELAY: int = Field(default=2,
        description="Seconds to wait between successive connector extractions")
    SCRAPER_INTERVAL_HOURS: int = Field(default=24)

    # ── P2-specific — JIRA ──────────────────────────────────────────────
    # Optional in P1 (scraper doesn't use JIRA); Required in P2
    JIRA_URL: Optional[str] = Field(default=None)
    JIRA_USERNAME: Optional[str] = Field(default=None)
    JIRA_API_TOKEN: Optional[str] = Field(default=None)
    JIRA_WEBHOOK_SECRET: str = Field(default="")

    # JIRA workflow transition names (must match the customer's workflow status names)
    JIRA_STATUS_IN_REVIEW: str = Field(default="In Review")
    JIRA_STATUS_NEEDS_REVISION: str = Field(default="To Do")
    JIRA_STATUS_DONE: str = Field(default="Done")

    # ── P2-specific — Auth / Identity ───────────────────────────────────
    PIPELINE2_API_KEY: str = Field(default="changeme-token",
        description="Bearer token for developer API endpoints (P2 only)")
    JWT_SECRET: str = Field(default="",
        description="JWT signing secret for CCO dashboard sessions (P2 only)")
    CLIENT_NAME: str = Field(default="",
        description="Bank slug used as SSM namespace, e.g. 'firstnational'")

    # ── P2-specific — GitHub integration ────────────────────────────────
    GITHUB_TOKEN: str = Field(default="")
    GITHUB_ORG: str = Field(default="")
    GITHUB_REPO: str = Field(default="")

    # ── P2-specific — Sandbox test runner ───────────────────────────────
    TEST_RUNNER_TIMEOUT: int = Field(default=120,
        description="Max seconds for sandbox pytest execution")

    # ── P2-specific — Fortress sync ─────────────────────────────────────
    PIPELINE1_API_URL: Optional[str] = Field(default=None,
        description="Fortress Cloud sync API base URL (P2 only)")
    PIPELINE1_API_KEY: Optional[str] = Field(default=None,
        description="HMAC api_key issued by Fortress for this bank (P2 only)")
    INTERNAL_SYNC_SECRET: str = Field(default="",
        description="Shared secret gating POST /internal/sync/trigger (P2 only)")

    # ── CORS ────────────────────────────────────────────────────────────
    # Explicit allowlist for the dashboard origin(s). NEVER include "*"
    # alongside `allow_credentials=True` — browsers reject the combination
    # and the dashboard JWT flow silently breaks. Set via env var as either
    # JSON (`["https://a","https://b"]`) or comma-separated
    # (`https://a,https://b`). In production an empty list is a hard error.
    ALLOWED_ORIGINS: List[str] = Field(default_factory=list)

    # Allow the dev-token fallback in src/api/auth.py. Must be explicitly
    # enabled — leaving it false (the default) forces Cognito JWT in any
    # non-dev environment, so a missing COGNITO_USER_POOL_ID can't silently
    # disable auth.
    DEV_MODE: bool = Field(default=False)

    PORT: int = Field(default=8000)

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                try:
                    return json.loads(s)
                except json.JSONDecodeError:
                    pass
            return [o.strip() for o in s.split(",") if o.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # Hard guard: in production the CORS allowlist must be non-empty and
    # must not contain "*". Empty wildcards in a credentialed CORS setup
    # are a silent customer-visible auth failure.
    if s.ENVIRONMENT == "production":
        if not s.ALLOWED_ORIGINS:
            raise RuntimeError(
                "ALLOWED_ORIGINS is empty in production. "
                "Set it via env var (comma-separated origin URLs)."
            )
        if "*" in s.ALLOWED_ORIGINS:
            raise RuntimeError(
                "ALLOWED_ORIGINS contains '*' in production. "
                "Browsers reject wildcard + credentials; list explicit origins."
            )
    return s


settings = get_settings()
