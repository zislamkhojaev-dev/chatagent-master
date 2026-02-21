"""Логирование взаимодействий в PostgreSQL. ТЗ Б.7."""
import json
import logging
from datetime import datetime
from typing import Any, Dict

import asyncpg

logger = logging.getLogger(__name__)


async def log_interaction(
    pool: asyncpg.Pool,
    chat_id: str,
    message: str,
    response: str,
    classification: Dict[str, str],
    tokens: int,
    escalation: bool,
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO interactions (chat_id, message, response, classification, tokens, escalation, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                chat_id,
                message,
                response,
                json.dumps(classification),
                tokens,
                escalation,
                datetime.now(),
            )
    except Exception as e:
        logger.error("Error logging interaction: %s", e)
