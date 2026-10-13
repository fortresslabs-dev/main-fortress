"""AWS Bedrock Service — Claude Sonnet on bedrock-runtime."""
import asyncio
import boto3
import json
from typing import Dict, Optional
import os
import structlog
from botocore.config import Config

logger = structlog.get_logger(__name__)

DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


class BedrockService:
    """AWS Bedrock service for Claude Sonnet API calls"""

    def __init__(self, model_id: Optional[str] = None):
        from src.utils.config import settings
        self.region = os.getenv('AWS_REGION', settings.AWS_REGION or 'us-east-1')
        # Resolution order: explicit constructor arg → settings (env/SSM/Secrets) → built-in default
        self.model_id = model_id or settings.BEDROCK_MODEL or DEFAULT_BEDROCK_MODEL

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
        """Generate code using Claude Sonnet via Bedrock"""

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
