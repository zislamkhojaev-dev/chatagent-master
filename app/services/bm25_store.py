"""BM25-индекс по чанкам (uz/ru). ТЗ Б.1."""
import logging
import re
from typing import List, Optional, Tuple

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


def tokenize_ru_uz(text: str) -> List[str]:
    """Простая токенизация для ru/uz: буквы, цифры, апостроф (o', g')."""
    text = text.lower().strip()
    tokens = re.findall(r"[a-zа-яёўқғҳ0-9']+", text, re.IGNORECASE)
    return [t for t in tokens if len(t) > 1 or t.isdigit()]


def build_bm25(chunks: List[str]) -> BM25Okapi:
    """Строит BM25-индекс по списку текстов чанков."""
    tokenized = [tokenize_ru_uz(c) for c in chunks]
    return BM25Okapi(tokenized)


def search_bm25(
    bm25: BM25Okapi,
    chunks: List[str],
    query: str,
    top_k: int = 20,
) -> List[Tuple[int, float]]:
    """Возвращает список (index, score) для top_k по BM25."""
    if not chunks:
        return []
    tokenized_query = tokenize_ru_uz(query)
    if not tokenized_query:
        return []
    scores = bm25.get_scores(tokenized_query)
    indexed = list(enumerate(scores))
    indexed.sort(key=lambda x: -x[1])
    return [(i, s) for i, s in indexed[:top_k] if s > 0]
