"""Безопасные обёртки над Redis (ТЗ Б.7 app/utils/redis_utils.py)."""
import logging
from typing import List, Optional

from redis.asyncio import Redis
from redis.asyncio import RedisError

logger = logging.getLogger(__name__)


async def safe_redis_get(redis_client: Redis, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        result = await redis_client.get(key) or default
        return result
    except RedisError as e:
        logger.warning("Redis unavailable for get %s: %s", key, e)
        return default


async def safe_redis_set(
    redis_client: Redis, key: str, value: str, ex: Optional[int] = None
) -> None:
    try:
        if ex:
            await redis_client.setex(key, ex, value)
        else:
            await redis_client.set(key, value)
    except RedisError as e:
        logger.warning("Redis unavailable for set %s: %s", key, e)


async def safe_redis_incr(redis_client: Redis, key: str) -> int:
    try:
        result = await redis_client.incr(key)
        return result
    except RedisError as e:
        logger.warning("Redis unavailable for incr %s: %s", key, e)
        return 0


async def safe_redis_delete(redis_client: Redis, *keys: str) -> None:
    try:
        await redis_client.delete(*keys)
    except RedisError as e:
        logger.warning("Redis unavailable for delete %s: %s", keys, e)


async def safe_redis_lpush(redis_client: Redis, key: str, value: str) -> None:
    try:
        await redis_client.lpush(key, value)
    except RedisError as e:
        logger.warning("Redis unavailable for lpush %s: %s", key, e)


async def safe_redis_ltrim(redis_client: Redis, key: str, start: int, end: int) -> None:
    try:
        await redis_client.ltrim(key, start, end)
    except RedisError as e:
        logger.warning("Redis unavailable for ltrim %s: %s", key, e)


async def safe_redis_lrange(redis_client: Redis, key: str, start: int, end: int) -> List[str]:
    try:
        result = await redis_client.lrange(key, start, end)
        return result
    except RedisError as e:
        logger.warning("Redis unavailable for lrange %s: %s", key, e)
        return []


async def safe_redis_scan_iter(redis_client: Redis, pattern: str) -> List[str]:
    try:
        keys = []
        async for key in redis_client.scan_iter(match=pattern, count=1000):
            keys.append(key)
        return keys
    except RedisError as e:
        logger.warning("Redis unavailable for scan_iter %s: %s", pattern, e)
        return []


async def safe_redis_ping(redis_client: Redis) -> bool:
    try:
        await redis_client.ping()
        return True
    except RedisError:
        return False
