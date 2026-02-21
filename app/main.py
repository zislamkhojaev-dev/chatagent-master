"""FastAPI-приложение: точка входа, lifespan, роутеры. ТЗ Б.7."""
import asyncio
import logging
import os

import asyncpg
from contextlib import asynccontextmanager
from fastapi import FastAPI
from redis.asyncio import Redis
from prometheus_client import start_http_server

from app.core.config import settings
from app.api import process, health, synonyms, language, admin
from app.services.session import cleanup_inactive_sessions
from app.utils.synonyms import load_synonyms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация БД, Redis, БЗ, Prometheus, фоновых задач."""
    logger.info("Starting application lifespan")
    try:
        load_synonyms()
    except Exception as e:
        logger.error("Failed to load synonyms: %s", e)

    db_pool = None
    try:
        db_pool = await asyncpg.create_pool(
            database=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            min_size=1,
            max_size=20,
        )
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id SERIAL PRIMARY KEY,
                    chat_id VARCHAR(255),
                    message TEXT,
                    response TEXT,
                    classification JSONB,
                    tokens INT,
                    escalation BOOLEAN,
                    timestamp TIMESTAMP
                )
            """)
        logger.info("Database pool and interactions table ready")
    except Exception as e:
        logger.error("Error initializing database: %s", e)

    redis_client = await Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    await redis_client.ping()
    logger.info("Redis connected")

    knowledge_base = []
    qdrant_client = None
    qdrant_collection = None
    bm25_index = None
    try:
        from app.services.qdrant_store import get_qdrant_client, get_collection_by_alias
        from app.services.knowledge_base import load_knowledge_base_chunks
        from app.services.bm25_store import build_bm25
        qdrant_client = get_qdrant_client()
        if qdrant_client and get_collection_by_alias(qdrant_client, settings.QDRANT_ALIAS):
            chunks = load_knowledge_base_chunks()
            if chunks:
                bm25_index = build_bm25(chunks)
                qdrant_collection = settings.QDRANT_ALIAS
                knowledge_base = chunks
                logger.info("Knowledge base loaded from Qdrant + BM25 (%s chunks)", len(chunks))
        if not knowledge_base:
            logger.info("RAG disabled: no Qdrant collection or chunks. Use /admin/kb to upload and index PDF.")
    except Exception as e:
        logger.error("Error loading knowledge base: %s", e)

    try:
        start_http_server(int(settings.PROMETHEUS_PORT))
        logger.info("Prometheus metrics server started on port %s", settings.PROMETHEUS_PORT)
    except Exception as e:
        logger.error("Failed to start Prometheus server: %s", e)

    asyncio.create_task(cleanup_inactive_sessions(redis_client))
    logger.info("Session cleanup task started")

    app.state.redis = redis_client
    app.state.db_pool = db_pool
    app.state.knowledge_base = knowledge_base
    app.state.qdrant_client = qdrant_client
    app.state.qdrant_collection = qdrant_collection
    app.state.bm25_index = bm25_index
    app.state.kb_index_ready = bool(knowledge_base)

    yield

    if db_pool:
        await db_pool.close()
        logger.info("Database pool closed")
    await redis_client.aclose()
    logger.info("Redis client closed")


app = FastAPI(
    title="Paynet RAG Chatbot",
    description="Чат-бот колл-центра Paynet (ru/uz), RAG, эскалация.",
    lifespan=lifespan,
)

app.include_router(process.router, tags=["process"])
app.include_router(health.router, tags=["health"])
app.include_router(synonyms.router, tags=["synonyms"])
app.include_router(language.router, tags=["language"])
app.include_router(admin.router, tags=["admin"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        log_level="info",
    )
