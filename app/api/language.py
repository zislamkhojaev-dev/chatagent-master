"""GET /language_detection_stats, POST /clear_language_cache. ТЗ Б.7."""
from fastapi import APIRouter
from app.core.dependencies import get_redis
from fastapi import Depends
from redis.asyncio import Redis
from app.utils import redis_utils

router = APIRouter()


@router.get("/language_detection_stats")
async def language_detection_stats(redis_client: Redis = Depends(get_redis)):
    cache_keys = await redis_utils.safe_redis_scan_iter(redis_client, "language:*")
    cache_stats = {"total_cached": len(cache_keys)}
    cached_languages = {"ru": 0, "uz": 0}
    for key in cache_keys[:100]:
        lang = await redis_utils.safe_redis_get(redis_client, key)
        if lang in cached_languages:
            cached_languages[lang] += 1
    cache_stats.update(cached_languages)
    return {
        "cache_statistics": cache_stats,
        "detection_methods": {
            "primary": "openai_api (gpt-4o-mini)",
            "history": "last message language from Redis",
            "fallback": "default to uz",
            "default": "uz",
        },
        "supported_languages": ["ru", "uz"],
        "cache_ttl_seconds": 3600,
    }


@router.post("/clear_language_cache")
async def clear_language_cache(redis_client: Redis = Depends(get_redis)):
    cache_keys = await redis_utils.safe_redis_scan_iter(redis_client, "language:*")
    if cache_keys:
        await redis_utils.safe_redis_delete(redis_client, *cache_keys)
        cleared_count = len(cache_keys)
    else:
        cleared_count = 0
    return {
        "status": "success",
        "cleared_entries": cleared_count,
        "message": f"Cleared {cleared_count} language cache entries",
    }
