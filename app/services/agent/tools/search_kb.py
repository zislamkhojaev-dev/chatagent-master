"""Tool: search knowledge base via hybrid RAG with metadata filters."""
import logging
from typing import Any, Optional

from app.services.embeddings import get_embedding
from app.services.kb_metadata import MetadataFilter, build_metadata_filter
from app.services.scenarios import Scenario, build_search_query
from app.services.search import hybrid_search

logger = logging.getLogger(__name__)


async def execute_search_kb(
    query: str,
    language: str,
    *,
    app_state: Any,
    redis_client: Any,
    scenario: Optional[Scenario] = None,
    slots: Optional[dict] = None,
    metadata_filter: Optional[MetadataFilter] = None,
) -> dict:
    kb_chunks = getattr(app_state, "knowledge_base", None) or []
    qdrant_client = getattr(app_state, "qdrant_client", None)
    qdrant_collection = getattr(app_state, "qdrant_collection", None)
    bm25_index = getattr(app_state, "bm25_index", None)

    if metadata_filter is None:
        metadata_filter = build_metadata_filter(scenario, slots or {})

    search_query = query
    if scenario:
        search_query = build_search_query(scenario, slots or {}, query)

    embedding = await get_embedding(search_query, redis_client)
    result = await hybrid_search(
        search_query,
        embedding,
        kb_chunks,
        language,
        qdrant_client=qdrant_client,
        qdrant_collection=qdrant_collection,
        bm25_index=bm25_index,
        top_k=5,
        metadata_filter=metadata_filter,
    )

    eval_ = result.evaluation
    return {
        "chunks": result.chunks,
        "chunk_count": len(result.chunks),
        "has_relevant_context": eval_.is_sufficient and not eval_.is_empty,
        "is_ambiguous": eval_.is_ambiguous,
        "is_empty": eval_.is_empty,
        "evaluation": eval_,
        "query": search_query,
        "metadata_filter": metadata_filter,
    }


def format_kb_result_for_llm(result: dict) -> str:
    if result.get("is_ambiguous"):
        audiences = result.get("evaluation")
        if audiences and hasattr(audiences, "audiences_found"):
            return (
                "Knowledge base returned conflicting results for different user types "
                f"({audiences.audiences_found}). Clarification may be needed."
            )
    if not result.get("has_relevant_context"):
        return "Knowledge base returned no relevant results for this query and filter."
    chunks = result.get("chunks", [])
    return "Knowledge base results:\n" + "\n---\n".join(chunks)
