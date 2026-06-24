"""Единая таксономия KB: audiences, channels (scenarios/taxonomy.json).

topic в PDF и сценариях — произвольная строка; список topics в JSON только подсказки и legacy keywords.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

_taxonomy_data: Optional["Taxonomy"] = None
_taxonomy_last_modified: float = 0


@dataclass
class Taxonomy:
    version: int = 1
    defaults: Dict[str, str] = field(
        default_factory=lambda: {
            "audience": "both",
            "channel": "general",
            "topic": "general",
        }
    )
    audiences: List[str] = field(default_factory=list)
    channels: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    section_rules: List[Tuple[re.Pattern, str, str]] = field(default_factory=list)
    topic_keywords: Dict[str, List[str]] = field(default_factory=dict)

    def allowed(self, key: str) -> List[str]:
        if key == "audience":
            return self.audiences
        if key == "channel":
            return self.channels
        if key == "topic":
            return self.topics
        return []

    def is_valid(self, key: str, value: Optional[str]) -> bool:
        if not value:
            return True
        if key == "topic":
            return True
        return value in self.allowed(key)


def _parse_taxonomy(raw: dict) -> Taxonomy:
    legacy = raw.get("legacy") or {}
    section_rules: List[Tuple[re.Pattern, str, str]] = []
    for rule in legacy.get("section_rules") or raw.get("section_rules") or []:
        section_rules.append(
            (re.compile(rule["pattern"], re.I), rule["audience"], rule["channel"])
        )
    defaults = raw.get("defaults") or {
        "audience": "both",
        "channel": "general",
        "topic": "general",
    }
    return Taxonomy(
        version=raw.get("version", 1),
        defaults=defaults,
        audiences=raw.get("audiences") or [],
        channels=raw.get("channels") or [],
        topics=raw.get("topics") or [],
        section_rules=section_rules,
        topic_keywords=legacy.get("topic_keywords") or raw.get("topic_keywords") or {},
    )


def _default_taxonomy() -> Taxonomy:
    return _parse_taxonomy(
        {
            "version": 1,
            "defaults": {"audience": "both", "channel": "general", "topic": "general"},
            "audiences": ["client", "agent", "both"],
            "channels": ["mobile_app", "agent", "infokiosk", "general"],
            "topics": ["qr", "payment", "refund", "sms", "identification", "general"],
            "legacy": {"section_rules": [], "topic_keywords": {}},
        }
    )


def load_taxonomy(force: bool = False) -> Taxonomy:
    global _taxonomy_data, _taxonomy_last_modified
    path = settings.TAXONOMY_FILE
    try:
        if not path.exists():
            logger.warning("Taxonomy file not found: %s. Using built-in defaults.", path)
            _taxonomy_data = _default_taxonomy()
            return _taxonomy_data
        current_modified = os.path.getmtime(path)
        if not force and current_modified == _taxonomy_last_modified and _taxonomy_data:
            return _taxonomy_data
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _taxonomy_data = _parse_taxonomy(raw)
        _taxonomy_last_modified = current_modified
        logger.info(
            "Taxonomy loaded: %s audiences, %s channels, %s topics",
            len(_taxonomy_data.audiences),
            len(_taxonomy_data.channels),
            len(_taxonomy_data.topics),
        )
        return _taxonomy_data
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in taxonomy file: %s", e)
        return _default_taxonomy()
    except Exception as e:
        logger.error("Failed to load taxonomy: %s", e)
        return _default_taxonomy()


def get_taxonomy() -> Taxonomy:
    if _taxonomy_data is None:
        return load_taxonomy()
    return _taxonomy_data


def reload_taxonomy() -> Taxonomy:
    global _taxonomy_last_modified
    _taxonomy_last_modified = 0
    return load_taxonomy(force=True)


def validate_taxonomy_field(field: str, value: Optional[str], context: str = "") -> None:
    if field == "topic":
        return
    taxonomy = get_taxonomy()
    if value and not taxonomy.is_valid(field, value):
        prefix = f"{context}: " if context else ""
        raise ValueError(f"{prefix}invalid {field} '{value}' (allowed: {taxonomy.allowed(field)})")
