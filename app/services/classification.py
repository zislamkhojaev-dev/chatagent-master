"""Классификация запросов: локальные ключевые слова + OpenAI fallback. ТЗ Б.7."""
import json
import logging
from typing import Dict

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
from app.services.constants import CATEGORIES
from app.utils import redis_utils
from app.utils.metrics import CLASSIFICATIONS_COUNT
from app.utils.synonyms import normalize_text_with_synonyms

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(settings.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=settings.MIN_WAIT, max=settings.MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
)
async def classify_query(
    query: str, language: str, redis_client: Redis
) -> Dict[str, str]:
    """Классифицирует запрос с заранее определённым языком."""
    cached = await redis_utils.safe_redis_get(redis_client, f"classification:{query}")
    if cached:
        logger.info("Using cached classification: %s", query)
        return json.loads(cached)
    normalized_query = normalize_text_with_synonyms(query, language)
    query_lower = normalized_query.lower()
    for category, subcategories in CATEGORIES.items():
        for subcategory, keywords in subcategories.items():
            if any(kw in query_lower for kw in keywords):
                result = {
                    "theme": category.split(" / ")[0]
                    if language == "ru"
                    else category.split(" / ")[1],
                    "category": subcategory.split(" / ")[0]
                    if language == "ru"
                    else subcategory.split(" / ")[1],
                    "subcategory": keywords[0],
                }
                await redis_utils.safe_redis_set(
                    redis_client, f"classification:{query}", json.dumps(result), ex=3600
                )
                CLASSIFICATIONS_COUNT.labels(
                    theme=result["theme"],
                    category=result["category"],
                    subcategory=result["subcategory"],
                ).inc()
                return result
    logger.info("No local match found, falling back to OpenAI")
    client = get_openai_client()
    prompt = (
        f"Определи тематику, категорию и подкатегорию запроса пользователя на основе следующего текста:\n"
        f"Текст запроса: {normalized_query}\n"
        f"Язык запроса: {'русский' if language == 'ru' else 'узбекский'}\n"
        f"Доступные категории:\n{json.dumps(CATEGORIES, ensure_ascii=False, indent=2)}\n"
        "Выбери категорию и подкатегорию строго из списка выше. Если запрос не соответствует ни одной категории, верни 'Неизвестно' для всех полей.\n"
        'Верни результат строго в формате JSON: {"theme": "тема", "category": "категория", "subcategory": "подкатегория"}. '
        "Используй только двойные кавычки. Не добавляй никакого дополнительного текста."
    )
    raw_response = ""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.5,
            max_tokens=200,
        )
        raw_response = response.choices[0].message.content.strip()
        result = json.loads(raw_response)
        if not all(k in result for k in ("theme", "category", "subcategory")):
            raise ValueError("Неверный формат ответа от OpenAI")
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse OpenAI classification: %s. Raw: %s", e, raw_response)
        result = {
            "theme": "Неизвестно" if language == "ru" else "Noma'lum",
            "category": "Неизвестно" if language == "ru" else "Noma'lum",
            "subcategory": "Неизвестно" if language == "ru" else "Noma'lum",
        }
    except httpx.TimeoutException:
        result = {
            "theme": "Неизвестно" if language == "ru" else "Noma'lum",
            "category": "Неизвестно" if language == "ru" else "Noma'lum",
            "subcategory": "Неизвестно" if language == "ru" else "Noma'lum",
        }
    CLASSIFICATIONS_COUNT.labels(
        theme=result["theme"],
        category=result["category"],
        subcategory=result["subcategory"],
    ).inc()
    await redis_utils.safe_redis_set(
        redis_client, f"classification:{query}", json.dumps(result), ex=3600
    )
    return result
