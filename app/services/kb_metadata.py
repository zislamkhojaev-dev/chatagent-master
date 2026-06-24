"""Метаданные чанков KB: явные [kb] теги, legacy fallback, фильтры поиска."""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.taxonomy import Taxonomy, get_taxonomy, validate_taxonomy_field

logger = logging.getLogger(__name__)

AUDIENCE_CLIENT = "client"
AUDIENCE_AGENT = "agent"
AUDIENCE_BOTH = "both"

CHANNEL_MOBILE = "mobile_app"
CHANNEL_AGENT = "agent"
CHANNEL_INFOKIOSK = "infokiosk"
CHANNEL_GENERAL = "general"

KB_TAG_LINE_RE = re.compile(r"^\[kb\s+([^\]]+)\]\s*$", re.I)
KB_TAG_PREFIX_RE = re.compile(r"^\[kb\s+([^\]]+)\]\s*\n?", re.I | re.M)
KB_TAG_INLINE_RE = re.compile(r"\[kb\s+[^\]]+\]\s*", re.I)


def default_chunk_metadata(taxonomy: Optional[Taxonomy] = None) -> Dict[str, Any]:
    tax = taxonomy or get_taxonomy()
    defaults = tax.defaults
    return {
        "audience": defaults.get("audience", AUDIENCE_BOTH),
        "channel": defaults.get("channel", CHANNEL_GENERAL),
        "topic": defaults.get("topic", "general"),
        "section": "",
    }


def parse_kb_tag_attrs(attr_string: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for part in attr_string.strip().split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        attrs[key.lower().strip()] = value.lower().strip()
    return attrs


def merge_kb_tag_into_meta(
    meta: Dict[str, Any],
    attrs: Dict[str, str],
    taxonomy: Optional[Taxonomy] = None,
) -> Dict[str, Any]:
    tax = taxonomy or get_taxonomy()
    out = meta.copy()
    for key in ("audience", "channel", "topic", "section"):
        if key not in attrs:
            continue
        value = attrs[key]
        if key in ("audience", "channel"):
            validate_taxonomy_field(key, value, context="[kb] tag")
        out[key] = value
    return out


def strip_kb_tags(text: str) -> str:
    return KB_TAG_INLINE_RE.sub("", text).strip()


def infer_metadata_legacy(
    text: str,
    section: str = "",
    taxonomy: Optional[Taxonomy] = None,
) -> Dict[str, Any]:
    """Legacy fallback: эвристики по заголовку раздела и ключевым словам."""
    tax = taxonomy or get_taxonomy()
    meta = default_chunk_metadata(tax)
    meta["section"] = section
    combined = f"{section} {text}".lower()

    for pattern, audience, channel in tax.section_rules:
        if pattern.search(combined):
            meta["audience"] = audience
            meta["channel"] = channel
            break

    for topic, keywords in tax.topic_keywords.items():
        if any(kw in combined for kw in keywords):
            meta["topic"] = topic
            break

    if re.search(r"\bагент\b|agent|cashout", combined) and not re.search(
        r"мобильн|приложен|ilova|клиент", combined
    ):
        if meta["audience"] == AUDIENCE_BOTH:
            meta["audience"] = AUDIENCE_AGENT
    if re.search(r"мобильн|приложен|ilova|клиент|foydalanuvchi", combined):
        if meta["audience"] == AUDIENCE_BOTH:
            meta["audience"] = AUDIENCE_CLIENT

    return meta


def infer_metadata_from_text(text: str, section: str = "") -> Dict[str, Any]:
    """Обратная совместимость: делегирует в legacy fallback."""
    return infer_metadata_legacy(text, section)


def infer_section_meta(section: str, taxonomy: Optional[Taxonomy] = None) -> Dict[str, Any]:
    tax = taxonomy or get_taxonomy()
    meta = default_chunk_metadata(tax)
    meta["section"] = section
    for pattern, audience, channel in tax.section_rules:
        if pattern.search(section):
            meta["audience"] = audience
            meta["channel"] = channel
            break
    return meta


def is_section_header(paragraph: str, taxonomy: Optional[Taxonomy] = None) -> bool:
    tax = taxonomy or get_taxonomy()
    p = paragraph.strip()
    if not p or len(p) > 120:
        return False
    if p.startswith("#"):
        return True
    if len(p.split()) <= 8 and any(rule[0].search(p) for rule in tax.section_rules):
        return True
    return False


@dataclass
class ChunkValidationReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tagged_chunks: int = 0
    legacy_chunks: int = 0
    total_chunks: int = 0

    def has_errors(self) -> bool:
        return bool(self.errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "tagged_chunks": self.tagged_chunks,
            "legacy_chunks": self.legacy_chunks,
            "total_chunks": self.total_chunks,
        }


def validate_chunk_metadata(
    record: Dict[str, Any],
    taxonomy: Optional[Taxonomy] = None,
) -> List[str]:
    tax = taxonomy or get_taxonomy()
    errors: List[str] = []
    for key in ("audience", "channel"):
        value = record.get(key)
        if value and not tax.is_valid(key, value):
            errors.append(f"invalid {key} '{value}'")
    return errors


def finalize_chunk_record(
    text: str,
    meta: Dict[str, Any],
    *,
    metadata_source: str,
    validation: ChunkValidationReport,
    taxonomy: Optional[Taxonomy] = None,
) -> Dict[str, Any]:
    record = {"text": text, **meta}
    validation.errors.extend(validate_chunk_metadata(record, taxonomy))
    if metadata_source == "tag":
        validation.tagged_chunks += 1
    else:
        validation.legacy_chunks += 1
    validation.total_chunks += 1
    return record


def chunk_text_with_metadata(
    text: str,
    chunk_max_size: int = 800,
    overlap: int = 100,
) -> Tuple[List[Dict[str, Any]], ChunkValidationReport]:
    """
    Семантический чанкинг с явными [kb audience=... channel=... topic=...] тегами.
    Без тегов — legacy fallback по заголовкам разделов и ключевым словам из taxonomy.json.
    """
    from app.services.chunking import semantic_chunking

    taxonomy = get_taxonomy()
    validation = ChunkValidationReport()
    current_meta = default_chunk_metadata(taxonomy)
    current_section = ""
    blocks: List[Tuple[str, Dict[str, Any], str, str]] = []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for paragraph in paragraphs:
        if KB_TAG_LINE_RE.match(paragraph):
            attrs = parse_kb_tag_attrs(KB_TAG_LINE_RE.match(paragraph).group(1))
            current_meta = merge_kb_tag_into_meta(current_meta, attrs, taxonomy)
            continue

        body = paragraph
        block_meta = current_meta.copy()
        metadata_source = "section"

        prefix_match = KB_TAG_PREFIX_RE.match(paragraph)
        if prefix_match:
            attrs = parse_kb_tag_attrs(prefix_match.group(1))
            block_meta = merge_kb_tag_into_meta(current_meta.copy(), attrs, taxonomy)
            body = paragraph[prefix_match.end() :].strip()
            metadata_source = "tag"
        elif is_section_header(paragraph, taxonomy):
            current_section = paragraph.lstrip("#").strip()
            current_meta = infer_section_meta(current_section, taxonomy)
            continue

        if not body:
            continue
        blocks.append((current_section, block_meta, body, metadata_source))

    records: List[Dict[str, Any]] = []
    for section, base_meta, body, metadata_source in blocks:
        sub_chunks = semantic_chunking(body, chunk_max_size=chunk_max_size, overlap=overlap)
        for chunk_text in sub_chunks:
            clean_text = strip_kb_tags(chunk_text)
            if not clean_text:
                continue
            if metadata_source == "tag":
                meta = {**base_meta, "section": section}
            else:
                meta = infer_metadata_legacy(clean_text, section, taxonomy)
                meta.update(
                    {
                        k: base_meta[k]
                        for k in ("audience", "channel", "topic")
                        if base_meta.get(k) != default_chunk_metadata(taxonomy).get(k)
                    }
                )
            records.append(
                finalize_chunk_record(
                    clean_text,
                    meta,
                    metadata_source=metadata_source,
                    validation=validation,
                    taxonomy=taxonomy,
                )
            )

    if not records:
        for chunk_text in semantic_chunking(text, chunk_max_size=chunk_max_size, overlap=overlap):
            clean_text = strip_kb_tags(chunk_text)
            if not clean_text:
                continue
            meta = infer_metadata_legacy(clean_text, taxonomy=taxonomy)
            records.append(
                finalize_chunk_record(
                    clean_text,
                    meta,
                    metadata_source="legacy",
                    validation=validation,
                    taxonomy=taxonomy,
                )
            )

    if validation.legacy_chunks:
        validation.warnings.append(
            f"{validation.legacy_chunks}/{validation.total_chunks} chunks indexed "
            "without explicit [kb] tags (legacy inference)"
        )

    logger.info(
        "Built %s chunks with metadata (%s tagged, %s legacy)",
        len(records),
        validation.tagged_chunks,
        validation.legacy_chunks,
    )
    return records, validation


def normalize_chunk_record(item: Any) -> Dict[str, Any]:
    """Поддержка legacy формата (строка) и нового (dict с metadata)."""
    if isinstance(item, str):
        meta = infer_metadata_legacy(item)
        return {"text": item, **meta}
    if isinstance(item, dict):
        text = item.get("text", "")
        stored = {
            k: item[k]
            for k in ("audience", "channel", "topic", "section")
            if item.get(k) is not None
        }
        if stored.get("audience") or stored.get("channel") or stored.get("topic"):
            base = default_chunk_metadata()
            base.update(stored)
            base["text"] = text
            return base
        base = infer_metadata_legacy(text, item.get("section", ""))
        base.update(stored)
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
    """Фильтр из слотов и полей сценария (default_audience, default_channel, topic)."""
    audiences: List[str] = []
    channels: List[str] = []

    if slots.get("user_type"):
        mapped = map_slot_to_audience(slots["user_type"])
        if mapped:
            audiences = [mapped, AUDIENCE_BOTH]
    if slots.get("payment_channel"):
        ch = map_slot_to_channel(slots["payment_channel"])
        if ch:
            channels.append(ch)

    if not audiences:
        default_audience = getattr(scenario, "default_audience", None) or AUDIENCE_CLIENT
        audiences = [default_audience, AUDIENCE_BOTH]

    if scenario:
        default_channel = getattr(scenario, "default_channel", None)
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
