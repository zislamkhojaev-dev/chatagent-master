"""GET /health, GET /stats. ТЗ Б.7."""
from datetime import datetime

from fastapi import APIRouter, Request
from app.utils import redis_utils
from app.utils.synonyms import synonyms_dict, synonyms_last_modified, SYNONYMS_FILE_PATH
import os

router = APIRouter()


@router.get("/health")
async def health_check(request: Request):
    redis_client = request.app.state.redis
    db_pool = request.app.state.db_pool
    redis_status = "healthy" if await redis_utils.safe_redis_ping(redis_client) else "unhealthy"
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("SELECT 1")
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
    kb_ready = getattr(request.app.state, "kb_index_ready", False) and (request.app.state.knowledge_base or [])
    kb_status = "healthy" if kb_ready else "unhealthy"
    synonyms_status = (
        "healthy"
        if synonyms_dict and ("ru" in synonyms_dict or "uz" in synonyms_dict)
        else "unhealthy"
    )
    return {
        "status": "healthy"
        if all(s == "healthy" for s in [redis_status, db_status, kb_status, synonyms_status])
        else "unhealthy",
        "components": {
            "redis": redis_status,
            "database": db_status,
            "knowledge_base": kb_status,
            "synonyms": synonyms_status,
        },
        "language_detection": "openai_api_with_history",
        "supported_languages": ["Russian", "Uzbek"],
        "synonyms_loaded": {
            "ru": len(synonyms_dict.get("ru", {})),
            "uz": len(synonyms_dict.get("uz", {})),
        },
    }


@router.get("/stats")
async def get_stats(request: Request):
    redis_client = request.app.state.redis
    knowledge_base = request.app.state.knowledge_base or []
    active_chats = len(await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:active"))
    total_seen_chats = len(await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:seen"))
    keys = await redis_utils.safe_redis_scan_iter(redis_client, "chat:*:escalation_count")
    escalation_counts = {}
    for key in keys:
        chat_id = key.split(":")[1]
        count = int(await redis_utils.safe_redis_get(redis_client, key, "0") or "0")
        escalation_counts[chat_id] = count
    return {
        "active_chats": active_chats,
        "total_seen_chats": total_seen_chats,
        "escalation_counts": escalation_counts,
        "knowledge_base_chunks": len(knowledge_base),
        "language_detection_method": "openai_api_with_history",
        "supported_languages": ["ru", "uz"],
        "bilingual_support": True,
        "synonyms": {
            "ru_count": len(synonyms_dict.get("ru", {})),
            "uz_count": len(synonyms_dict.get("uz", {})),
            "last_updated": datetime.fromtimestamp(synonyms_last_modified).isoformat()
            if synonyms_last_modified
            else None,
        },
    }
