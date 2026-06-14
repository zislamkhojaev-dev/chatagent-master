"""Контекст чата: get, асинхронное переписывание (LLM). ТЗ Б.5."""
import asyncio
import logging
from typing import Optional

from redis.asyncio import Redis

from app.core.config import settings
from app.core.dependencies import get_openai_client
from app.utils import redis_utils

logger = logging.getLogger(__name__)

# Лимит контекста в символах (ТЗ уточнение п.7)
CONTEXT_MAX_CHARS = settings.CONTEXT_MAX_CHARS
CONTEXT_TTL = 3600

# In-process: не более одной задачи обновления на чат (ТЗ п.8)
_context_tasks: dict[str, asyncio.Task] = {}


async def get_chat_context(redis_client: Redis, chat_id: str) -> str:
    """Читает сохранённый контекст диалога из Redis."""
    return await redis_utils.safe_redis_get(
        redis_client, f"chat:{chat_id}:context", default=""
    ) or ""


async def _rewrite_context_llm(
    current_context: str,
    last_user_message: str,
    last_bot_response: str,
) -> str:
    """Вызов LLM для обновления краткого изложения диалога."""
    client = get_openai_client()
    prompt = (
        "Обнови краткое изложение диалога поддержки с учётом нового обмена. "
        "Сохрани ключевые факты, тему и решения. Ответь только текстом изложения, без лишнего.\n"
        f"Текущее изложение: {current_context or '(пусто)'}\n"
        f"Пользователь: {last_user_message}\n"
        f"Бот: {last_bot_response}\n"
        "Новое изложение:"
    )
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        text = (response.choices[0].message.content or "").strip()
        if len(text) > CONTEXT_MAX_CHARS:
            text = text[:CONTEXT_MAX_CHARS] + "..."
        return text
    except Exception as e:
        logger.warning("Context rewrite LLM failed: %s", e)
        return current_context


async def update_chat_context_async(
    redis_client: Redis,
    chat_id: str,
    last_user_message: str,
    last_bot_response: str,
) -> None:
    """
    Фоновая задача: обновить контекст чата (текущий контекст + последний обмен → LLM → Redis).
    Не более одной задачи на чат (дедупликация по chat_id).
    """
    try:
        current = await get_chat_context(redis_client, chat_id)
        new_context = await _rewrite_context_llm(
            current, last_user_message, last_bot_response
        )
        await redis_utils.safe_redis_set(
            redis_client,
            f"chat:{chat_id}:context",
            new_context,
            ex=CONTEXT_TTL,
        )
    except Exception as e:
        logger.warning("update_chat_context_async failed for %s: %s", chat_id, e)
    finally:
        _context_tasks.pop(chat_id, None)


def schedule_context_update(
    redis_client: Redis,
    chat_id: str,
    last_user_message: str,
    last_bot_response: str,
) -> None:
    """Поставить в очередь фоновое обновление контекста (не более одной задачи на чат)."""
    if chat_id in _context_tasks:
        return
    task = asyncio.create_task(
        update_chat_context_async(
            redis_client, chat_id, last_user_message, last_bot_response
        )
    )
    _context_tasks[chat_id] = task
