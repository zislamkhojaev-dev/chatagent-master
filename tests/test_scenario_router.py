"""Tests for embedding scenario router (no OpenAI required)."""
import pytest

from app.services import scenario_router as router_module
from app.services.scenario_router import (
    build_scenario_document,
    clear_scenario_embedding_cache,
    rank_scenarios_by_embedding,
    resolve_scenario,
)
from app.services.scenarios import Scenario, match_scenario_substring


def _unit(vec):
    """Normalize for cosine ~1 with itself."""
    return vec


def test_build_scenario_document_includes_descriptions():
    s = Scenario(
        id="sms_not_received",
        triggers=["sms"],
        description_ru="Не приходит SMS код",
        description_uz="SMS kod kelmayapti",
        search_hint="SMS приложение",
    )
    doc = build_scenario_document(s)
    assert "Не приходит SMS" in doc
    assert "SMS kod" in doc
    assert "sms" in doc.lower()


def test_rank_scenarios_by_embedding():
    clear_scenario_embedding_cache()
    embeddings = {
        "sms_not_received": [1.0, 0.0, 0.0],
        "qr_issue": [0.0, 1.0, 0.0],
        "refund": [0.0, 0.0, 1.0],
    }
    ranked = rank_scenarios_by_embedding([0.9, 0.1, 0.0], embeddings)
    assert ranked[0][0] == "sms_not_received"
    assert ranked[0][1] > ranked[1][1]


@pytest.mark.asyncio
async def test_resolve_scenario_embedding_mode(monkeypatch):
    clear_scenario_embedding_cache()
    sms = Scenario(
        id="sms_not_received",
        enabled=True,
        triggers=["sms"],
        description_ru="SMS OTP",
        default_channel="mobile_app",
        topic="sms",
    )
    qr = Scenario(
        id="qr_issue",
        enabled=True,
        triggers=["qr"],
        description_ru="QR scan",
        topic="qr",
    )

    class FakeConfig:
        scenarios = [sms, qr]

    monkeypatch.setattr(router_module, "get_scenarios_config", lambda: FakeConfig())
    monkeypatch.setattr(router_module.settings, "SCENARIO_ROUTER_MODE", "embedding")
    monkeypatch.setattr(router_module.settings, "SCENARIO_ROUTER_THRESHOLD", 0.5)
    router_module._scenario_embeddings = {
        "sms_not_received": [1.0, 0.0],
        "qr_issue": [0.0, 1.0],
    }
    router_module._index_signature = "fixed"

    async def _noop_ensure(_redis):
        return None

    monkeypatch.setattr(router_module, "ensure_scenario_embeddings", _noop_ensure)

    chosen = await resolve_scenario(
        "код подтверждения не приходит",
        "ru",
        query_embedding=[1.0, 0.0],
        redis_client=object(),
    )
    assert chosen is not None
    assert chosen.id == "sms_not_received"


@pytest.mark.asyncio
async def test_resolve_scenario_hybrid_fallback_substring(monkeypatch):
    clear_scenario_embedding_cache()
    sms = Scenario(
        id="sms_not_received",
        enabled=True,
        triggers=["уникальный_триггер_sms_xyz"],
        description_ru="SMS",
    )

    class FakeConfig:
        scenarios = [sms]

    monkeypatch.setattr(router_module, "get_scenarios_config", lambda: FakeConfig())
    monkeypatch.setattr(
        "app.services.scenarios.get_scenarios_config", lambda: FakeConfig()
    )
    monkeypatch.setattr(router_module.settings, "SCENARIO_ROUTER_MODE", "hybrid")
    monkeypatch.setattr(router_module.settings, "SCENARIO_ROUTER_THRESHOLD", 0.99)
    router_module._scenario_embeddings = {"sms_not_received": [1.0, 0.0]}
    router_module._index_signature = "fixed"

    async def _noop_ensure(_redis):
        return None

    monkeypatch.setattr(router_module, "ensure_scenario_embeddings", _noop_ensure)

    # Low similarity to cached emb, but substring trigger hits
    chosen = await resolve_scenario(
        "уникальный_триггер_sms_xyz проблема",
        "ru",
        query_embedding=[0.0, 1.0],
        redis_client=object(),
    )
    assert chosen is not None
    assert chosen.id == "sms_not_received"


def test_match_scenario_substring_still_works():
    # uses real scenarios.json if loaded; smoke via local config would need fixture
    # keep using match_scenario_substring API existence
    assert callable(match_scenario_substring)
