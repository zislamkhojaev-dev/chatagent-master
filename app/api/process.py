"""POST /process_message, POST /escalate. ТЗ Б.7, Б.3 (опциональный language)."""
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis
from sklearn.metrics.pairwise import cosine_similarity

from app.models import MessageRequest, MessageResponse, EscalateRequest, EscalateResponse
from app.services.anonymization import anonymize_message
from app.services.classification import classify_query
from app.services.constants import OPERATOR_KEYWORDS
from app.services.embeddings import get_embedding
from app.services.language import detect_language_openai, set_request_language
from app.services.logging_interaction import log_interaction
from app.services.response import generate_response
from app.services.search import find_relevant_context
from app.services.context import schedule_context_update
from app.services.session import (
    update_session_and_get_history,
    get_escalation_keys_to_delete,
)
from app.utils import redis_utils
from app.utils.metrics import (
    REQUEST_COUNT,
    REQUEST_LATENCY,
    ESCALATION_COUNT,
    HIGH_LOAD_ESCALATIONS_TOTAL,
    UNIQUE_CHATS_TOTAL,
    ACTIVE_CHATS,
    SESSIONS_TOTAL,
    SESSION_DURATION,
    UNCERTAIN_REQUESTS,
)
from app.core.config import settings
from app.core.dependencies import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/process_message", response_model=MessageResponse)
async def process_message(
    request: MessageRequest,
    req: Request,
    redis_client: Redis = Depends(get_redis),
):
    REQUEST_COUNT.labels(endpoint="/process_message").inc()
    with REQUEST_LATENCY.labels(endpoint="/process_message").time():
        chat_id = request.chat_id
        message = request.message
        logger.info("Processing message: chat_id=%s, message=%s", chat_id, message)
        if not chat_id or not message:
            raise HTTPException(status_code=400, detail="Missing chat_id or message")

        # Б.3: если передан language — установить контекст и не вызывать детекцию
        if request.language in ("uz", "ru"):
            set_request_language(request.language)
            language = request.language
            is_uncertain = False
        else:
            language, is_uncertain = await detect_language_openai(
                message, chat_id, redis_client
            )
        logger.info("Language: %s, is_uncertain: %s", language, is_uncertain)

        anonymized_message = anonymize_message(message)
        is_pii_only = not anonymized_message.strip() or anonymized_message.strip() in ("***", "")
        if is_pii_only:
            pii_response = (
                "Xabar faqat shaxsiy ma'lumotlarni o'z ichiga oladi. Iltimos, shaxsiy ma'lumotlarsiz savol bering."
                if language == "uz"
                else "Сообщение содержит только персональные данные. Пожалуйста, задайте вопрос без личных данных."
            )
            return MessageResponse(
                status="success",
                chat_id=chat_id,
                response=pii_response,
                classification={"theme": "Ошибка", "category": "PII", "subcategory": "Анонимизация не удалась"},
                escalation=False,
                history=[],
            )

        history, last_embedding_str, count_str, escalation_count_str, seen_is_new = (
            await update_session_and_get_history(redis_client, chat_id, anonymized_message)
        )
        if seen_is_new:
            UNIQUE_CHATS_TOTAL.inc()

        embedding = await get_embedding(anonymized_message, redis_client)
        last_embedding = json.loads(last_embedding_str)
        count = int(count_str)
        if last_embedding and cosine_similarity([embedding], [last_embedding])[0][0] > settings.SIMILARITY_THRESHOLD:
            count = await redis_utils.safe_redis_incr(redis_client, f"chat:{chat_id}:count")
        else:
            await redis_utils.safe_redis_set(redis_client, f"chat:{chat_id}:count", "1")
            count = 1
        await redis_utils.safe_redis_set(redis_client, f"chat:{chat_id}:embedding", json.dumps(embedding))

        classification = await classify_query(anonymized_message, language, redis_client)
        escalation_count = int(escalation_count_str)

        if any(kw in anonymized_message.lower() for kw in OPERATOR_KEYWORDS) or count >= 3:
            escalation_count = await redis_utils.safe_redis_incr(
                redis_client, f"chat:{chat_id}:escalation_count"
            )
            if escalation_count >= 3:
                HIGH_LOAD_ESCALATIONS_TOTAL.inc()
                high_load_response = (
                    "Kechirasiz, hozirda yuklama yuqori. Iltimos, +998712020707 raqamiga qo'ng'iroq qiling."
                    if language == "uz"
                    else "Извините, сейчас высокая нагрузка. Пожалуйста, позвоните в колл-центр по номеру +998712020707."
                )
                await log_interaction(
                    req.app.state.db_pool, chat_id, anonymized_message, high_load_response,
                    {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Высокая нагрузка"},
                    0, False,
                )
                return MessageResponse(
                    status="high_load",
                    chat_id=chat_id,
                    response=high_load_response,
                    classification={"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Высокая нагрузка"},
                    escalation=False,
                    history=history,
                )
            ESCALATION_COUNT.inc()
            escalation_response = (
                "Operatorga o'tkazamiz. Biroz kuting, iltimos."
                if language == "uz"
                else "Передаём оператору. Пожалуйста, подождите."
            )
            await log_interaction(
                req.app.state.db_pool, chat_id, anonymized_message, escalation_response,
                {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Передача оператору"},
                0, True,
            )
            start_time_str = await redis_utils.safe_redis_get(
                redis_client, f"chat:{chat_id}:start_time"
            )
            if start_time_str:
                SESSION_DURATION.observe(time.time() - float(start_time_str))
                SESSIONS_TOTAL.inc()
            await redis_utils.safe_redis_delete(
                redis_client, *get_escalation_keys_to_delete(chat_id)
            )
            active_chats = len(await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:active"))
            ACTIVE_CHATS.set(active_chats)
            return MessageResponse(
                status="escalation",
                chat_id=chat_id,
                response=escalation_response,
                classification={"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Передача оператору"},
                escalation=True,
                history=history,
            )

        if is_uncertain:
            logger.info("Uncertain text, skipping context search for chat %s", chat_id)
            UNCERTAIN_REQUESTS.inc()
            context = []
        else:
            kb_chunks = req.app.state.knowledge_base or []
            qdrant_client = getattr(req.app.state, "qdrant_client", None)
            qdrant_collection = getattr(req.app.state, "qdrant_collection", None)
            bm25_index = getattr(req.app.state, "bm25_index", None)
            context = await find_relevant_context(
                anonymized_message,
                embedding,
                kb_chunks,
                language,
                qdrant_client=qdrant_client,
                qdrant_collection=qdrant_collection,
                bm25_index=bm25_index,
                top_k=5,
            )
        chat_context = await redis_utils.safe_redis_get(
            redis_client, f"chat:{chat_id}:context", default=""
        ) or ""
        ai_response, tokens = await generate_response(
            context, anonymized_message, history, language, chat_context=chat_context or None
        )
        await log_interaction(
            req.app.state.db_pool, chat_id, anonymized_message, ai_response,
            classification, tokens, False,
        )
        schedule_context_update(redis_client, chat_id, anonymized_message, ai_response)
        return MessageResponse(
            status="success",
            chat_id=chat_id,
            response=ai_response,
            classification=classification,
            escalation=False,
            history=history,
        )


@router.post("/escalate", response_model=EscalateResponse)
async def escalate(
    request: EscalateRequest,
    req: Request,
    redis_client: Redis = Depends(get_redis),
):
    REQUEST_COUNT.labels(endpoint="/escalate").inc()
    with REQUEST_LATENCY.labels(endpoint="/escalate").time():
        chat_id = request.chat_id
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.lrange(f"chat:{chat_id}:history", 0, -1)
            pipe.get(f"chat:{chat_id}:last_language")
            pipe.incr(f"chat:{chat_id}:escalation_count")
            results = await pipe.execute()
        history = list(results[0] or [])
        last_language = results[1] or "uz"
        escalation_count = int(results[2])
        if escalation_count >= 3:
            HIGH_LOAD_ESCALATIONS_TOTAL.inc()
            high_load_response = (
                "Kechirasiz, hozirda yuklama yuqori. Iltimos, +998712020707 raqamiga qo'ng'iroq qiling."
                if last_language == "uz"
                else "Извините, сейчас высокая нагрузка. Пожалуйста, позвоните в колл-центр по номеру +998712020707."
            )
            await log_interaction(
                req.app.state.db_pool, chat_id, "Manual escalation", high_load_response,
                {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Высокая нагрузка"},
                0, False,
            )
            return EscalateResponse(
                status="high_load",
                chat_id=chat_id,
                response=high_load_response,
                history=history,
            )
        ESCALATION_COUNT.inc()
        escalation_response = (
            "Chat operatorga o'tkazildi. Tez orada siz bilan bog'lanishadi."
            if last_language == "uz"
            else "Чат передан оператору. Скоро с вами свяжутся."
        )
        await log_interaction(
            req.app.state.db_pool, chat_id, "Manual escalation", escalation_response,
            {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Передача оператору"},
            0, True,
        )
        seen = await redis_utils.safe_redis_get(redis_client, f"chat:{chat_id}:seen")
        if seen is None:
            await redis_utils.safe_redis_set(redis_client, f"chat:{chat_id}:seen", "1")
            UNIQUE_CHATS_TOTAL.inc()
        start_time_str = await redis_utils.safe_redis_get(
            redis_client, f"chat:{chat_id}:start_time"
        )
        if start_time_str:
            SESSION_DURATION.observe(time.time() - float(start_time_str))
            SESSIONS_TOTAL.inc()
            await redis_utils.safe_redis_delete(
                redis_client, *get_escalation_keys_to_delete(chat_id)
            )
        active_chats = len(await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:active"))
        ACTIVE_CHATS.set(active_chats)
        return EscalateResponse(
            status="success",
            chat_id=chat_id,
            response=escalation_response,
            history=history,
        )
