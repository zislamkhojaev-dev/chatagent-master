"""Tests for keyword-only classification stubs (no OpenAI)."""
from app.services.classification import (
    build_stub_classification,
    is_rudeness_message,
    classify_query_keywords_only,
)


def test_is_rudeness_message_keywords():
    assert is_rudeness_message("это хулиганство", "ru")
    assert is_rudeness_message("bezorilik qilyapti", "uz")
    assert not is_rudeness_message("как оплатить по QR", "ru")


def test_stub_rudeness():
    c = build_stub_classification(language="ru", hard_reason="rudeness", record_metric=False)
    assert c["theme"] == "Хулиганство"
    assert c["subcategory"] == "keyword_guard"


def test_stub_scenario_mode():
    c = build_stub_classification(
        language="ru",
        scenario_id="qr_issue",
        mode="clarifying",
        record_metric=False,
    )
    assert c["theme"] == "qr_issue"
    assert c["category"] == "agent"
    assert c["subcategory"] == "clarifying"


def test_stub_agent_escalation_reason():
    c = build_stub_classification(
        language="ru",
        scenario_id="sms_not_received",
        mode="escalation",
        hard_reason="no_kb_match",
        record_metric=False,
    )
    assert c["theme"] == "sms_not_received"
    assert c["subcategory"] == "no_kb_match"


def test_classify_keywords_only_no_openai():
    c = classify_query_keywords_only("привет", "ru")
    assert c["theme"] == "general"
    assert c["category"] == "agent"
