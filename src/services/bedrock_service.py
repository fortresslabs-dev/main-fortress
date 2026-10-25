"""Claude generation service.

Default provider is AWS Bedrock (Claude on bedrock-runtime). When
``LLM_PROVIDER=anthropic`` the same service calls the **direct Claude API**
(``anthropic`` SDK) instead — this is the no-AWS local path: set
``LLM_PROVIDER=anthropic`` + ``ANTHROPIC_API_KEY`` and test generation runs
without any Bedrock/AWS credentials. Model IDs configured as Bedrock
inference-profile strings (``us.anthropic.claude-sonnet-4-5-20250929-v1:0``)
are translated to their direct Anthropic form (``claude-sonnet-4-5-20250929``)
so the same model tier is used either way. See ``docs/setup/local-dev.md``.
"""
import asyncio
import boto3
import json
import re
from typing import Dict, Optional
import os
import structlog
from botocore.config import Config

logger = structlog.get_logger(__name__)

DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# One variable — LLM_PROVIDER — selects the backend. Friendly aliases map to
# the canonical names so `claude`/`gpt` work as well as `anthropic`/`openai`.
_PROVIDER_ALIASES = {"claude": "anthropic", "gpt": "openai"}


def normalize_provider(provider: Optional[str]) -> str:
    """Canonical provider: anthropic (Claude API) | openai (GPT) | bedrock (AWS)."""
    p = (provider or "bedrock").lower()
    return _PROVIDER_ALIASES.get(p, p)


def to_anthropic_model_id(bedrock_id: str) -> str:
    """Translate a Bedrock inference-profile ID to a direct Anthropic API ID.

    ``us.anthropic.claude-sonnet-4-5-20250929-v1:0`` → ``claude-sonnet-4-5-20250929``
    ``us.anthropic.claude-haiku-4-5-20251001-v1:0``  → ``claude-haiku-4-5-20251001``
    A string that's already a bare Anthropic ID passes through unchanged.
    """
    mid = re.sub(r"^(us|eu|apac|global)\.", "", bedrock_id)  # regional profile prefix
    mid = re.sub(r"^anthropic\.", "", mid)                   # provider prefix
    mid = re.sub(r"-v\d+:\d+$", "", mid)                     # -v1:0 version suffix
    mid = re.sub(r":\d+$", "", mid)                          # bare :0 suffix
    return mid


class BedrockService:
    """Claude generation service — Bedrock by default, direct Claude API when
    ``LLM_PROVIDER=anthropic``."""

    def __init__(self, model_id: Optional[str] = None):
        from src.utils.config import settings
        self.region = os.getenv('AWS_REGION', settings.AWS_REGION or 'us-east-1')
        # Resolution order: explicit constructor arg → settings (env/SSM/Secrets) → built-in default
        self.model_id = model_id or settings.BEDROCK_MODEL or DEFAULT_BEDROCK_MODEL
        self.provider = normalize_provider(settings.LLM_PROVIDER)

        if self.provider == "anthropic":
            # Direct Claude API — no AWS. An explicit ANTHROPIC_MODEL overrides;
            # otherwise translate the configured Bedrock ID so the model tier
            # matches the Bedrock path exactly (default → claude-sonnet-4-5).
            from anthropic import Anthropic
            self.anthropic_model = settings.ANTHROPIC_MODEL or to_anthropic_model_id(self.model_id)
            self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("llm_service_initialized", provider="anthropic", model_id=self.anthropic_model)
            return

        if self.provider == "openai":
            # Direct OpenAI API (GPT) — no AWS. Model is OPENAI_MODEL (a
            # chat-completions model, default gpt-4o); the Bedrock ID is not used.
            from openai import OpenAI
            self.openai_model = settings.OPENAI_MODEL or "gpt-4o"
            self.client = OpenAI(api_key=settings.OPENAI_API_KEY or settings.GPT_API_KEY)
            logger.info("llm_service_initialized", provider="openai", model_id=self.openai_model)
            return

        # boto3 defaults: read_timeout=60s, max_attempts=3. Sonnet 4.5 with
        # max_tokens=8000 + long compliance prompts can take 2-5 min to return,
        # which exceeds the 60s read timeout → 3 retry storm → ~5 min before
        # ReadTimeoutError surfaces. Set a generous read timeout and cap
        # retries (re-trying a slow-but-working endpoint won't make it faster).
        self.client = boto3.client(
            service_name='bedrock-runtime',
            region_name=self.region,
            config=Config(
                read_timeout=600,
                connect_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

        logger.info("bedrock_service_initialized", region=self.region, model_id=self.model_id)

    async def generate_code(
        self,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.3,
        model_id: Optional[str] = None,
    ) -> str:
        """Generate code using Claude — direct Claude API or Bedrock per provider."""

        if self.provider == "anthropic":
            return await self._generate_code_anthropic(
                prompt, max_tokens, temperature,
                to_anthropic_model_id(model_id) if model_id else self.anthropic_model,
            )

        if self.provider == "openai":
            return await self._generate_code_openai(
                prompt, max_tokens, temperature, model_id or self.openai_model,
            )

        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            })

            # boto3 invoke_model is synchronous and blocks the event loop for
            # the entire generation duration (can be 1-4 min on Sonnet 4.5 with
            # max_tokens=8000). That blocks /health/, ALB marks the target
            # unhealthy, ECS kills the task, and the BackgroundTask dies
            # mid-flight. Offload to the default thread pool executor so the
            # event loop stays free for health checks + other requests.
            response = await asyncio.to_thread(
                self.client.invoke_model,
                modelId=model_id or self.model_id,
                body=body,
            )
            
            response_body = json.loads(response['body'].read())
            code = response_body['content'][0]['text']
            
            logger.info("bedrock_code_generated", tokens=response_body.get('usage', {}).get('output_tokens'))

            return code

        except Exception as e:
            logger.error("bedrock_generation_failed", error=str(e))
            raise

    async def _generate_code_anthropic(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
    ) -> str:
        """Generate via the direct Claude API (anthropic SDK). No AWS.

        The SDK call is synchronous, so (like the Bedrock path) it's offloaded
        to a thread to keep the event loop free for health checks. ``temperature``
        is accepted by the default Sonnet-4.5 tier; note newer tiers (Opus 4.7+,
        Sonnet 5) reject it — pin ANTHROPIC_MODEL only to a temperature-capable
        model, or drop temperature if you override to a newer one.
        """
        try:
            def _call():
                message = self.client.messages.create(
                    model=model_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(
                    block.text for block in message.content
                    if getattr(block, "type", None) == "text"
                )

            code = await asyncio.to_thread(_call)
            logger.info("anthropic_code_generated", model_id=model_id)
            return code
        except Exception as e:
            logger.error("anthropic_generation_failed", error=str(e))
            raise

    async def _generate_code_openai(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
    ) -> str:
        """Generate via the direct OpenAI API (GPT, chat completions). No AWS."""
        try:
            def _call():
                resp = self.client.chat.completions.create(
                    model=model_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content or ""

            code = await asyncio.to_thread(_call)
            logger.info("openai_code_generated", model_id=model_id)
            return code
        except Exception as e:
            logger.error("openai_generation_failed", error=str(e))
            raise
