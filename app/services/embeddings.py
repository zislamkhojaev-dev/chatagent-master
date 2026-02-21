"""Эмбеддинги (OpenAI), кэш в Redis. ТЗ Б.7."""
import asyncio
import json
import logging
from typing import Optional

import httpx
from openai import APIError, RateLimitError
from redis.asyncio import Redis
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.dependencies import get_openai_client
from app.utils import redis_utils

logger = logging.getLogger(__name__)

_embedding_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _embedding_semaphore
    if _embedding_semaphore is None:
        _embedding_semaphore = asyncio.Semaphore(settings.EMBEDDING_SEMAPHORE_LIMIT)
    return _embedding_semaphore


@retry(
    stop=stop_after_attempt(settings.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=settings.MIN_WAIT, max=settings.MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
)
async def get_embedding(text: str, redis_client: Redis) -> list[float]:
    """Эмбеддинг текста (OpenAI text-embedding-ada-002), с кэшем в Redis."""
    cached = await redis_utils.safe_redis_get(redis_client, f"embedding:{text}")
    if cached:
        logger.info("Using cached embedding for: %s...", text[:50])
        return json.loads(cached)
    logger.info("Fetching new embedding for: %s...", text[:50])
    client = get_openai_client()
    response = await client.embeddings.create(
        model="text-embedding-ada-002", input=text
    )
    if not response.data or not response.data[0].embedding:
        raise APIError("No embedding data in OpenAI response")
    embedding = response.data[0].embedding
    await redis_utils.safe_redis_set(
        redis_client, f"embedding:{text}", json.dumps(embedding), ex=3600
    )
    return embedding


async def get_embedding_with_semaphore(
    chunk: str, redis_client: Redis
) -> list[float] | None:
    """Эмбеддинг с семафором (для батча при индексации)."""
    try:
        async with _get_semaphore().acquire():
            return await get_embedding(chunk, redis_client)
    except Exception as e:
        logger.error("Failed to get embedding for chunk %s...: %s", chunk[:50], e)
        return None
