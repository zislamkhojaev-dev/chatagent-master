"""Tests for scenarios service."""
import json
import tempfile
from pathlib import Path

import pytest

from app.services import scenarios as scenarios_module
from app.services.scenarios import (
    Scenario,
    build_search_query,
    extract_slot_value,
    get_missing_slots,
    load_scenarios,
    match_scenario,
    validate_scenarios,
)


@pytest.fixture
def scenarios_file(tmp_path, monkeypatch):
    data = {
        "version": 1,
        "updated_at": "",
        "scenarios": [
            {
                "id": "qr_issue",
                "enabled": True,
                "triggers": ["qr", "qr-код"],
                "required_slots": [
                    {
                        "id": "user_type",
                        "question_ru": "Вы клиент или агент?",
                        "question_uz": "Mijozmisiz yoki agentmisiz?",
                    }
                ],
                "search_hint": "QR {user_type}",
                "max_clarifications": 2,
            }
        ],
        "auto_escalate_categories": ["Хулиганство / Bezorilik"],
        "default_max_clarifications": 2,
    }
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(scenarios_module.settings, "SCENARIOS_DIR", tmp_path)
    monkeypatch.setattr(scenarios_module.settings, "SCENARIOS_FILE", path)
    monkeypatch.setattr(scenarios_module.settings, "SCENARIOS_BACKUP_FILE", tmp_path / "backup.json")
    scenarios_module._scenarios_data = {}
    scenarios_module._scenarios_last_modified = 0
    return path


def test_match_scenario_qr(scenarios_file):
    load_scenarios(force=True)
    s = match_scenario("у меня проблемы с qr кодом", "ru")
    assert s is not None
    assert s.id == "qr_issue"


def test_missing_slots(scenarios_file):
    load_scenarios(force=True)
    s = match_scenario("qr не работает", "ru")
    missing = get_missing_slots(s, {"slots": {}})
    assert len(missing) == 1
    assert missing[0].id == "user_type"


def test_build_search_query(scenarios_file):
    load_scenarios(force=True)
    s = match_scenario("qr", "ru")
    q = build_search_query(s, {"user_type": "агент"}, "qr")
    assert "агент" in q


def test_validate_scenarios_duplicate_ids():
    with pytest.raises(Exception):
        validate_scenarios({
            "scenarios": [
                {"id": "a", "triggers": ["x"], "required_slots": []},
                {"id": "a", "triggers": ["y"], "required_slots": []},
            ]
        })


def test_extract_slot_value_user_type():
    assert extract_slot_value("user_type", "Я агент, QR не работает") == "агент"
    assert extract_slot_value("user_type", "я клиент приложения") == "клиент"
    assert extract_slot_value("user_type", "не знаю что делать") is None


def test_extract_slot_value_payment_channel():
    assert extract_slot_value("payment_channel", "в инфокиоске") == "инфокиоск"
    assert extract_slot_value("payment_channel", "через приложение") == "мобильное приложение"
