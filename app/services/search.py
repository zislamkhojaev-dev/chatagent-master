"""Гибридный поиск: Qdrant + BM25 (RRF) с фильтрацией по metadata."""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.kb_metadata import (
    KbSearchEvaluation,
    MetadataFilter,
    chunk_matches_filter,
    evaluate_kb_hits,
    normalize_chunk_record,
    records_to_texts,
    to_qdrant_filter,
)
from app.utils.synonyms import normalize_text_with_synonyms

logger = logging.getLogger(__name__)

RRF_K = 60


@dataclass
class HybridSearchResult:
    chunks: List[str]
    hits: List[tuple]
    evaluation: KbSearchEvaluation
    metadata_filter: Optional[MetadataFilter] = None


def _hybrid_search_sync(
    qdrant_client: Any,
    collection_name: str,
    bm25_index: Any,
    kb_records: List[Dict[str, Any]],
    query_embedding: List[float],
    query_text: str,
    *,
    metadata_filter: Optional[MetadataFilter] = None,
    vector_top_k: int = 20,
    bm25_top_k: int = 20,
    final_top_k: int = 5,
) -> HybridSearchResult:
    from app.services.qdrant_store import search as qdrant_search
    from app.services.bm25_store import search_bm25

    chunks = records_to_texts(kb_records)
    allowed_ids = {
        i for i, rec in enumerate(kb_records)
        if chunk_matches_filter(rec, metadata_filter)
    }

    qdrant_filter = to_qdrant_filter(metadata_filter)
    vector_hits = qdrant_search(
        qdrant_client,
        collection_name,
        query_embedding,
        limit=vector_top_k,
        query_filter=qdrant_filter,
    )
    bm25_hits = search_bm25(bm25_index, chunks, query_text, top_k=bm25_top_k)
    if allowed_ids is not None:
        bm25_hits = [(i, s) for i, s in bm25_hits if i in allowed_ids]
        vector_hits = [(uid, sc, pl) for uid, sc, pl in vector_hits if uid in allowed_ids]

    rrf_scores: dict[int, float] = {}
    hit_meta: dict[int, dict] = {}
    for rank, (uid, _, payload) in enumerate(vector_hits):
        rrf_scores[uid] = rrf_scores.get(uid, 0) + 1.0 / (RRF_K + rank)
        if uid not in hit_meta:
            hit_meta[uid] = payload
    for rank, (idx, _) in enumerate(bm25_hits):
        rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (RRF_K + rank)
        if idx not in hit_meta and 0 <= idx < len(kb_records):
            hit_meta[idx] = kb_records[idx]

    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: -rrf_scores[x])[:final_top_k]
    hits: List[tuple] = []
    result_chunks: List[str] = []
    for idx in sorted_ids:
        if 0 <= idx < len(kb_records):
            score = rrf_scores[idx]
            meta = hit_meta.get(idx, kb_records[idx])
            hits.append((idx, score, meta))
            result_chunks.append(kb_records[idx]["text"])

    evaluation = evaluate_kb_hits(hits, min_rrf_score=settings.MIN_RRF_SCORE)
    return HybridSearchResult(
        chunks=result_chunks,
        hits=hits,
        evaluation=evaluation,
        metadata_filter=metadata_filter,
    )


async def find_relevant_context(
    query: str,
    query_embedding: List[float],
    knowledge_base: List[Any],
    language: str,
    *,
    qdrant_client: Optional[Any] = None,
    qdrant_collection: Optional[str] = None,
    bm25_index: Optional[Any] = None,
    top_k: int = 5,
    metadata_filter: Optional[MetadataFilter] = None,
) -> List[str]:
    """Гибридный поиск. knowledge_base: list[str] legacy или list[dict] с metadata."""
    result = await hybrid_search(
        query,
        query_embedding,
        knowledge_base,
        language,
        qdrant_client=qdrant_client,
        qdrant_collection=qdrant_collection,
        bm25_index=bm25_index,
        top_k=top_k,
        metadata_filter=metadata_filter,
    )
    return result.chunks


async def hybrid_search(
    query: str,
    query_embedding: List[float],
    knowledge_base: List[Any],
    language: str,
    *,
    qdrant_client: Optional[Any] = None,
    qdrant_collection: Optional[str] = None,
    bm25_index: Optional[Any] = None,
    top_k: int = 5,
    metadata_filter: Optional[MetadataFilter] = None,
) -> HybridSearchResult:
    normalized_query = normalize_text_with_synonyms(query, language)
    logger.info(
        "Hybrid search: query=%s, filter=%s",
        normalized_query[:80],
        metadata_filter,
    )
    if not query_embedding:
        return HybridSearchResult([], [], KbSearchEvaluation(is_empty=True))

    kb_records = [normalize_chunk_record(item) for item in (knowledge_base or [])]
    if not kb_records:
        return HybridSearchResult([], [], KbSearchEvaluation(is_empty=True))

    if qdrant_client and qdrant_collection and bm25_index is not None:
        try:
            return await asyncio.to_thread(
                _hybrid_search_sync,
                qdrant_client,
                qdrant_collection,
                bm25_index,
                kb_records,
                query_embedding,
                normalized_query,
                metadata_filter=metadata_filter,
                vector_top_k=20,
                bm25_top_k=20,
                final_top_k=top_k,
            )
        except Exception as e:
            logger.warning("Hybrid search failed: %s", e)

    # Fallback: BM25 only with metadata filter
    if bm25_index is not None:
        from app.services.bm25_store import search_bm25

        chunks = records_to_texts(kb_records)
        bm25_hits = search_bm25(bm25_index, chunks, normalized_query, top_k=top_k * 4)
        hits = []
        result_chunks = []
        for idx, score in bm25_hits:
            if not chunk_matches_filter(kb_records[idx], metadata_filter):
                continue
            hits.append((idx, score, kb_records[idx]))
            result_chunks.append(kb_records[idx]["text"])
            if len(result_chunks) >= top_k:
                break
        evaluation = evaluate_kb_hits(hits, min_rrf_score=settings.MIN_RRF_SCORE)
        return HybridSearchResult(result_chunks, hits, evaluation, metadata_filter)

    return HybridSearchResult([], [], KbSearchEvaluation(is_empty=True))
