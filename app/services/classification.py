"""Лёгкая классификация для hot path: keyword-guard + stub (без OpenAI).

Полная таксономия Paynet — в offline analyzer.py.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.services.constants import CATEGORIES

logger = logging.getLogger(__name__)


def rudeness_keywords() -> List[str]:
    """Ключевые слова хамства из CATEGORIES (единый источник)."""
    block = CATEGORIES.get("Хулиганство / Bezorilik") or {}
    words: List[str] = []
    for keywords in block.values():
        words.extend(keywords)
    return words


def is_rudeness_message(query: str, language: str = "ru") -> bool:
    """Дешёвый keyword-guard: мат / хулиганство / bezorilik (без OpenAI)."""
    del language  # reserved for future locale-specific lists
    if not query or not query.strip():
        return False
    query_lower = query.lower()
    return any(kw.lower() in query_lower for kw in rudeness_keywords())


def build_stub_classification(
    *,
    language: str = "ru",
    scenario_id: Optional[str] = None,
    mode: Optional[str] = None,
    hard_reason: Optional[str] = None,
    record_metric: bool = True,
) -> Dict[str, str]:
    """
    Стабильный JSON classification для API / interactions без LLM-таксономии.
    theme ≈ scenario или причина; subcategory ≈ mode / keyword_guard / escalation_reason.
    """
    if hard_reason == "rudeness":
        result = {
            "theme": "Хулиганство" if language == "ru" else "Bezorilik",
            "category": "Хулиганство / Bezorilik",
            "subcategory": "keyword_guard",
        }
    elif hard_reason == "user_request":
        result = {
            "theme": "Запрос оператора",
            "category": "Эскалация",
            "subcategory": "user_request",
        }
    elif hard_reason == "repeat":
        result = {
            "theme": "Повтор",
            "category": "Эскалация",
            "subcategory": "repeat",
        }
    else:
        result = {
            "theme": scenario_id or "general",
            "category": "agent",
            "subcategory": hard_reason or mode or "answering",
        }

    if record_metric:
        try:
            from app.utils.metrics import CLASSIFICATIONS_COUNT

            CLASSIFICATIONS_COUNT.labels(
                theme=result["theme"],
                category=result["category"],
                subcategory=result["subcategory"],
            ).inc()
        except Exception:
            pass
    return result


def classify_query_keywords_only(query: str, language: str = "ru") -> Dict[str, str]:
    """
    Обратная совместимость: только rudeness keywords → stub,
    иначе general stub. OpenAI не вызывается.
    """
    if is_rudeness_message(query, language):
        logger.info("Keyword rudeness match for classification stub")
        return build_stub_classification(language=language, hard_reason="rudeness")
    return build_stub_classification(language=language)
