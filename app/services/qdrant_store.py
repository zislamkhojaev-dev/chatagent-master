"""Работа с Qdrant: коллекции, alias, upsert, поиск. ТЗ Б.1."""
import logging
import time
from typing import Any, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.models import PointStruct

from app.core.config import settings

logger = logging.getLogger(__name__)

VECTOR_SIZE = 1536  # OpenAI text-embedding-ada-002


def get_qdrant_client() -> Optional[QdrantClient]:
    """Создаёт клиент Qdrant. При ошибке подключения возвращает None."""
    try:
        client = QdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
            timeout=10.0,
        )
        client.get_collections()
        return client
    except Exception as e:
        logger.warning("Qdrant unavailable: %s", e)
        return None


def qdrant_available(client: Optional[QdrantClient]) -> bool:
    if client is None:
        return False
    try:
        client.get_collections()
        return True
    except Exception:
        return False


def create_collection(
    client: QdrantClient,
    collection_name: str,
    vector_size: int = VECTOR_SIZE,
) -> None:
    client.recreate_collection(
        collection_name=collection_name,
        vectors_config=qdrant_models.VectorParams(
            size=vector_size,
            distance=qdrant_models.Distance.COSINE,
        ),
    )
    logger.info("Created Qdrant collection %s", collection_name)


def upsert_points(
    client: QdrantClient,
    collection_name: str,
    ids: List[int],
    vectors: List[List[float]],
    payloads: List[dict],
) -> None:
    points = [
        PointStruct(id=uid, vector=vec, payload=payload)
        for uid, vec, payload in zip(ids, vectors, payloads)
    ]
    client.upsert(collection_name=collection_name, points=points, wait=True)
    logger.info("Upserted %s points to %s", len(points), collection_name)


def set_alias(client: QdrantClient, collection_name: str, alias: str) -> None:
    client.update_collection_aliases(
        change_aliases=[
            qdrant_models.AliasOperations(
                create_alias=qdrant_models.CreateAlias(
                    collection_name=collection_name,
                    alias_name=alias,
                )
            )
        ]
    )
    logger.info("Set alias %s -> %s", alias, collection_name)


def get_collection_by_alias(client: QdrantClient, alias: str) -> Optional[str]:
    try:
        aliases = client.get_aliases()
        for a in aliases.aliases:
            if a.alias_name == alias:
                return a.collection_name
        return None
    except Exception as e:
        logger.warning("Failed to get alias %s: %s", alias, e)
        return None


def search(
    client: QdrantClient,
    collection_name: str,
    query_vector: List[float],
    limit: int = 20,
) -> List[tuple[Any, float, dict]]:
    """Возвращает список (id, score, payload)."""
    results = client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=limit,
    )
    return [(r.id, r.score, r.payload or {}) for r in results]


def scroll_collection(
    client: QdrantClient,
    collection_name: str,
    limit: int = 10000,
) -> List[tuple[Any, dict]]:
    """Возвращает (id, payload) для всех точек (для загрузки чанков)."""
    points, _ = client.scroll(
        collection_name=collection_name,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return [(p.id, p.payload or {}) for p in points]
