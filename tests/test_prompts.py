"""Tests for agent prompts (no external deps)."""
from app.services.agent.prompts import build_system_prompt
from app.services.scenarios import Scenario, ScenarioSlot


def test_system_prompt_clarification():
    scenario = Scenario(
        id="qr_issue",
        triggers=["qr"],
        required_slots=[
            ScenarioSlot(id="user_type", question_ru="Клиент или агент?", question_uz="Mijoz yoki agent?")
        ],
    )
    prompt = build_system_prompt("ru", scenario, {"slots": {}})
    assert "qr_issue" in prompt
    assert "ОБЯЗАТЕЛЬНО" in prompt or "уточняющий" in prompt.lower()


def test_system_prompt_kb_answer_mode_no_forced_clarify():
    scenario = Scenario(
        id="qr_issue",
        triggers=["qr"],
        required_slots=[
            ScenarioSlot(id="user_type", question_ru="Клиент или агент?", question_uz="Mijoz?")
        ],
    )
    prompt = build_system_prompt(
        "ru", scenario, {"slots": {}}, kb_answer_mode=True
    )
    assert "ОБЯЗАТЕЛЬНО" not in prompt
    assert "уже найден" in prompt or "ТОЛЬКО по результатам" in prompt
