"""Гибридный поиск: Qdrant + BM25 (RRF). ТЗ Б.1."""
import asyncio
import logging
from typing import Any, List, Optional

from app.utils.synonyms import normalize_text_with_synonyms

logger = logging.getLogger(__name__)

# RRF (Reciprocal Rank Fusion) constant
RRF_K = 60


def _hybrid_search_sync(
    qdrant_client: Any,
    collection_name: str,
    bm25_index: Any,
    chunks: List[str],
    query_embedding: List[float],
    query_text: str,
    vector_top_k: int = 20,
    bm25_top_k: int = 20,
    final_top_k: int = 5,
) -> List[str]:
    """Синхронный гибрид: векторный поиск + BM25 → RRF → top_k. Без реранкера (fallback по ТЗ)."""
    from app.services.qdrant_store import search as qdrant_search
    from app.services.bm25_store import search_bm25

    vector_hits = qdrant_search(
        qdrant_client, collection_name, query_embedding, limit=vector_top_k
    )
    bm25_hits = search_bm25(bm25_index, chunks, query_text, top_k=bm25_top_k)

    # RRF: score = sum 1/(k + rank). Ключи — int (id чанка), т.к. в Qdrant точки с id 0, 1, 2, ...
    rrf_scores: dict[int, float] = {}
    for rank, (uid, _, _) in enumerate(vector_hits):
        rrf_scores[uid] = rrf_scores.get(uid, 0) + 1.0 / (RRF_K + rank)
    for rank, (idx, _) in enumerate(bm25_hits):
        rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (RRF_K + rank)

    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: -rrf_scores[x])[:final_top_k]
    result = []
    for idx in sorted_ids:
        if 0 <= idx < len(chunks):
            result.append(chunks[idx])
    return result


async def find_relevant_context(
    query: str,
    query_embedding: List[float],
    knowledge_base: List[str],
    language: str,
    *,
    qdrant_client: Optional[Any] = None,
    qdrant_collection: Optional[str] = None,
    bm25_index: Optional[Any] = None,
    top_k: int = 5,
) -> List[str]:
    """Гибридный поиск (Qdrant + BM25 + RRF). При отсутствии индекса — пустой контекст."""
    normalized_query = normalize_text_with_synonyms(query, language)
    logger.info("Normalized query for context search: %s, language: %s", normalized_query, language)
    if not query_embedding:
        logger.warning("No embedding for query, returning empty context")
        return []

    if qdrant_client and qdrant_collection and bm25_index is not None and knowledge_base:
        try:
            return await asyncio.to_thread(
                _hybrid_search_sync,
                qdrant_client,
                qdrant_collection,
                bm25_index,
                knowledge_base,
                query_embedding,
                normalized_query,
                vector_top_k=20,
                bm25_top_k=20,
                final_top_k=top_k,
            )
        except Exception as e:
            logger.warning("Hybrid search failed: %s", e)

    logger.warning("No Qdrant/BM25 index or knowledge base, returning empty context")
    return []
