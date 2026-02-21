"""Загрузка/индексация БЗ: PDF → чанки → Qdrant + BM25. Единственный источник — kb/*.pdf (ТЗ Б.1, Б.7)."""
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
from redis.asyncio import Redis

from app.core.config import settings
from app.services.chunking import semantic_chunking
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


def save_knowledge_base(chunks: List[str], filename: Optional[Path] = None) -> None:
    path = filename or _chunks_filename()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)
        logger.info("Knowledge base saved to %s", path)
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
) -> Optional[List[str]]:
    path = filename or _chunks_filename()
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            logger.info("Knowledge base loaded from %s", path)
            return chunks
        return None
    except Exception as e:
        logger.error("Failed to load knowledge base: %s", e)
        return None


async def index_pdf_to_qdrant(
    redis_client: Redis,
    pdf_path: Optional[Path] = None,
) -> Tuple[List[str], str]:
    """
    Индексирует PDF в Qdrant: чанки → эмбеддинги → коллекция kb_v1_{timestamp} → alias kb_current.
    Сохраняет чанки в knowledge_base.json. Возвращает (chunks, collection_name).
    """
    from qdrant_client.http import models as qdrant_models
    from app.services.qdrant_store import (
        get_qdrant_client,
        create_collection,
        upsert_points,
        set_alias,
        get_collection_by_alias,
    )

    path = pdf_path or _kb_path()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    client = get_qdrant_client()
    if not client:
        raise RuntimeError("Qdrant unavailable")
    full_text = ""
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"
    chunks = semantic_chunking(full_text, chunk_max_size=800, overlap=100)
    logger.info("Created %s chunks for Qdrant", len(chunks))
    embeddings = await asyncio.gather(
        *(get_embedding_with_semaphore(c, redis_client) for c in chunks)
    )
    valid = [(i, e, c) for i, (e, c) in enumerate(zip(embeddings, chunks)) if e]
    if not valid:
        raise RuntimeError("No embeddings generated")
    indices, embs, chunks = [x[0] for x in valid], [x[1] for x in valid], [x[2] for x in valid]
    collection_name = f"{settings.QDRANT_COLLECTION_PREFIX}_{int(time.time())}"
    create_collection(client, collection_name, vector_size=1536)
    ids = list(range(len(chunks)))  # Qdrant принимает только int (uint64) или UUID
    payloads = [{"text": c, "id": i} for i, c in enumerate(chunks)]
    upsert_points(client, collection_name, ids, embs, payloads)
    # Переключить alias: удалить старый, создать новый
    try:
        old = get_collection_by_alias(client, settings.QDRANT_ALIAS)
        if old:
            client.update_collection_aliases(
                change_aliases=[
                    qdrant_models.AliasOperations(
                        delete_alias=qdrant_models.DeleteAlias(alias_name=settings.QDRANT_ALIAS)
                    )
                ]
            )
    except Exception as e:
        logger.debug("No previous alias or error: %s", e)
    set_alias(client, collection_name, settings.QDRANT_ALIAS)
    save_knowledge_base(chunks)
    save_kb_hash(get_file_hash(str(path)))
    return chunks, collection_name
