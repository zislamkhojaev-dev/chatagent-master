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
def _embedding_cache_key(text: str) -> str:
    """Префикс по модели/размерности — не смешивать с кэшем от ada-002."""
    return (
        f"embedding:{settings.OPENAI_EMBEDDING_MODEL}:"
        f"{settings.OPENAI_EMBEDDING_DIMENSIONS}:{text}"
    )


async def get_embedding(text: str, redis_client: Redis) -> list[float]:
    """Эмбеддинг текста (OpenAI, по умолчанию text-embedding-3-small, 1536), кэш Redis."""
    cache_key = _embedding_cache_key(text)
    cached = await redis_utils.safe_redis_get(redis_client, cache_key)
    if cached:
        logger.info("Using cached embedding for: %s...", text[:50])
        return json.loads(cached)
    logger.info("Fetching new embedding for: %s...", text[:50])
    client = get_openai_client()
    response = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text,
        dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
    )
    if not response.data or not response.data[0].embedding:
        raise APIError("No embedding data in OpenAI response")
    embedding = response.data[0].embedding
    await redis_utils.safe_redis_set(
        redis_client, cache_key, json.dumps(embedding), ex=3600
    )
    return embedding


async def get_embedding_with_semaphore(
    chunk: str, redis_client: Redis
) -> list[float] | None:
    """Эмбеддинг с семафором (для батча при индексации)."""
    try:
        async with _get_semaphore():
            return await get_embedding(chunk, redis_client)
    except Exception as e:
        logger.error("Failed to get embedding for chunk %s...: %s", chunk[:50], e)
        return None
