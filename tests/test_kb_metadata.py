"""Tests for KB metadata inference and evaluation."""
from app.services.kb_metadata import (
    MetadataFilter,
    build_metadata_filter,
    chunk_matches_filter,
    evaluate_kb_hits,
    infer_metadata_from_text,
    to_qdrant_filter,
)
from app.services.scenarios import Scenario, load_scenarios


def test_infer_metadata_agent_section():
    meta = infer_metadata_from_text(
        "Агент не выдал чек после cashout операции.",
        section="Оплата через агента",
    )
    assert meta["audience"] == "agent"
    assert meta["channel"] == "agent"


def test_infer_metadata_client_mobile():
    meta = infer_metadata_from_text(
        "В мобильном приложении Paynet не приходит SMS код.",
        section="Мобильное приложение",
    )
    assert meta["audience"] == "client"
    assert meta["channel"] == "mobile_app"
    assert meta["topic"] == "sms"


def test_chunk_matches_filter_audience():
    meta = {"audience": "client", "channel": "mobile_app", "topic": "sms"}
    filt = MetadataFilter(audiences=["client"])
    assert chunk_matches_filter(meta, filt) is True
    filt_agent = MetadataFilter(audiences=["agent"])
    assert chunk_matches_filter(meta, filt_agent) is False


def test_evaluate_sufficient_single_audience():
    hits = [
        (0, 0.05, {"audience": "client", "channel": "mobile_app"}),
        (1, 0.03, {"audience": "both", "channel": "general"}),
    ]
    ev = evaluate_kb_hits(hits)
    assert ev.is_sufficient is True
    assert ev.is_ambiguous is False


def test_evaluate_ambiguous_mixed_audiences():
    hits = [
        (0, 0.05, {"audience": "client", "channel": "mobile_app"}),
        (1, 0.04, {"audience": "agent", "channel": "agent"}),
    ]
    ev = evaluate_kb_hits(hits)
    assert ev.is_ambiguous is True
    assert ev.is_sufficient is False


def test_build_metadata_filter_default_client():
    scenario = Scenario(
        id="qr_issue",
        triggers=["qr"],
        default_audience="client",
        topic="qr",
    )
    filt = build_metadata_filter(scenario, {})
    assert "client" in filt.audiences


def test_to_qdrant_filter_returns_model():
    filt = MetadataFilter(audiences=["client"], channels=["mobile_app"])
    qf = to_qdrant_filter(filt)
    assert qf is not None
    assert len(qf.must) == 2
    assert qf.must[0].key == "audience"
