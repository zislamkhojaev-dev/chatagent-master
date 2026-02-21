"""Детекция языка (OpenAI, кэш Redis, last_language). ТЗ Б.7."""
import hashlib
import logging
import re
import time
from contextvars import ContextVar
from typing import Optional

import httpx
from openai import APIError, AsyncOpenAI, RateLimitError
from redis.asyncio import Redis
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.utils import redis_utils
from app.utils.metrics import (
    LANGUAGE_DETECTION_CACHE_HIT,
    LANGUAGE_DETECTION_COUNT,
    LANGUAGE_DETECTION_ERRORS,
    LANGUAGE_DETECTION_LATENCY,
)

logger = logging.getLogger(__name__)

_request_language: ContextVar[Optional[str]] = ContextVar(
    "request_language", default=None
)


def get_request_language_context() -> ContextVar[Optional[str]]:
    return _request_language


def set_request_language(lang: Optional[str]) -> None:
    _request_language.set(lang)


def get_request_language() -> Optional[str]:
    return _request_language.get()


@retry(
    stop=stop_after_attempt(settings.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=settings.MIN_WAIT, max=settings.MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
    before_sleep=lambda rs: logger.warning(
        "Retrying OpenAI language detection: attempt %s due to %s",
        rs.attempt_number,
        rs.outcome.exception(),
    ),
)
async def detect_language_openai(
    text: str,
    chat_id: Optional[str] = None,
    redis_client: Optional[Redis] = None,
) -> tuple[str, bool]:
    """Определяет язык (ru/uz). Возвращает (language, is_uncertain)."""
    start_time = time.time()
    cached_in_request = _request_language.get()
    if cached_in_request is not None:
        logger.info("Using request-cached language: '%s'", cached_in_request)
        LANGUAGE_DETECTION_COUNT.labels(
            method="request_cache", detected_language=cached_in_request
        ).inc()
        LANGUAGE_DETECTION_LATENCY.labels(method="request_cache").observe(
            time.time() - start_time
        )
        return cached_in_request, False

    text_clean = text.strip()
    is_uncertain = (
        len(text_clean) < 3
        or not any(c.isalpha() for c in text_clean)
        or re.match(r"^\d+$", text_clean)
    )
    if is_uncertain and chat_id and redis_client:
        last_language = await redis_utils.safe_redis_get(
            redis_client, f"chat:{chat_id}:last_language", default="uz"
        )
        if last_language in ("ru", "uz"):
            logger.info(
                "Using last_language: '%s' for uncertain text in chat %s",
                last_language,
                chat_id,
            )
            LANGUAGE_DETECTION_COUNT.labels(
                method="last_language", detected_language=last_language
            ).inc()
            LANGUAGE_DETECTION_LATENCY.labels(method="last_language").observe(
                time.time() - start_time
            )
            _request_language.set(last_language)
            return last_language, True
        logger.info("No last_language found, defaulting to 'uz' for uncertain text")
        LANGUAGE_DETECTION_COUNT.labels(method="default", detected_language="uz").inc()
        LANGUAGE_DETECTION_LATENCY.labels(method="default").observe(
            time.time() - start_time
        )
        await redis_utils.safe_redis_set(
            redis_client, f"chat:{chat_id}:last_language", "uz", ex=3600
        )
        _request_language.set("uz")
        return "uz", True

    cache_key = f"language:{hashlib.md5(text.encode()).hexdigest()}"
    if redis_client:
        cached = await redis_utils.safe_redis_get(redis_client, cache_key)
        if cached:
            LANGUAGE_DETECTION_CACHE_HIT.inc()
            LANGUAGE_DETECTION_COUNT.labels(
                method="openai_cached", detected_language=cached
            ).inc()
            if chat_id:
                await redis_utils.safe_redis_set(
                    redis_client, f"chat:{chat_id}:last_language", cached, ex=3600
                )
            LANGUAGE_DETECTION_LATENCY.labels(method="openai_cached").observe(
                time.time() - start_time
            )
            _request_language.set(cached)
            return cached, False

    prompt = f'''Определи язык следующего текста. Верни только одно слово:
- "ru" если текст на русском языке
- "uz" если текст на узбекском языке

Если текст не является ни русским, ни узбекским, верни "uz".

Текст: "{text}"

Ответ:'''

    from app.core.dependencies import get_openai_client

    client = get_openai_client()
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=5,
        )
        if not response.choices or not response.choices[0].message.content:
            logger.error("Invalid response from OpenAI for language detection")
            LANGUAGE_DETECTION_ERRORS.labels(
                method="openai", error_type="invalid_response"
            ).inc()
            if chat_id and redis_client:
                last_language = await redis_utils.safe_redis_get(
                    redis_client, f"chat:{chat_id}:last_language", "uz"
                )
                _request_language.set(last_language)
                return last_language, False
            _request_language.set("uz")
            return "uz", False
        detected_language = response.choices[0].message.content.strip().lower()
        if detected_language not in ("ru", "uz"):
            logger.warning(
                "Invalid language detected: '%s', defaulting to 'uz'", detected_language
            )
            LANGUAGE_DETECTION_ERRORS.labels(
                method="openai", error_type="invalid_language"
            ).inc()
            detected_language = "uz"
        if not is_uncertain and redis_client:
            await redis_utils.safe_redis_set(
                redis_client, cache_key, detected_language, ex=3600
            )
        if chat_id and redis_client:
            await redis_utils.safe_redis_set(
                redis_client, f"chat:{chat_id}:last_language", detected_language, ex=3600
            )
        LANGUAGE_DETECTION_LATENCY.labels(method="openai").observe(
            time.time() - start_time
        )
        LANGUAGE_DETECTION_COUNT.labels(
            method="openai", detected_language=detected_language
        ).inc()
        _request_language.set(detected_language)
        return detected_language, False
    except httpx.TimeoutException as e:
        logger.error("OpenAI language detection timed out: %s", e)
        LANGUAGE_DETECTION_ERRORS.labels(method="openai", error_type="timeout").inc()
        if chat_id and redis_client:
            last_language = await redis_utils.safe_redis_get(
                redis_client, f"chat:{chat_id}:last_language", "uz"
            )
            _request_language.set(last_language)
            return last_language, False
        _request_language.set("uz")
        return "uz", False
    except Exception as e:
        logger.error("OpenAI language detection failed: %s", e)
        LANGUAGE_DETECTION_ERRORS.labels(
            method="openai", error_type=type(e).__name__
        ).inc()
        if chat_id and redis_client:
            last_language = await redis_utils.safe_redis_get(
                redis_client, f"chat:{chat_id}:last_language", "uz"
            )
            _request_language.set(last_language)
            return last_language, False
        _request_language.set("uz")
        return "uz", False
