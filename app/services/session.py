"""Работа с сессией в Redis: история, счётчики, очистка неактивных (ТЗ Б.7)."""
import asyncio
import json
import logging
import time
from typing import List

from redis.asyncio import Redis

from app.core.config import settings
from app.utils import redis_utils
from app.utils.metrics import ACTIVE_CHATS, SESSION_DURATION, SESSIONS_TOTAL

logger = logging.getLogger(__name__)


async def update_session_and_get_history(
    redis_client: Redis,
    chat_id: str,
    anonymized_message: str,
) -> tuple[List[str], str, str, str, bool]:
    """
    Обновляет сессию в Redis. Возвращает (history, last_embedding_str, count_str, escalation_count_str, seen_is_new).
    seen_is_new = (seen was None) — для метрики UNIQUE_CHATS_TOTAL.
    """
    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.get(f"chat:{chat_id}:seen")
        pipe.set(f"chat:{chat_id}:seen", "1")
        pipe.set(f"chat:{chat_id}:active", "1", ex=3600)
        pipe.get(f"chat:{chat_id}:start_time")
        pipe.lpush(f"chat:{chat_id}:history", anonymized_message)
        pipe.ltrim(f"chat:{chat_id}:history", 0, settings.MAX_HISTORY_SIZE - 1)
        pipe.lrange(f"chat:{chat_id}:history", 0, -1)
        pipe.get(f"chat:{chat_id}:embedding")
        pipe.get(f"chat:{chat_id}:count")
        pipe.get(f"chat:{chat_id}:escalation_count")
        results = await pipe.execute()
    seen, _, _, start_time_str, _, _, history, last_embedding_str, count_str, escalation_count_str = results
    history = list(history or [anonymized_message])
    seen_is_new = seen is None
    if start_time_str is None:
        await redis_utils.safe_redis_set(
            redis_client, f"chat:{chat_id}:start_time", str(time.time())
        )
    return (
        history,
        last_embedding_str or "[]",
        count_str or "0",
        escalation_count_str or "0",
        seen_is_new,
    )


async def cleanup_inactive_sessions(redis_client: Redis) -> None:
    """Фоновая задача: сброс сессий по таймауту (1 ч). При очистке удалять и контекст чата (ТЗ уточнение п.3)."""
    while True:
        try:
            keys = await redis_utils.safe_redis_scan_iter(
                redis_client, "chat:*:start_time"
            )
            for key in keys:
                chat_id = key.split(":")[1]
                start_time = float(
                    await redis_utils.safe_redis_get(redis_client, key) or "0"
                )
                if time.time() - start_time > 3600:
                    duration = time.time() - start_time
                    SESSION_DURATION.observe(duration)
                    SESSIONS_TOTAL.inc()
                    await redis_utils.safe_redis_delete(
                        redis_client,
                        key,
                        f"chat:{chat_id}:active",
                        f"chat:{chat_id}:escalation_count",
                        f"chat:{chat_id}:last_language",
                        f"chat:{chat_id}:context",
                        f"chat:{chat_id}:context_updated_at",
                    )
                    logger.info("Session for chat %s timed out", chat_id)
            active_chats = len(
                await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:active")
            )
            ACTIVE_CHATS.set(active_chats)
        except Exception as e:
            logger.error("Error in session cleanup: %s", e)
        await asyncio.sleep(60)


def get_escalation_keys_to_delete(chat_id: str) -> List[str]:
    """Ключи Redis для удаления при эскалации (очистка сессии)."""
    return [
        f"chat:{chat_id}:start_time",
        f"chat:{chat_id}:active",
        f"chat:{chat_id}:escalation_count",
    ]
