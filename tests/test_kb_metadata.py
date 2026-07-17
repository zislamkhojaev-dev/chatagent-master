"""Tests for KB metadata inference and evaluation."""
from app.services.kb_metadata import (
    MetadataFilter,
    build_metadata_filter,
    chunk_matches_filter,
    chunk_text_with_metadata,
    evaluate_kb_hits,
    infer_metadata_from_text,
    merge_kb_tag_into_meta,
    parse_kb_tag_attrs,
    strip_kb_tags,
    to_qdrant_filter,
)
from app.services.scenarios import Scenario, validate_scenarios
from app.services.taxonomy import get_taxonomy, load_taxonomy


def test_parse_kb_tag_attrs():
    attrs = parse_kb_tag_attrs("audience=client channel=mobile_app topic=qr")
    assert attrs == {
        "audience": "client",
        "channel": "mobile_app",
        "topic": "qr",
    }


def test_strip_kb_tags():
    text = "[kb audience=client topic=qr]\nQR не сканируется."
    assert strip_kb_tags(text) == "QR не сканируется."


def test_chunk_with_kb_tag():
    text = (
        "[kb audience=client channel=mobile_app topic=qr]\n"
        "QR не сканируется: перезапустите приложение."
    )
    records, validation = chunk_text_with_metadata(text, chunk_max_size=800, overlap=0)
    assert len(records) >= 1
    assert records[0]["audience"] == "client"
    assert records[0]["channel"] == "mobile_app"
    assert records[0]["topic"] == "qr"
    assert "[kb" not in records[0]["text"]
    assert validation.tagged_chunks >= 1
    assert validation.legacy_chunks == 0


def test_chunk_with_kb_tag_pdf_glued_blocks():
    """PDF часто склеивает блоки через '\\n \\n' или вообще без пустых строк."""
    text = (
        "[kb audience=client channel=general topic=general] \n \n"
        "Общая информация о Paynet. "
        "[kb audience=client channel=mobile_app topic=sms] \n \n"
        "SMS код не приходит в приложении. "
        "[kb audience=agent channel=agent topic=qr] "
        "Cashout QR для агентов."
    )
    records, validation = chunk_text_with_metadata(text, chunk_max_size=800, overlap=0)
    assert validation.tagged_chunks >= 3
    combos = {(r["audience"], r["channel"], r["topic"]) for r in records}
    assert ("client", "general", "general") in combos
    assert ("client", "mobile_app", "sms") in combos
    assert ("agent", "agent", "qr") in combos


def test_coerce_audience_general_to_both():
    meta = merge_kb_tag_into_meta(
        {"audience": "both", "channel": "general", "topic": "general"},
        {"audience": "general", "channel": "infokiosk"},
        warnings=[],
    )
    assert meta["audience"] == "both"
    assert meta["channel"] == "infokiosk"


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
    assert filt.topics == ["qr"]


def test_build_metadata_filter_from_scenario_fields_not_id():
    scenario = Scenario(
        id="agent_payment",
        triggers=["агент"],
        default_audience="agent",
        default_channel="agent",
        topic="payment",
    )
    filt = build_metadata_filter(scenario, {})
    assert "agent" in filt.audiences
    assert "agent" in filt.channels
    assert filt.topics == ["payment"]


def test_merge_kb_tag_rejects_invalid_audience():
    try:
        merge_kb_tag_into_meta(
            {"audience": "both", "channel": "general", "topic": "general"},
            {"audience": "invalid_audience"},
        )
        assert False, "expected ValueError"
    except ValueError as e:
        assert "invalid audience" in str(e)


def test_merge_kb_tag_accepts_freeform_topic():
    meta = merge_kb_tag_into_meta(
        {"audience": "both", "channel": "general", "topic": "general"},
        {"topic": "cashout"},
    )
    assert meta["topic"] == "cashout"


def test_chunk_with_freeform_topic():
    text = "[kb audience=client topic=cashout]\nИнструкция по cashout."
    records, validation = chunk_text_with_metadata(text, chunk_max_size=800, overlap=0)
    assert records[0]["topic"] == "cashout"
    assert not validation.has_errors()


def test_validate_scenarios_with_custom_topic():
    load_taxonomy(force=True)
    config = validate_scenarios(
        {
            "version": 1,
            "scenarios": [
                {
                    "id": "cashout_help",
                    "triggers": ["cashout"],
                    "default_audience": "agent",
                    "topic": "cashout",
                }
            ],
        }
    )
    assert config.scenarios[0].topic == "cashout"


def test_validate_scenarios_against_taxonomy():
    load_taxonomy(force=True)
    config = validate_scenarios(
        {
            "version": 1,
            "scenarios": [
                {
                    "id": "test",
                    "triggers": ["test"],
                    "default_audience": "client",
                    "topic": "qr",
                }
            ],
        }
    )
    assert len(config.scenarios) == 1


def test_to_qdrant_filter_returns_model():
    filt = MetadataFilter(audiences=["client"], channels=["mobile_app"])
    qf = to_qdrant_filter(filt)
    assert qf is not None
    assert len(qf.must) == 2
    assert qf.must[0].key == "audience"
