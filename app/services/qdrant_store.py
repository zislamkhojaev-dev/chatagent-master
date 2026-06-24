"""Работа с Qdrant: коллекции, alias, upsert, поиск. ТЗ Б.1."""
import logging
import time
from typing import Any, List, Optional

import httpx
from qdrant_client import QdrantClient

from app.core.config import settings

logger = logging.getLogger(__name__)

VECTOR_SIZE = settings.OPENAI_EMBEDDING_DIMENSIONS  # синхронно с эмбеддингами OpenAI


def _qdrant_base() -> str:
    return f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"


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
    client: Optional[QdrantClient],
    collection_name: str,
    vector_size: int = VECTOR_SIZE,
) -> None:
    """Создание коллекции через REST API (обход Union в qdrant-client)."""
    base = _qdrant_base()
    url = f"{base}/collections/{collection_name}"
    body = {"vectors": {"size": vector_size, "distance": "Cosine"}}
    with httpx.Client(timeout=30.0) as http:
        # Удалить старую коллекцию с таким именем, если есть
        r = http.delete(url)
        if r.status_code not in (200, 404):
            r.raise_for_status()
        resp = http.put(url, json=body)
        resp.raise_for_status()
    logger.info("Created Qdrant collection %s", collection_name)


def upsert_points(
    client: Optional[QdrantClient],
    collection_name: str,
    ids: List[int],
    vectors: List[List[float]],
    payloads: List[dict],
) -> None:
    """Загрузка точек через REST API (обход ошибки «Cannot instantiate typing.Union» в qdrant-client)."""
    base = _qdrant_base()
    url = f"{base}/collections/{collection_name}/points?wait=true"
    body = {
        "points": [
            {"id": uid, "vector": vec, "payload": payload}
            for uid, vec, payload in zip(ids, vectors, payloads)
        ]
    }
    with httpx.Client(timeout=60.0) as http:
        resp = http.put(url, json=body)
        resp.raise_for_status()
    logger.info("Upserted %s points to %s", len(ids), collection_name)


def set_alias(client: Optional[QdrantClient], collection_name: str, alias: str) -> None:
    """Создание алиаса через REST API (обход Union в qdrant-client)."""
    base = _qdrant_base()
    url = f"{base}/collections/aliases"
    body = {
        "actions": [
            {"create_alias": {"collection_name": collection_name, "alias_name": alias}}
        ]
    }
    with httpx.Client(timeout=30.0) as http:
        resp = http.post(url, json=body)
        resp.raise_for_status()
    logger.info("Set alias %s -> %s", alias, collection_name)


def delete_alias(alias: str) -> None:
    """Удаление алиаса через REST API (обход Union в qdrant-client)."""
    base = _qdrant_base()
    url = f"{base}/collections/aliases"
    body = {"actions": [{"delete_alias": {"alias_name": alias}}]}
    with httpx.Client(timeout=30.0) as http:
        resp = http.post(url, json=body)
        resp.raise_for_status()
    logger.info("Deleted alias %s", alias)


def get_collection_by_alias(client: Optional[QdrantClient], alias: str) -> Optional[str]:
    """Получение имени коллекции по алиасу через REST API."""
    base = _qdrant_base()
    url = f"{base}/aliases"
    try:
        with httpx.Client(timeout=10.0) as http:
            resp = http.get(url)
            resp.raise_for_status()
            data = resp.json()
        for a in data.get("result", {}).get("aliases", []):
            if a.get("alias_name") == alias:
                return a.get("collection_name")
        return None
    except Exception as e:
        logger.warning("Failed to get alias %s: %s", alias, e)
        return None


def search(
    client: QdrantClient,
    collection_name: str,
    query_vector: List[float],
    limit: int = 20,
    query_filter: Optional[Any] = None,
) -> List[tuple[Any, float, dict]]:
    """Возвращает список (id, score, payload)."""
    kwargs: dict = {
        "collection_name": collection_name,
        "query": query_vector,
        "limit": limit,
    }
    if query_filter:
        kwargs["query_filter"] = query_filter
    response = client.query_points(**kwargs)
    return [(r.id, r.score, r.payload or {}) for r in response.points]


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
