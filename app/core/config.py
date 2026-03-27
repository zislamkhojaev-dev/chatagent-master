"""Конфигурация из переменных окружения (ТЗ Б.7, Б.8 п.2)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


class Settings:
    """Настройки приложения."""

    # OpenAI
    OPENAI_API_KEY: str = _get_required("OPENAI_API_KEY")
    OPENAI_EMBEDDING_MODEL: str = os.getenv(
        "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    OPENAI_EMBEDDING_DIMENSIONS: int = int(
        os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536")
    )

    # DB
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "postgres")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "123")

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))

    # App
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    PROMETHEUS_PORT: int = int(os.getenv("PROMETHEUS_PORT", "8001"))

    # KB: единственный источник файлов БЗ — каталог kb/ (файл kb/KB.pdf)
    KB_DIR: Path = Path(os.getenv("KB_DIR", "kb"))
    KB_FILENAME: str = "KB.pdf"
    KB_PATH: Path = KB_DIR / KB_FILENAME
    KB_BACKUP_FILENAME: str = "KB_backup.pdf"
    KB_HASH_FILE: Path = KB_DIR / "kb_hash.json"

    # Константы пайплайна (вынесены в конфиг по Б.8)
    MAX_HISTORY_SIZE: int = int(os.getenv("MAX_HISTORY_SIZE", "3"))
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.9"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "10"))
    MIN_WAIT: int = int(os.getenv("MIN_WAIT", "1"))
    MAX_WAIT: int = int(os.getenv("MAX_WAIT", "5"))
    EMBEDDING_SEMAPHORE_LIMIT: int = int(os.getenv("EMBEDDING_SEMAPHORE_LIMIT", "20"))

    # Admin (Б.4, KB_MANAGEMENT_FEATURE)
    ADMIN_USER: str = os.getenv("ADMIN_USER", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    KB_MAX_FILE_SIZE_MB: int = int(os.getenv("KB_MAX_FILE_SIZE_MB", "20"))

    # Контекст чата (Б.5): лимит символов, TTL в Redis
    CONTEXT_MAX_CHARS: int = int(os.getenv("CONTEXT_MAX_CHARS", "2000"))

    # Qdrant (Б.1)
    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
    QDRANT_COLLECTION_PREFIX: str = "kb_v1"
    QDRANT_ALIAS: str = "kb_current"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


settings = Settings()
