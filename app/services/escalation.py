"""Эскалация на оператора и генерация выжимки диалога."""
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from redis.asyncio import Redis

from app.core.config import settings
from app.core.dependencies import get_openai_client
from app.services.session import get_escalation_keys_to_delete
from app.utils import redis_utils
from app.utils.metrics import (
    ESCALATION_COUNT,
    HIGH_LOAD_ESCALATIONS_TOTAL,
    SESSION_DURATION,
    SESSIONS_TOTAL,
    ACTIVE_CHATS,
)

logger = logging.getLogger(__name__)


@dataclass
class EscalationResult:
    status: str
    response: str
    escalation: bool
    escalation_summary: Optional[str] = None
    escalation_reason: Optional[str] = None
    tokens: int = 0


def _high_load_response(language: str) -> str:
    if language == "uz":
        return "Kechirasiz, hozirda yuklama yuqori. Iltimos, +998712020707 raqamiga qo'ng'iroq qiling."
    return "Извините, сейчас высокая нагрузка. Пожалуйста, позвоните в колл-центр по номеру +998712020707."


def _escalation_response(language: str, manual: bool = False) -> str:
    if manual:
        return (
            "Chat operatorga o'tkazildi. Tez orada siz bilan bog'lanishadi."
            if language == "uz"
            else "Чат передан оператору. Скоро с вами свяжутся."
        )
    return (
        "Operatorga o'tkazamiz. Biroz kuting, iltimos."
        if language == "uz"
        else "Передаём оператору. Пожалуйста, подождите."
    )


def _format_messages_for_summary(messages: List[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "user")
        label = "Клиент" if role == "user" else "Бот"
        lines.append(f"{label}: {msg.get('content', '')}")
    return "\n".join(lines)


def _fallback_summary(
    messages: List[dict],
    agent_state: dict,
    classification: dict,
    reason: str,
) -> str:
    parts = [
        f"Тема: {classification.get('theme', '—')}",
        f"Категория: {classification.get('category', '—')}",
        f"Сценарий: {agent_state.get('scenario_id', '—')}",
        f"Слоты: {agent_state.get('slots', {})}",
        f"Причина эскалации: {reason}",
        "Переписка:",
        _format_messages_for_summary(messages),
    ]
    text = "\n".join(parts)
    if len(text) > settings.ESCALATION_SUMMARY_MAX_CHARS:
        return text[: settings.ESCALATION_SUMMARY_MAX_CHARS] + "..."
    return text


async def build_escalation_summary(
    messages: List[dict],
    agent_state: dict,
    classification: dict,
    language: str,
    reason: str,
) -> tuple[str, int]:
    fallback = _fallback_summary(messages, agent_state, classification, reason)
    try:
        client = get_openai_client()
        dialog = _format_messages_for_summary(messages)
        prompt = (
            "Составь краткую выжимку диалога для оператора колл-центра Paynet на русском языке.\n"
            "Формат: Тема / Тип клиента / Суть проблемы / Что уже сделал бот / Причина эскалации.\n"
            f"Язык клиента: {'узбекский' if language == 'uz' else 'русский'}\n"
            f"Классификация: {classification}\n"
            f"Состояние агента: {agent_state}\n"
            f"Причина эскалации: {reason}\n"
            f"Переписка:\n{dialog}\n"
            "Выжимка:"
        )
        response = await client.chat.completions.create(
            model=settings.ESCALATION_SUMMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        text = (response.choices[0].message.content or "").strip() or fallback
        tokens = response.usage.total_tokens if response.usage else 0
        if len(text) > settings.ESCALATION_SUMMARY_MAX_CHARS:
            text = text[: settings.ESCALATION_SUMMARY_MAX_CHARS] + "..."
        return text, tokens
    except Exception as e:
        logger.warning("Escalation summary LLM failed: %s", e)
        return fallback, 0


async def perform_escalation(
    redis_client: Redis,
    chat_id: str,
    language: str,
    reason: str,
    *,
    messages: List[dict],
    agent_state: dict,
    classification: dict,
    manual: bool = False,
    increment_count: bool = True,
) -> EscalationResult:
    escalation_count = int(
        await redis_utils.safe_redis_get(redis_client, f"chat:{chat_id}:escalation_count", "0") or "0"
    )
    if increment_count:
        escalation_count = await redis_utils.safe_redis_incr(
            redis_client, f"chat:{chat_id}:escalation_count"
        )

    if escalation_count >= 3:
        HIGH_LOAD_ESCALATIONS_TOTAL.inc()
        return EscalationResult(
            status="high_load",
            response=_high_load_response(language),
            escalation=False,
            escalation_reason=reason,
        )

    ESCALATION_COUNT.inc()
    summary, tokens = await build_escalation_summary(
        messages, agent_state, classification, language, reason
    )
    await redis_utils.safe_redis_set(
        redis_client, f"chat:{chat_id}:escalation_summary", summary, ex=3600
    )

    start_time_str = await redis_utils.safe_redis_get(redis_client, f"chat:{chat_id}:start_time")
    if start_time_str:
        SESSION_DURATION.observe(time.time() - float(start_time_str))
        SESSIONS_TOTAL.inc()
    await redis_utils.safe_redis_delete(redis_client, *get_escalation_keys_to_delete(chat_id))
    active_chats = len(await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:active"))
    ACTIVE_CHATS.set(active_chats)

    return EscalationResult(
        status="escalation",
        response=_escalation_response(language, manual=manual),
        escalation=True,
        escalation_summary=summary,
        escalation_reason=reason,
        tokens=tokens,
    )


def enrich_classification_for_escalation(
    classification: dict,
    escalation_summary: Optional[str],
    escalation_reason: Optional[str],
    tools_used: Optional[List[str]] = None,
) -> dict:
    enriched = dict(classification)
    if escalation_summary:
        enriched["escalation_summary"] = escalation_summary
    if escalation_reason:
        enriched["escalation_reason"] = escalation_reason
    if tools_used:
        enriched["tools_used"] = ", ".join(tools_used)
    return enriched
