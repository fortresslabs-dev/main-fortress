"""LLM Client Abstraction"""
import boto3
from anthropic import Anthropic
from src.utils.config import settings

def get_llm_client():
    """Get LLM client based on provider"""
    if settings.LLM_PROVIDER == "bedrock":
        return boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
    elif settings.LLM_PROVIDER == "anthropic":
        api_key = settings.ANTHROPIC_API_KEY or getattr(settings, "CLAUDE_API_KEY", "")
        return Anthropic(api_key=api_key)
    elif settings.LLM_PROVIDER == "openai":
        return Anthropic(api_key=settings.OPENAI_API_KEY)
    else:
        raise ValueError(f"Unknown LLM provider: {settings.LLM_PROVIDER}")
