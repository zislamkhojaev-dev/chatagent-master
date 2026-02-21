"""Генерация ответа (OpenAI) с опциональным языком и контекстом чата. ТЗ Б.3, Б.5, Б.7."""
import logging
from typing import List, Optional

import httpx
from openai import APIError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.dependencies import get_openai_client

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(settings.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=settings.MIN_WAIT, max=settings.MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
)
async def generate_response(
    context: List[str],
    query: str,
    history: List[str],
    language: str,
    chat_context: Optional[str] = None,
) -> tuple[str, int]:
    """
    Генерирует ответ по RAG-контексту и истории.
    language: "uz" | "ru" (опционально передаётся из API по Б.3).
    chat_context: сохранённый контекст диалога из Redis (Б.5), подставляется в промпт.
    """
    logger.info("Generating response for language: %s", language)
    uz_instruction = (
        "MUHIM: FAQAT O'ZBEK TILIDA VA FAQAT LOTIN YOZUVIDA javob bering!\n"
        "To'g'ri misol: \"To'lov qilish uchun...\"\n"
        "Noto'g'ri misol: \"Тўлов қилиш учун...\" (kirillitsa ishlatmang!)\n"
        "Noto'g'ri misol: \"Оплата через...\" (rus tilida yozmang!)\n"
        "Faqat lotin harflari: a, b, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, x, y, z\n"
        "O'zbek maxsus belgilar: o', g', sh, ch, ng"
    )
    ru_instruction = "Отвечай на русском языке, используя кириллицу."
    language_instruction = uz_instruction if language == "uz" else ru_instruction
    history_context = (
        f"Предыдущие сообщения: {' | '.join(history[-2:])}"
        if history and len(history) > 1
        else ""
    )
    chat_context_block = (
        f"\nКонтекст диалога: {chat_context}\n" if chat_context else ""
    )

    if language == "uz":
        prompt = (
            "Siz Paynet texnik yordam xizmati chat-botisiz. Quyidagi bilimlar bazasi ma'lumotlari asosida javob bering.\n"
            "JUDA MUHIM: Javobingizni FAQAT O'ZBEK TILIDA va FAQAT LOTIN YOZUVIDA yozing!\n"
            "QOIDALAR:\n"
            "1. Agar bu avvalgi savolga aniqlik kiritilsa - dialog tarixi asosida javob bering\n"
            "2. Agar bu yangi savol bo'lsa, bilimlar bazasiga javob bering\n"
            "3. Ma'lumotni faqat quyidagi kontekstdan foydalaning\n"
            "4. Agar ma'lumot etarli bo'lmasa, savolni aniqlashtirishni yoki operatorga murojaat qilishni taklif qiling\n"
            "5. Muloyim va aniq bo'ling\n"
            "6. Amaliy ko'rsatmalar bering\n"
            f"Bilimlar bazasi:{context}\n"
            f"{chat_context_block}"
            f"{history_context}\n"
            f"Foydalanuvchi savoli: {query}\n"
            "O'zbek tilida lotin yozuvida javob:"
        )
    else:
        prompt = (
            "Ты — чат-бот службы поддержки Paynet. Твоя задача помочь пользователю с услугами компании.\n"
            f"{language_instruction}\n"
            "ПРАВИЛА:\n"
            "1. Если это уточнение предыдущего вопроса - отвечайте на основе истории диалога\n"
            "2. Если это новый вопрос - отвечай по базе знаний\n"
            "3. Используй информацию ТОЛЬКО из контекста ниже\n"
            "4. Если информации недостаточно - предложи уточнить вопрос или обратиться к оператору\n"
            "5. Будь вежливым и конкретным\n"
            "6. Давай практичные инструкции\n"
            "7. Если контекст пуст или не относится к вопросу, НЕ ГОВОРИ, что не можешь помочь.\n"
            f"Контекст из базы знаний:{context}\n"
            f"{chat_context_block}"
            f"{history_context}\n"
            f"Запрос пользователя: {query}\n"
            "Ответ на русском языке:"
        )
    client = get_openai_client()
    response = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": prompt}],
        max_tokens=600,
        temperature=0.6,
    )
    if not response.choices or not response.choices[0].message.content:
        raise APIError("No completion data in OpenAI response")
    ai_response = response.choices[0].message.content.strip()
    if language == "uz":
        cyrillic_replacements = {
            "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
            "Ў": "O'", "Қ": "Q", "Ғ": "G'", "Ҳ": "H",
        }
        for cyr, lat in cyrillic_replacements.items():
            ai_response = ai_response.replace(cyr, lat)
    logger.info("Generated response for %s: %s...", language, ai_response[:100])
    return ai_response, response.usage.total_tokens
