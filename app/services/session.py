"""Работа с сессией в Redis: история, счётчики, agent state, очистка неактивных."""
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


def _messages_key(chat_id: str) -> str:
    return f"chat:{chat_id}:messages"


def _agent_state_key(chat_id: str) -> str:
    return f"chat:{chat_id}:agent_state"


async def get_messages(redis_client: Redis, chat_id: str) -> List[dict]:
    raw = await redis_utils.safe_redis_get(redis_client, _messages_key(chat_id), default="[]")
    try:
        return json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []


async def get_agent_state(redis_client: Redis, chat_id: str) -> dict:
    raw = await redis_utils.safe_redis_get(redis_client, _agent_state_key(chat_id), default="{}")
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


async def save_agent_state(redis_client: Redis, chat_id: str, state: dict) -> None:
    await redis_utils.safe_redis_set(
        redis_client, _agent_state_key(chat_id), json.dumps(state, ensure_ascii=False), ex=3600
    )


async def append_message(redis_client: Redis, chat_id: str, role: str, content: str) -> List[dict]:
    messages = await get_messages(redis_client, chat_id)
    messages.append({"role": role, "content": content, "ts": time.time()})
    messages = messages[-settings.MAX_HISTORY_SIZE :]
    await redis_utils.safe_redis_set(
        redis_client, _messages_key(chat_id), json.dumps(messages, ensure_ascii=False), ex=3600
    )
    return messages


def user_history_from_messages(messages: List[dict]) -> List[str]:
    return [m["content"] for m in messages if m.get("role") == "user"]


async def update_session_and_get_history(
    redis_client: Redis,
    chat_id: str,
    anonymized_message: str,
) -> tuple:
    """
    Обновляет сессию в Redis.
    Возвращает (user_history, last_embedding_str, count_str, escalation_count_str, seen_is_new, messages).
    """
    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.get(f"chat:{chat_id}:seen")
        pipe.set(f"chat:{chat_id}:seen", "1")
        pipe.set(f"chat:{chat_id}:active", "1", ex=3600)
        pipe.get(f"chat:{chat_id}:start_time")
        pipe.get(_messages_key(chat_id))
        pipe.get(f"chat:{chat_id}:embedding")
        pipe.get(f"chat:{chat_id}:count")
        pipe.get(f"chat:{chat_id}:escalation_count")
        results = await pipe.execute()
    seen, _, _, start_time_str, messages_raw, last_embedding_str, count_str, escalation_count_str = results

    messages = []
    if messages_raw:
        try:
            messages = json.loads(messages_raw)
        except json.JSONDecodeError:
            messages = []

    # Legacy: migrate from chat:{id}:history if messages empty
    if not messages:
        legacy = await redis_utils.safe_redis_lrange(redis_client, f"chat:{chat_id}:history", 0, -1)
        if legacy:
            for item in reversed(legacy):
                messages.append({"role": "user", "content": item, "ts": time.time()})

    messages.append({"role": "user", "content": anonymized_message, "ts": time.time()})
    messages = messages[-settings.MAX_HISTORY_SIZE :]
    await redis_utils.safe_redis_set(
        redis_client, _messages_key(chat_id), json.dumps(messages, ensure_ascii=False), ex=3600
    )
    # Keep legacy history for compatibility
    await redis_utils.safe_redis_lpush(redis_client, f"chat:{chat_id}:history", anonymized_message)
    await redis_utils.safe_redis_ltrim(
        redis_client, f"chat:{chat_id}:history", 0, settings.MAX_HISTORY_SIZE - 1
    )

    seen_is_new = seen is None
    if start_time_str is None:
        await redis_utils.safe_redis_set(
            redis_client, f"chat:{chat_id}:start_time", str(time.time())
        )

    user_history = user_history_from_messages(messages)
    return (
        user_history,
        last_embedding_str or "[]",
        count_str or "0",
        escalation_count_str or "0",
        seen_is_new,
        messages,
    )


async def append_assistant_message(redis_client: Redis, chat_id: str, content: str) -> List[dict]:
    return await append_message(redis_client, chat_id, "assistant", content)


async def cleanup_inactive_sessions(redis_client: Redis) -> None:
    """Фоновая задача: сброс сессий по таймауту (1 ч)."""
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
                        f"chat:{chat_id}:count",
                        f"chat:{chat_id}:embedding",
                        f"chat:{chat_id}:seen",
                        _messages_key(chat_id),
                        _agent_state_key(chat_id),
                        f"chat:{chat_id}:escalation_summary",
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
    """Ключи Redis для удаления при эскалации (в т.ч. repeat-счётчик)."""
    return [
        f"chat:{chat_id}:start_time",
        f"chat:{chat_id}:active",
        f"chat:{chat_id}:escalation_count",
        f"chat:{chat_id}:count",
        f"chat:{chat_id}:embedding",
        f"chat:{chat_id}:seen",
        _messages_key(chat_id),
        _agent_state_key(chat_id),
        f"chat:{chat_id}:context",
        f"chat:{chat_id}:context_updated_at",
        f"chat:{chat_id}:escalation_summary",
    ]
