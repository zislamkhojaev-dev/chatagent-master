"""Загрузка и нормализация синонимов (ТЗ Б.7 app/utils/synonyms.py)."""
import json
import logging
import os
import re
from typing import Dict, List

from app.core.config import settings
from app.utils.metrics import SYNONYMS_USAGE_COUNT

logger = logging.getLogger(__name__)

SYNONYMS_FILE_PATH = getattr(
    settings, "SYNONYMS_FILE_PATH", "synonyms.json"
)  # можно вынести в config при необходимости
synonyms_dict: Dict[str, Dict[str, List[str]]] = {}
synonyms_last_modified: float = 0


def load_synonyms() -> None:
    global synonyms_dict, synonyms_last_modified
    path = SYNONYMS_FILE_PATH
    try:
        if not os.path.exists(path):
            logger.warning("Synonyms file not found: %s. Creating empty structure.", path)
            default_synonyms = {"ru": {}, "uz": {}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default_synonyms, f, ensure_ascii=False, indent=2)
            synonyms_dict = default_synonyms
            return
        current_modified = os.path.getmtime(path)
        if current_modified == synonyms_last_modified and synonyms_dict:
            return
        with open(path, "r", encoding="utf-8") as f:
            synonyms_dict = json.load(f)
        synonyms_last_modified = current_modified
        if not isinstance(synonyms_dict, dict):
            raise ValueError("Synonyms must be a dictionary")
        for lang in ["ru", "uz"]:
            if lang not in synonyms_dict:
                synonyms_dict[lang] = {}
                logger.warning("Language '%s' not found in synonyms, created empty section", lang)
        logger.info(
            "Synonyms loaded successfully. RU: %s, UZ: %s",
            len(synonyms_dict.get("ru", {})),
            len(synonyms_dict.get("uz", {})),
        )
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in synonyms file: %s", e)
        synonyms_dict = {"ru": {}, "uz": {}}
    except Exception as e:
        logger.error("Error loading synonyms: %s", e)
        synonyms_dict = {"ru": {}, "uz": {}}


def normalize_text_with_synonyms(text: str, language: str) -> str:
    """Нормализует текст с использованием синонимов для определённого языка."""
    load_synonyms()
    if language not in synonyms_dict:
        logger.warning("Language '%s' not found in synonyms", language)
        return text
    normalized_text = text.lower()
    replacements_made = []
    for standard_term, synonyms_list in synonyms_dict[language].items():
        if not isinstance(synonyms_list, list):
            logger.warning(
                "Synonyms for '%s' must be a list, got: %s",
                standard_term,
                type(synonyms_list),
            )
            continue
        for synonym in synonyms_list:
            if isinstance(synonym, str) and synonym.lower() in normalized_text:
                pattern = r"\b" + re.escape(synonym.lower()) + r"\b"
                if re.search(pattern, normalized_text):
                    normalized_text = re.sub(
                        pattern, standard_term.lower(), normalized_text
                    )
                    replacements_made.append(
                        f"'{synonym}' -> '{standard_term}'"
                    )
                    SYNONYMS_USAGE_COUNT.labels(
                        language=language, standard_term=standard_term
                    ).inc()
    if replacements_made:
        logger.info(
            "Text normalization for %s: %s",
            language,
            "; ".join(replacements_made),
        )
    return normalized_text
