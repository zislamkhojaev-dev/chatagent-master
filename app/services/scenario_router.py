"""Embedding-based scenario router with substring fallback."""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.scenarios import Scenario, get_scenarios_config, match_scenario_substring

if TYPE_CHECKING:
    from redis.asyncio import Redis
else:
    Redis = Any  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

# scenario_id -> embedding vector (in-memory cache)
_scenario_embeddings: Dict[str, List[float]] = {}
_index_signature: str = ""


def build_scenario_document(scenario: Scenario) -> str:
    """Текст для эмбеддинга сценария (ru+uz+triggers+hint)."""
    parts = [
        scenario.description_ru or "",
        scenario.description_uz or "",
        " ".join(scenario.triggers or []),
        scenario.search_hint or "",
        scenario.topic or "",
        scenario.default_channel or "",
        scenario.default_audience or "",
    ]
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return text or scenario.id


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _config_signature(scenarios: List[Scenario]) -> str:
    parts = []
    for s in scenarios:
        if not s.enabled:
            continue
        parts.append(f"{s.id}|{build_scenario_document(s)}")
    return str(hash("|".join(parts)))


def get_cached_embeddings() -> Dict[str, List[float]]:
    return dict(_scenario_embeddings)


def clear_scenario_embedding_cache() -> None:
    global _scenario_embeddings, _index_signature
    _scenario_embeddings = {}
    _index_signature = ""


async def rebuild_scenario_embeddings(redis_client: Redis) -> int:
    """Пересчитывает эмбеддинги всех enabled-сценариев. Возвращает число векторов."""
    from app.services.embeddings import get_embedding

    global _scenario_embeddings, _index_signature
    config = get_scenarios_config()
    enabled = [s for s in config.scenarios if s.enabled]
    new_map: Dict[str, List[float]] = {}
    for scenario in enabled:
        doc = build_scenario_document(scenario)
        try:
            emb = await get_embedding(doc, redis_client)
            if emb:
                new_map[scenario.id] = emb
        except Exception as e:
            logger.warning("Failed to embed scenario %s: %s", scenario.id, e)
    _scenario_embeddings = new_map
    _index_signature = _config_signature(enabled)
    logger.info("Scenario router index rebuilt: %s embeddings", len(new_map))
    return len(new_map)


async def ensure_scenario_embeddings(redis_client: Redis) -> None:
    """Rebuild if cache empty or scenarios changed."""
    config = get_scenarios_config()
    enabled = [s for s in config.scenarios if s.enabled]
    sig = _config_signature(enabled)
    if _scenario_embeddings and sig == _index_signature:
        return
    await rebuild_scenario_embeddings(redis_client)


def rank_scenarios_by_embedding(
    query_embedding: List[float],
    embeddings: Optional[Dict[str, List[float]]] = None,
) -> List[Tuple[str, float]]:
    """[(scenario_id, score), ...] по убыванию score."""
    emb_map = embeddings if embeddings is not None else _scenario_embeddings
    scored: List[Tuple[str, float]] = []
    for sid, vec in emb_map.items():
        scored.append((sid, _cosine_similarity(query_embedding, vec)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


async def resolve_scenario(
    query: str,
    language: str = "ru",
    *,
    query_embedding: Optional[List[float]] = None,
    redis_client: Optional[Redis] = None,
) -> Optional[Scenario]:
    """
    Выбор сценария:
    - substring: только triggers
    - embedding: cosine ≥ threshold (без substring, кроме отсутствия embedding)
    - hybrid: embedding, иначе substring fallback (default)
    """
    mode = (settings.SCENARIO_ROUTER_MODE or "hybrid").lower().strip()
    threshold = float(settings.SCENARIO_ROUTER_THRESHOLD)

    if mode == "substring":
        return match_scenario_substring(query, language)

    config = get_scenarios_config()
    by_id = {s.id: s for s in config.scenarios if s.enabled}

    emb = query_embedding
    if emb is None and redis_client is not None:
        try:
            from app.services.embeddings import get_embedding

            emb = await get_embedding(query, redis_client)
        except Exception as e:
            logger.warning("Scenario router: query embedding failed: %s", e)
            emb = None

    if emb is not None and redis_client is not None:
        try:
            await ensure_scenario_embeddings(redis_client)
        except Exception as e:
            logger.warning("Scenario router: ensure embeddings failed: %s", e)

    if emb is not None and _scenario_embeddings:
        ranked = rank_scenarios_by_embedding(emb)
        if ranked:
            best_id, best_score = ranked[0]
            logger.info(
                "Scenario router embedding: best=%s score=%.3f threshold=%.3f mode=%s",
                best_id,
                best_score,
                threshold,
                mode,
            )
            if best_score >= threshold and best_id in by_id:
                return by_id[best_id]
            if mode == "embedding":
                return None

    # hybrid / no embedding available → substring fallback
    if mode == "hybrid" or emb is None or not _scenario_embeddings:
        fallback = match_scenario_substring(query, language)
        if fallback:
            logger.info("Scenario router fallback substring: %s", fallback.id)
        return fallback

    return None
