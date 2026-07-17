"""Загрузка/индексация БЗ: PDF → чанки → Qdrant + BM25. Единственный источник — kb/*.pdf (ТЗ Б.1, Б.7)."""
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pdfplumber
from redis.asyncio import Redis

from app.core.config import settings
from app.services.embeddings import get_embedding_with_semaphore

logger = logging.getLogger(__name__)


def get_file_hash(filepath: str | Path) -> str:
    try:
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error("Failed to compute hash for %s: %s", filepath, e)
        return ""


def _kb_path() -> Path:
    """Единственный путь к PDF БЗ — kb/KB.pdf (config.KB_PATH)."""
    return settings.KB_PATH


def _chunks_filename() -> Path:
    return settings.KB_DIR / "knowledge_base.json"


def _hash_file_path() -> Path:
    return settings.KB_DIR / "kb_hash.json"


def resolve_kb_source_path(explicit: Optional[Path] = None) -> Path:
    """
    Источник для индексации:
    1) явный путь
    2) kb/KB.pdf
    3) kb/kb.txt / kb/KB.txt (если PDF нет)
    """
    if explicit is not None:
        return explicit
    if settings.KB_PATH.exists():
        return settings.KB_PATH
    for name in ("kb.txt", "KB.txt"):
        candidate = settings.KB_DIR / name
        if candidate.exists():
            return candidate
    return settings.KB_PATH


def extract_kb_text(path: Path) -> str:
    """Читает текст БЗ из PDF или TXT (UTF-8)."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1251"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Cannot decode text KB file: {path}")

    if suffix != ".pdf":
        raise ValueError(f"Unsupported KB file type: {path.suffix} (use .pdf or .txt)")

    full_text = ""
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
    return full_text


def save_knowledge_base(chunks: List[Any], filename: Optional[Path] = None) -> None:
    """Сохраняет чанки: list[dict] с metadata или list[str] legacy."""
    path = filename or _chunks_filename()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from app.services.kb_metadata import normalize_chunk_record

        records = [normalize_chunk_record(c) for c in chunks]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
        logger.info("Knowledge base saved to %s (%s chunks)", path, len(records))
    except Exception as e:
        logger.error("Failed to save knowledge base: %s", e)


def save_kb_hash(pdf_hash: str, filename: Optional[Path] = None) -> None:
    path = filename or _hash_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = load_kb_hash_data(path) or {}
        data["pdf_hash"] = pdf_hash
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.info("PDF hash saved to %s", path)
    except Exception as e:
        logger.error("Failed to save PDF hash: %s", e)


def load_kb_hash_data(filename: Optional[Path] = None) -> Optional[dict]:
    path = filename or _hash_file_path()
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
    except Exception as e:
        logger.error("Failed to load PDF hash: %s", e)
        return None


def load_kb_hash(filename: Optional[Path] = None) -> str:
    data = load_kb_hash_data(filename)
    return (data or {}).get("pdf_hash", "")


def load_knowledge_base_chunks(
    filename: Optional[Path] = None,
) -> Optional[List[dict]]:
    """Загружает чанки с metadata. Legacy list[str] нормализуется при чтении."""
    path = filename or _chunks_filename()
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            from app.services.kb_metadata import normalize_chunk_record

            records = [normalize_chunk_record(item) for item in raw]
            logger.info("Knowledge base loaded from %s (%s chunks)", path, len(records))
            return records
        return None
    except Exception as e:
        logger.error("Failed to load knowledge base: %s", e)
        return None


async def index_pdf_to_qdrant(
    redis_client: Redis,
    pdf_path: Optional[Path] = None,
) -> Tuple[List[dict], str, Any]:
    """
    Индексирует PDF/TXT в Qdrant: чанки → эмбеддинги → коллекция kb_v1_{timestamp} → alias kb_current.
    Сохраняет чанки в knowledge_base.json. Возвращает (records, collection_name, validation).
    """
    from app.services.qdrant_store import (
        get_qdrant_client,
        create_collection,
        upsert_points,
        set_alias,
        delete_alias,
        get_collection_by_alias,
    )

    from app.services.kb_metadata import (
        chunk_text_with_metadata,
        records_to_texts,
    )

    path = resolve_kb_source_path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"KB file not found: {path}")
    client = get_qdrant_client()
    if not client:
        raise RuntimeError("Qdrant unavailable")
    full_text = extract_kb_text(path)
    logger.info("Extracted %s chars from %s", len(full_text), path.name)
    kb_records, validation = chunk_text_with_metadata(full_text, chunk_max_size=800, overlap=100)
    if validation.has_errors():
        raise ValueError(
            "KB validation failed: " + "; ".join(validation.errors[:10])
        )
    for warning in validation.warnings:
        logger.warning("KB indexing: %s", warning)
    chunks = records_to_texts(kb_records)
    logger.info("Created %s chunks with metadata for Qdrant", len(chunks))
    embeddings = await asyncio.gather(
        *(get_embedding_with_semaphore(c, redis_client) for c in chunks)
    )
    valid_pairs = [(i, e) for i, (e, _) in enumerate(zip(embeddings, chunks)) if e]
    if not valid_pairs:
        raise RuntimeError("No embeddings generated")
    indices = [p[0] for p in valid_pairs]
    embs = [p[1] for p in valid_pairs]
    valid_records = [kb_records[i] for i in indices]
    collection_name = f"{settings.QDRANT_COLLECTION_PREFIX}_{int(time.time())}"
    create_collection(client, collection_name, vector_size=1536)
    ids = list(range(len(valid_records)))
    payloads = [
        {
            "text": rec["text"],
            "id": i,
            "audience": rec.get("audience", "both"),
            "channel": rec.get("channel", "general"),
            "topic": rec.get("topic", "general"),
            "section": rec.get("section", ""),
        }
        for i, rec in enumerate(valid_records)
    ]
    upsert_points(client, collection_name, ids, embs, payloads)
    # Переключить alias: удалить старый, создать новый (всё через REST, без моделей qdrant-client)
    try:
        old = get_collection_by_alias(client, settings.QDRANT_ALIAS)
        if old:
            delete_alias(settings.QDRANT_ALIAS)
    except Exception as e:
        logger.debug("No previous alias or error: %s", e)
    set_alias(client, collection_name, settings.QDRANT_ALIAS)
    save_knowledge_base(kb_records)
    hash_data = load_kb_hash_data() or {}
    hash_data["pdf_hash"] = get_file_hash(str(path))
    hash_data["validation"] = validation.to_dict()
    path_hash = _hash_file_path()
    path_hash.parent.mkdir(parents=True, exist_ok=True)
    with open(path_hash, "w", encoding="utf-8") as f:
        json.dump(hash_data, f, ensure_ascii=False)
    return kb_records, collection_name, validation
