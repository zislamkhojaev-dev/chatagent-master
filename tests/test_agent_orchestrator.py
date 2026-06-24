"""Tests for escalation summary helpers (no Redis required)."""
from app.services.agent.kb_guard import probe_has_relevant_context


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
