"""Зависимости FastAPI: Redis, DB, OpenAI, векторное хранилище (ТЗ Б.7)."""
import logging
from typing import Any, List, Optional

import httpx
from fastapi import Request
from openai import AsyncOpenAI
from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_openai_client: Optional[AsyncOpenAI] = None


def get_openai_client() -> AsyncOpenAI:
    """Глобальный клиент OpenAI (инициализируется при старте)."""
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=httpx.Timeout(
                connect=10.0,
                read=10.0,
                write=10.0,
                pool=30.0,
            ),
        )
        logger.info("Initialized AsyncOpenAI client")
    return _openai_client


async def get_redis(request: Request) -> Redis:
    """Redis из app.state."""
    return request.app.state.redis


async def get_db_pool(request: Request):
    """Пул PostgreSQL из app.state."""
    return request.app.state.db_pool


def get_knowledge_base(request: Request) -> List[str]:
    """Список чанков БЗ из app.state."""
    return request.app.state.knowledge_base or []
