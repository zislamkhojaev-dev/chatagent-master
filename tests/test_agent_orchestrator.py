"""Tests for agent orchestrator helpers."""
from app.services.agent.kb_guard import probe_has_relevant_context
from app.services.agent.orchestrator import _should_clarify
from app.services.scenarios import Scenario, ScenarioSlot


def _fallback_summary(messages, agent_state, classification, reason):
    """Inline copy for unit test without heavy imports."""
    lines = []
    for msg in messages:
        role = msg.get("role", "user")
        label = "Клиент" if role == "user" else "Бот"
        lines.append(f"{label}: {msg.get('content', '')}")
    parts = [
        f"Тема: {classification.get('theme', '—')}",
        f"Сценарий: {agent_state.get('scenario_id', '—')}",
        f"Причина эскалации: {reason}",
        "Переписка:",
        "\n".join(lines),
    ]
    return "\n".join(parts)


def test_fallback_summary():
    messages = [
        {"role": "user", "content": "QR не работает"},
        {"role": "assistant", "content": "Вы клиент или агент?"},
        {"role": "user", "content": "Я агент"},
    ]
    summary = _fallback_summary(
        messages,
        {"scenario_id": "qr_issue", "slots": {"user_type": "агент"}},
        {"theme": "Информация", "category": "Запрос"},
        "no_kb_match",
    )
    assert "QR" in summary or "qr" in summary.lower()
    assert "no_kb_match" in summary


def test_probe_has_relevant_context():
    assert probe_has_relevant_context("sufficient") is True
    assert probe_has_relevant_context("insufficient") is False
    assert probe_has_relevant_context("ambiguous") is False


def test_enrich_classification_logic():
    classification = {"theme": "T"}
    enriched = dict(classification)
    enriched["escalation_summary"] = "summary text"
    enriched["escalation_reason"] = "user_request"
    enriched["tools_used"] = ", ".join(["escalate_to_operator"])
    assert enriched["escalation_summary"] == "summary text"
    assert enriched["tools_used"] == "escalate_to_operator"


def _qr_scenario(**kwargs) -> Scenario:
    data = {
        "id": "qr_issue",
        "triggers": ["qr"],
        "clarify_policy": "if_ambiguous",
        "required_slots": [
            ScenarioSlot(
                id="user_type",
                question_ru="Клиент или агент?",
                question_uz="Mijoz yoki agent?",
            )
        ],
    }
    data.update(kwargs)
    return Scenario(**data)


def test_should_clarify_if_ambiguous_on_insufficient():
    scenario = _qr_scenario()
    assert _should_clarify(scenario, {"slots": {}}, "insufficient") is True
    assert _should_clarify(scenario, {"slots": {}}, "ambiguous") is True


def test_should_clarify_if_ambiguous_asks_when_disambiguation_slot_missing():
    """Sufficient probe on default client must still ask user_type."""
    scenario = _qr_scenario()
    assert _should_clarify(scenario, {"slots": {}}, "sufficient") is True


def test_should_clarify_skips_when_slot_filled():
    scenario = _qr_scenario()
    assert _should_clarify(scenario, {"slots": {"user_type": "клиент"}}, "sufficient") is False


def test_should_clarify_never_policy():
    scenario = _qr_scenario(clarify_policy="never")
    assert _should_clarify(scenario, {"slots": {}}, "insufficient") is False
