"""Загрузка и матчинг сценариев агента из scenarios/scenarios.json."""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.services.taxonomy import validate_taxonomy_field

logger = logging.getLogger(__name__)

_scenarios_data: Dict[str, Any] = {}
_scenarios_last_modified: float = 0


class ScenarioSlot(BaseModel):
    id: str
    question_ru: str
    question_uz: str


class Scenario(BaseModel):
    id: str
    enabled: bool = True
    triggers: List[str] = Field(min_length=1)
    required_slots: List[ScenarioSlot] = Field(default_factory=list)
    search_hint: str = ""
    max_clarifications: Optional[int] = None
    clarify_policy: str = "if_ambiguous"  # never | if_ambiguous | always
    default_audience: str = "client"  # client | agent | both
    default_channel: Optional[str] = None
    topic: Optional[str] = None


class ScenariosConfig(BaseModel):
    version: int = 1
    updated_at: str = ""
    scenarios: List[Scenario] = Field(default_factory=list)
    auto_escalate_categories: List[str] = Field(default_factory=list)
    default_max_clarifications: int = 2

    @field_validator("scenarios")
    @classmethod
    def unique_ids(cls, scenarios: List[Scenario]) -> List[Scenario]:
        ids = [s.id for s in scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario ids must be unique")
        return scenarios


def validate_scenarios(data: dict) -> ScenariosConfig:
    config = ScenariosConfig.model_validate(data)
    for scenario in config.scenarios:
        validate_taxonomy_field("audience", scenario.default_audience, scenario.id)
        if scenario.default_channel:
            validate_taxonomy_field("channel", scenario.default_channel, scenario.id)
    return config


def load_scenarios(force: bool = False) -> ScenariosConfig:
    global _scenarios_data, _scenarios_last_modified
    path = settings.SCENARIOS_FILE
    try:
        if not path.exists():
            logger.warning("Scenarios file not found: %s. Using empty config.", path)
            _scenarios_data = ScenariosConfig().model_dump()
            return ScenariosConfig()
        current_modified = os.path.getmtime(path)
        if not force and current_modified == _scenarios_last_modified and _scenarios_data:
            return ScenariosConfig.model_validate(_scenarios_data)
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        config = validate_scenarios(raw)
        _scenarios_data = config.model_dump()
        _scenarios_last_modified = current_modified
        logger.info("Scenarios loaded: %s enabled scenarios", sum(1 for s in config.scenarios if s.enabled))
        return config
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in scenarios file: %s", e)
        return ScenariosConfig()
    except Exception as e:
        logger.error("Failed to load scenarios: %s", e)
        return ScenariosConfig()


def reload_scenarios() -> ScenariosConfig:
    global _scenarios_last_modified
    _scenarios_last_modified = 0
    return load_scenarios(force=True)


def get_scenarios_config() -> ScenariosConfig:
    if not _scenarios_data:
        return load_scenarios()
    return ScenariosConfig.model_validate(_scenarios_data)


def save_scenarios(data: dict) -> ScenariosConfig:
    config = validate_scenarios(data)
    config.updated_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    settings.SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    if settings.SCENARIOS_FILE.exists():
        settings.SCENARIOS_BACKUP_FILE.write_text(
            settings.SCENARIOS_FILE.read_text(encoding="utf-8"), encoding="utf-8"
        )
    with open(settings.SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
    return reload_scenarios()


def match_scenario(query: str, language: str = "ru") -> Optional[Scenario]:
    config = get_scenarios_config()
    query_lower = query.lower()
    for scenario in config.scenarios:
        if not scenario.enabled:
            continue
        for trigger in scenario.triggers:
            if trigger.lower() in query_lower:
                return scenario
    return None


def get_missing_slots(scenario: Scenario, agent_state: dict) -> List[ScenarioSlot]:
    slots = agent_state.get("slots") or {}
    missing = []
    for slot in scenario.required_slots:
        if not slots.get(slot.id):
            missing.append(slot)
    return missing


def get_slot_question(slot: ScenarioSlot, language: str) -> str:
    return slot.question_uz if language == "uz" else slot.question_ru


def build_search_query(scenario: Optional[Scenario], slots: dict, fallback_query: str) -> str:
    if not scenario or not scenario.search_hint:
        return fallback_query
    query = scenario.search_hint
    for key, value in slots.items():
        query = query.replace("{" + key + "}", str(value))
    # Remove unfilled placeholders
    import re
    query = re.sub(r"\{[^}]+\}", "", query).strip()
    return query or fallback_query


def should_auto_escalate_category(classification: dict) -> bool:
    config = get_scenarios_config()
    theme = classification.get("theme", "")
    category = classification.get("category", "")
    for auto_cat in config.auto_escalate_categories:
        if auto_cat in theme or auto_cat in category:
            return True
    return False


def get_scenarios_status() -> dict:
    path = settings.SCENARIOS_FILE
    config = get_scenarios_config()
    return {
        "file_exists": path.exists(),
        "file_path": str(path),
        "updated_at": config.updated_at,
        "count": len(config.scenarios),
        "enabled_count": sum(1 for s in config.scenarios if s.enabled),
        "backup_exists": settings.SCENARIOS_BACKUP_FILE.exists(),
    }
