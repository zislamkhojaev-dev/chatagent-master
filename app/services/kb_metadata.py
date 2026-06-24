"""Метаданные чанков KB: инференс при индексации, фильтры и оценка поиска."""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

AUDIENCE_CLIENT = "client"
AUDIENCE_AGENT = "agent"
AUDIENCE_BOTH = "both"

CHANNEL_MOBILE = "mobile_app"
CHANNEL_AGENT = "agent"
CHANNEL_INFOKIOSK = "infokiosk"
CHANNEL_GENERAL = "general"

TOPIC_KEYWORDS = {
    "qr": ["qr", "qr-kod", "qr kod", "сканирован", "skaner"],
    "payment": ["оплат", "to'lov", "платеж", "payment"],
    "refund": ["возврат", "qaytarish", "отмен", "bekor", "ошибочн"],
    "sms": ["sms", "смс", "код не приходит"],
    "identification": ["идентификац", "identifikats"],
}

SECTION_RULES: List[tuple] = [
    (re.compile(r"агент|agent|cashout|агентск", re.I), AUDIENCE_AGENT, CHANNEL_AGENT),
    (re.compile(r"инфокиоск|infokiosk|киоск|kiosk|терминал", re.I), AUDIENCE_BOTH, CHANNEL_INFOKIOSK),
    (re.compile(r"мобильн|приложен|mobil\s*ilova|ilova|paynet\s*app", re.I), AUDIENCE_CLIENT, CHANNEL_MOBILE),
    (re.compile(r"общ|umumiy|general|для\s+всех", re.I), AUDIENCE_BOTH, CHANNEL_GENERAL),
]


def default_chunk_metadata() -> Dict[str, Any]:
    return {
        "audience": AUDIENCE_BOTH,
        "channel": CHANNEL_GENERAL,
        "topic": "general",
        "section": "",
    }


def infer_metadata_from_text(text: str, section: str = "") -> Dict[str, Any]:
    """Определяет metadata по тексту чанка и текущему разделу PDF."""
    meta = default_chunk_metadata()
    meta["section"] = section
    combined = f"{section} {text}".lower()

    for pattern, audience, channel in SECTION_RULES:
        if pattern.search(combined):
            meta["audience"] = audience
            meta["channel"] = channel
            break

    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            meta["topic"] = topic
            break

    # Уточнение audience по явным маркерам в тексте чанка
    if re.search(r"\bагент\b|agent|cashout", combined) and not re.search(
        r"мобильн|приложен|ilova|клиент", combined
    ):
        if meta["audience"] == AUDIENCE_BOTH:
            meta["audience"] = AUDIENCE_AGENT
    if re.search(r"мобильн|приложен|ilova|клиент|foydalanuvchi", combined):
        if meta["audience"] == AUDIENCE_BOTH:
            meta["audience"] = AUDIENCE_CLIENT

    return meta


def is_section_header(paragraph: str) -> bool:
    """Короткая строка или markdown-заголовок — вероятный заголовок раздела."""
    p = paragraph.strip()
    if not p or len(p) > 120:
        return False
    if p.startswith("#"):
        return True
    if len(p.split()) <= 8 and any(rule[0].search(p) for rule in SECTION_RULES):
        return True
    return False


def chunk_text_with_metadata(
    text: str,
    chunk_max_size: int = 800,
    overlap: int = 100,
) -> List[Dict[str, Any]]:
    """Семантический чанкинг с наследованием metadata от раздела PDF."""
    from app.services.chunking import semantic_chunking

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    current_section = ""
    sectioned_text_parts: List[tuple] = []

    for p in paragraphs:
        if is_section_header(p):
            current_section = p.lstrip("#").strip()
            continue
        sectioned_text_parts.append((current_section, p))

    records: List[Dict[str, Any]] = []
    for section, para in sectioned_text_parts:
        sub_chunks = semantic_chunking(para, chunk_max_size=chunk_max_size, overlap=overlap)
        for chunk_text in sub_chunks:
            meta = infer_metadata_from_text(chunk_text, section)
            records.append({"text": chunk_text, **meta})

    if not records:
        for chunk_text in semantic_chunking(text, chunk_max_size=chunk_max_size, overlap=overlap):
            meta = infer_metadata_from_text(chunk_text)
            records.append({"text": chunk_text, **meta})

    logger.info("Built %s chunks with metadata", len(records))
    return records


def normalize_chunk_record(item: Any) -> Dict[str, Any]:
    """Поддержка legacy формата (строка) и нового (dict с metadata)."""
    if isinstance(item, str):
        meta = infer_metadata_from_text(item)
        return {"text": item, **meta}
    if isinstance(item, dict):
        text = item.get("text", "")
        base = infer_metadata_from_text(text, item.get("section", ""))
        base.update({k: item.get(k, base[k]) for k in ("audience", "channel", "topic", "section")})
        base["text"] = text
        return base
    return {**default_chunk_metadata(), "text": str(item)}


def records_to_texts(records: List[Dict[str, Any]]) -> List[str]:
    return [r["text"] for r in records]


@dataclass
class MetadataFilter:
    audiences: Optional[List[str]] = None
    channels: Optional[List[str]] = None
    topics: Optional[List[str]] = None

    def is_empty(self) -> bool:
        return not self.audiences and not self.channels and not self.topics


def chunk_matches_filter(meta: Dict[str, Any], filt: Optional[MetadataFilter]) -> bool:
    if not filt or filt.is_empty():
        return True
    audience = meta.get("audience", AUDIENCE_BOTH)
    channel = meta.get("channel", CHANNEL_GENERAL)
    topic = meta.get("topic", "general")

    if filt.audiences:
        allowed = set(filt.audiences) | {AUDIENCE_BOTH}
        if audience not in allowed:
            return False
    if filt.channels:
        allowed = set(filt.channels) | {CHANNEL_GENERAL}
        if channel not in allowed:
            return False
    if filt.topics:
        if topic not in filt.topics and topic != "general":
            return False
    return True


def map_slot_to_audience(slot_value: str) -> Optional[str]:
    v = slot_value.lower()
    if any(w in v for w in ("агент", "agent", "cashout")):
        return AUDIENCE_AGENT
    if any(w in v for w in ("клиент", "client", "ilova", "приложен", "mobil", "foydalanuvchi")):
        return AUDIENCE_CLIENT
    return None


def map_slot_to_channel(slot_value: str) -> Optional[str]:
    v = slot_value.lower()
    if any(w in v for w in ("инфокиоск", "infokiosk", "киоск", "kiosk", "терминал")):
        return CHANNEL_INFOKIOSK
    if any(w in v for w in ("агент", "agent")):
        return CHANNEL_AGENT
    if any(w in v for w in ("приложен", "ilova", "mobil", "mobile")):
        return CHANNEL_MOBILE
    return None


def build_metadata_filter(
    scenario: Optional[Any],
    slots: dict,
) -> MetadataFilter:
    """Строит фильтр: по умолчанию client+both, уточняется слотами и сценарием."""
    audiences: List[str] = []
    channels: List[str] = []

    default_audience = getattr(scenario, "default_audience", None) or AUDIENCE_CLIENT
    default_channel = getattr(scenario, "default_channel", None)

    if slots.get("user_type"):
        mapped = map_slot_to_audience(slots["user_type"])
        if mapped:
            audiences = [mapped, AUDIENCE_BOTH]
    if slots.get("payment_channel"):
        ch = map_slot_to_channel(slots["payment_channel"])
        if ch:
            channels.append(ch)

    if not audiences:
        if scenario and scenario.id in ("agent_payment",):
            audiences = [AUDIENCE_AGENT, AUDIENCE_BOTH]
        elif scenario and scenario.id in ("infokiosk",):
            audiences = [AUDIENCE_BOTH]
            channels.append(CHANNEL_INFOKIOSK)
        elif scenario and scenario.id in ("mobile_app", "sms_not_received"):
            audiences = [AUDIENCE_CLIENT, AUDIENCE_BOTH]
            channels.append(CHANNEL_MOBILE)
        else:
            audiences = [default_audience, AUDIENCE_BOTH]

    if default_channel and default_channel not in channels:
        channels.append(default_channel)

    topic = getattr(scenario, "topic", None) if scenario else None
    topics = [topic] if topic else None

    return MetadataFilter(audiences=audiences, channels=channels or None, topics=topics)


@dataclass
class KbSearchEvaluation:
    is_sufficient: bool = False
    is_ambiguous: bool = False
    is_empty: bool = True
    audiences_found: Set[str] = field(default_factory=set)
    top_score: float = 0.0
    chunk_count: int = 0


def evaluate_kb_hits(
    hits: List[tuple],
    *,
    min_rrf_score: float = 0.015,
) -> KbSearchEvaluation:
    """
    Оценка результатов гибридного поиска.
    hits: [(chunk_idx, score, metadata_dict), ...]
    """
    if not hits:
        return KbSearchEvaluation(is_empty=True)

    top_score = hits[0][1]
    min_score = min_rrf_score if top_score < 1.0 else 0.5

    eval_result = KbSearchEvaluation(
        is_empty=False,
        chunk_count=len(hits),
        top_score=top_score,
    )
    primary_audiences: Set[str] = set()
    for _, score, meta in hits[:5]:
        if score < min_score:
            continue
        aud = meta.get("audience", AUDIENCE_BOTH)
        eval_result.audiences_found.add(aud)
        if aud != AUDIENCE_BOTH:
            primary_audiences.add(aud)

    if top_score < min_score:
        return eval_result

    if len(primary_audiences) <= 1:
        eval_result.is_sufficient = True
    else:
        eval_result.is_ambiguous = True
    return eval_result


def audience_filter_values(filt: MetadataFilter) -> Optional[List[str]]:
    if not filt.audiences:
        return None
    return list(set(filt.audiences) | {AUDIENCE_BOTH})


def to_qdrant_filter(filt: Optional[MetadataFilter]) -> Optional[Any]:
    """Filter model для qdrant-client query_points."""
    if not filt or filt.is_empty():
        return None
    try:
        from qdrant_client.http import models as qmodels
    except ImportError:
        logger.warning("qdrant_client models unavailable, skipping Qdrant filter")
        return None

    must = []
    if filt.audiences:
        must.append(
            qmodels.FieldCondition(
                key="audience",
                match=qmodels.MatchAny(any=audience_filter_values(filt) or []),
            )
        )
    if filt.channels:
        must.append(
            qmodels.FieldCondition(
                key="channel",
                match=qmodels.MatchAny(any=list(set(filt.channels) | {CHANNEL_GENERAL})),
            )
        )
    if filt.topics:
        must.append(
            qmodels.FieldCondition(
                key="topic",
                match=qmodels.MatchAny(any=filt.topics),
            )
        )
    if not must:
        return None
    return qmodels.Filter(must=must)
