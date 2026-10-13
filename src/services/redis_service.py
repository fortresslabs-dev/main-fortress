"""Redis service for caching and job queue"""
from redis import asyncio as aioredis
from src.utils.config import settings
import logging

logger = logging.getLogger(__name__)

redis_client = None

async def init_redis():
    """Initialize Redis connection. Soft-fails: Redis is cache-only here,
    and every call site checks `if not redis: return None`. A connectivity
    blip must not kill app startup."""
    global redis_client
    try:
        redis_client = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        await redis_client.ping()
        logger.info("Redis connected successfully")
    except Exception as e:
        logger.warning(f"Redis unavailable, continuing without cache: {e}")
        redis_client = None

async def close_redis():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")

async def get_redis():
    """Get Redis client instance"""
    return redis_client