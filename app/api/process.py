"""POST /process_message, POST /escalate."""
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis
from sklearn.metrics.pairwise import cosine_similarity

from app.models import MessageRequest, MessageResponse, EscalateRequest, EscalateResponse
from app.services.agent.orchestrator import AgentContext, run_agent
from app.services.anonymization import anonymize_message
from app.services.classification import classify_query
from app.services.constants import OPERATOR_KEYWORDS
from app.services.embeddings import get_embedding
from app.services.escalation import enrich_classification_for_escalation, perform_escalation
from app.services.language import detect_language_openai, set_request_language
from app.services.logging_interaction import log_interaction
from app.services.scenarios import should_auto_escalate_category
from app.services.context import schedule_context_update
from app.services.session import (
    append_assistant_message,
    get_agent_state,
    get_messages,
    update_session_and_get_history,
)
from app.utils import redis_utils
from app.utils.metrics import (
    REQUEST_COUNT,
    REQUEST_LATENCY,
    UNIQUE_CHATS_TOTAL,
    UNCERTAIN_REQUESTS,
)
from app.core.config import settings
from app.core.dependencies import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_rudeness_classification(classification: dict) -> bool:
    theme = classification.get("theme", "").lower()
    category = classification.get("category", "").lower()
    return "хулиганство" in theme or "bezorilik" in theme or "bezorilik" in category


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

        if request.language in ("uz", "ru"):
            set_request_language(request.language)
            language = request.language
            is_uncertain = False
        else:
            language, is_uncertain = await detect_language_openai(
                message, chat_id, redis_client
            )
        await redis_utils.safe_redis_set(redis_client, f"chat:{chat_id}:last_language", language)
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

        history, last_embedding_str, count_str, escalation_count_str, seen_is_new, messages = (
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
        agent_state = await get_agent_state(redis_client, chat_id)

        # Hard escalation guards
        hard_reason = None
        if any(kw in anonymized_message.lower() for kw in OPERATOR_KEYWORDS):
            hard_reason = "user_request"
        elif count >= 3:
            hard_reason = "repeat"
        elif _is_rudeness_classification(classification) or should_auto_escalate_category(classification):
            hard_reason = "rudeness"

        if hard_reason:
            esc = await perform_escalation(
                redis_client,
                chat_id,
                language,
                hard_reason,
                messages=messages,
                agent_state=agent_state,
                classification=classification,
            )
            log_class = enrich_classification_for_escalation(
                classification, esc.escalation_summary, esc.escalation_reason
            )
            await log_interaction(
                req.app.state.db_pool, chat_id, anonymized_message, esc.response,
                log_class, esc.tokens, esc.escalation, language,
            )
            return MessageResponse(
                status=esc.status,
                chat_id=chat_id,
                response=esc.response,
                classification=log_class,
                escalation=esc.escalation,
                history=history,
                mode="escalation",
                escalation_summary=esc.escalation_summary,
                escalation_reason=esc.escalation_reason,
            )

        if is_uncertain:
            UNCERTAIN_REQUESTS.inc()

        if not settings.AGENT_ENABLED:
            raise HTTPException(status_code=503, detail="Agent mode disabled")

        agent_result = await run_agent(AgentContext(
            chat_id=chat_id,
            message=anonymized_message,
            language=language,
            messages=messages,
            classification=classification,
            app_state=req.app.state,
            redis_client=redis_client,
        ))

        await append_assistant_message(redis_client, chat_id, agent_result.text)

        log_class = enrich_classification_for_escalation(
            classification,
            agent_result.escalation_summary,
            agent_result.escalation_reason,
            agent_result.tools_used,
        )
        await log_interaction(
            req.app.state.db_pool, chat_id, anonymized_message, agent_result.text,
            log_class, agent_result.tokens, agent_result.escalation, language,
        )
        if not agent_result.escalation:
            schedule_context_update(redis_client, chat_id, anonymized_message, agent_result.text)

        return MessageResponse(
            status=agent_result.status,
            chat_id=chat_id,
            response=agent_result.text,
            classification=log_class,
            escalation=agent_result.escalation,
            history=history,
            mode=agent_result.mode,
            tools_used=agent_result.tools_used,
            escalation_summary=agent_result.escalation_summary,
            escalation_reason=agent_result.escalation_reason,
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
        messages = await get_messages(redis_client, chat_id)
        last_language = await redis_utils.safe_redis_get(
            redis_client, f"chat:{chat_id}:last_language", "uz"
        ) or "uz"
        agent_state = await get_agent_state(redis_client, chat_id)
        history = [m["content"] for m in messages if m.get("role") == "user"]
        classification = {
            "theme": "Запрос оператора",
            "category": "Эскалация",
            "subcategory": "Ручная эскалация",
        }

        esc = await perform_escalation(
            redis_client,
            chat_id,
            last_language,
            "user_request",
            messages=messages,
            agent_state=agent_state,
            classification=classification,
            manual=True,
            increment_count=True,
        )

        seen = await redis_utils.safe_redis_get(redis_client, f"chat:{chat_id}:seen")
        if seen is None:
            await redis_utils.safe_redis_set(redis_client, f"chat:{chat_id}:seen", "1")
            UNIQUE_CHATS_TOTAL.inc()

        log_class = enrich_classification_for_escalation(
            classification, esc.escalation_summary, esc.escalation_reason
        )
        await log_interaction(
            req.app.state.db_pool, chat_id, "Manual escalation", esc.response,
            log_class, esc.tokens, esc.escalation, last_language,
        )

        return EscalateResponse(
            status=esc.status,
            chat_id=chat_id,
            response=esc.response,
            history=history,
            escalation_summary=esc.escalation_summary,
            escalation_reason=esc.escalation_reason,
        )
