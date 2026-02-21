# Standard Library Imports
import asyncio
import json
import logging
import os
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv
from contextvars import ContextVar

# Third-Party Imports
import asyncpg
import faiss
import numpy as np
import pdfplumber
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from pydantic import BaseModel
from redis.asyncio import Redis, RedisError
from sklearn.metrics.pairwise import cosine_similarity
from contextlib import asynccontextmanager
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, RateLimitError
import httpx
import re

# Configuration Constants
MAX_HISTORY_SIZE = 3
SIMILARITY_THRESHOLD = 0.9
OPERATOR_KEYWORDS = {"оператор", "человек", "operator", "inson", "odam"}
MAX_RETRIES = 10
MIN_WAIT = 1
MAX_WAIT = 5
semaphore = asyncio.Semaphore(20)

# Logging Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load Environment Variables
load_dotenv()

# Required Environment Variables Check
required_env_vars = [
    "OPENAI_API_KEY",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB",
    "PROMETHEUS_PORT"
]
for var in required_env_vars:
    if not os.getenv(var):
        logging.error(f"Missing required environment variable: {var}")
        raise ValueError(f"Missing required environment variable: {var}")

# Clients Initialization
try:
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=httpx.Timeout(
            connect=10.0,  # Connection timeout
            read=10.0,    # Read timeout for embeddings
            write=10.0,   # Write timeout
            pool=30.0     # General timeout for other operations
        )
    )
    logger.info("Successfully initialized AsyncOpenAI client with timeouts")
except Exception as e:
    logger.error(f"Failed to initialize AsyncOpenAI client: {e}")
    raise SystemExit(f"Failed to initialize AsyncOpenAI client: {e}")

# Synonyms Global Variables
synonyms_dict: Dict[str, Dict[str, List[str]]] = {}
synonyms_file_path = "synonyms.json"
synonyms_last_modified = 0

# PII Anonymization Patterns
PII_PATTERNS = [
    {"entity": "PHONE_NUMBER", "regex": r"\+998\s?(?:90|91|93|94|95|97|98|99|33|88)\s?\d{3}\s?\d{2}\s?\d{2}", "replacement": "***"},
    {"entity": "PASSPORT", "regex": r"[A-Z]{2}\d{7}", "replacement": "***"},
    {"entity": "CREDIT_CARD", "regex": r"\b\d{4}\s?-?\d{4}\s?-?\d{4}\s?-?\d{4}\b", "replacement": "***"},
    {"entity": "UZBEK_PINFL", "regex": r"\b\d{14}\b", "replacement": "***"},
    {"entity": "EMAIL_ADDRESS", "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "replacement": "***"}
]

# Prometheus Metrics
REQUEST_COUNT = Counter("requests_total", "Total number of requests", ["endpoint"])
REQUEST_LATENCY = Histogram("request_latency_seconds", "Request latency in seconds", ["endpoint"])
ESCALATION_COUNT = Counter("escalations_total", "Total number of escalations")
HIGH_LOAD_ESCALATIONS_TOTAL = Counter("high_load_escalations_total", "Total number of high load escalations")
UNIQUE_CHATS_TOTAL = Counter("unique_chats_total", "Total number of unique chat IDs ever interacted")
ACTIVE_CHATS = Gauge("active_chats", "Number of currently active chat IDs")
SESSIONS_TOTAL = Counter("sessions_total", "Total number of sessions completed")
SESSION_DURATION = Histogram(
    "session_duration_seconds",
    "Duration of a session in seconds",
    buckets=[30, 60, 120, 180, 300, 600, 900, 1800, 3600, 7200, float("inf")]
)
CLASSIFICATIONS_COUNT = Counter(
    "classifications_total",
    "Total number of classifications",
    ["theme", "category", "subcategory"]
)
SYNONYMS_USAGE_COUNT = Counter(
    "synonyms_usage_total",
    "Total number of synonym replacements",
    ["language", "standard_term"]
)
LANGUAGE_DETECTION_COUNT = Counter(
    "language_detection_total",
    "Total number of language detections",
    ["method", "detected_language"]
)
LANGUAGE_DETECTION_CACHE_HIT = Counter(
    "language_detection_cache_hits_total",
    "Number of cache hits for language detection"
)
LANGUAGE_DETECTION_LATENCY = Histogram(
    "language_detection_latency_seconds",
    "Language detection latency in seconds",
    ["method"]
)
LANGUAGE_DETECTION_ERRORS = Counter(
    "language_detection_errors_total",
    "Number of language detection errors",
    ["method", "error_type"]
)
UNCERTAIN_REQUESTS = Counter("uncertain_requests_total", "Total number of uncertain requests")

# Classification Categories
CATEGORIES = {
    "Претензия / Shikoyat": {
        "Оплата услуг через агента / Agent orqali xizmatlar uchun to'lov": [
            "агент не совершил платеж", "agent to'lovni amalga oshirmadi", "агент не выдал чек", 
            "agent chek bermadi", "агент отказался оказывать услуги", "agent xizmat ko'rsatishdan bosh tortdi"
        ],
        "Оплата услуг через инфокиоск / Infokiosk orqali xizmatlar uchun to'lov": [
            "инфокиоск не выдал чек", "infokiosk chek bermadi", "infokiosk"
        ],
        "Оплата через Paynet Avia / Paynet Avia orqali to'lov": [
            "невозможно купить билет", "bilet sotib olishning iloji yo'q", "aviabilet"
        ],
        "Прочие жалобы / Boshqa shikoyatlar": [
            "отказ от использования мп paynet", "paynet mobil ilovasidan foydalanishdan bosh tortish", 
            "не согласен с решением заявки", "ariza qaroriga rozi emasman", "мошенничество", "firibgarlik",
            "жалоба на сотрудника", "xodimdan shikoyat"
        ]
    },
    "Сервисное обслуживание / Xizmat ko'rsatish": {
        "Документы / Hujjatlar": [
            "предоставить чек оплаты", "to'lov chekini taqdim etish", "реквизиты компании", 
            "kompaniya rekvizitlari"
        ]
    },
    "Вакансии / Vakansiyalar": {
        "Вакансии / Vakansiyalar": [
            "узнать о вакансиях", "vakansiyalar haqida bilish", "куда отправить резюме", 
            "rezyumeni qayerga yuborish"
        ]
    },
    "Paynet Avia / Paynet Avia": {
        "Информация / Ma'lumot": [
            "отправка билета", "biletni jo'natish", "avia"
        ]
    },
    "Хулиганство / Bezorilik": {
        "Хулиганство / Bezorilik": [
            "мат", "bo'ralab so'kinish", "хулиганство", "bezorilik"
        ]
    },
    "Предложения / Takliflar": {
        "Предложения / Takliflar": [
            "вопросы по инфокиоску", "infokiosk bo'yicha savollar", "прочие предложения", 
            "boshqa takliflar", "хочет стать агентом", "agent bo'lishni xohlaydi",
            "хочет стать партнером", "hamkor bo'lishni xohlaydi"
        ]
    },
    "Информация / Ma'lumot": {
        "Акции / Aksiyalar": [
            "беспроцентный перевод", "foizsiz o'tkazma", "монеты", "tanga"
        ],
        "Запрос / So'rov": [
            "коммуникация", "kommunikatsiya"
        ],
        "Оплата услуг через агента / Agent orqali xizmatlar uchun to'lov": [
            "cashout", "cashout nfc", "отмена ошибочного платежа", 
            "xato to'lovni bekor qilish", "пополнение humo", "humo", "пополнение uzcard", "uzcard",
            "проверка оплат", "to'lovlarni tekshirish", "результат претензии", 
            "shikoyat natijasi"
        ],
        "Оплата услуг через инфокиоск / Infokiosk orqali xizmatlar uchun to'lov": [
            "cashin", "не отображается сумма", "pul balansi ko'rsatilmayapti", 
            "не работает инфокиоск", "infokiosk ishlamayapti", "общая информация", 
            "umumiy ma'lumot", "оплата через другие платежные инструменты",
            "boshqa to'lov vositalari orqali to'lov", "отмена ошибочного платежа", 
            "xato to'lovni bekor qilish", "пополнение visa", "visa", "пополнение mastercard", "mastercard",
            "проверка оплат", "to'lovlarni tekshirish", "просит предоставить чек", 
            "chek berishni so'raydi", "электронный кошелек", "elektron hamyon"
        ],
        "Оплата услуг через мобильное приложение / Mobil ilova orqali xizmatlar uchun to'lov": [
            "crypto", "p2p wallet", "paynet gold", "paynet nasiya", "visa direct", "identifikatsiya",
            "jd bilety", "poyezd chiptalari", "информация о бонусах и кешбек", 
            "bonuslar va keshbek haqida ma'lumot", "не приходит sms", "sms kelmayapti", 
            "общая информация о мобильном приложении", "mobil ilova haqida umumiy ma'lumot",
            "проверка p2p", "p2p tekshiruvi"
        ],
        "Оплата через Paynet Avia / Paynet Avia orqali to'lov": [
            "paynet avia", "общая информация", "umumiy ma'lumot", "результат претензии", 
            "shikoyat natijasi"
        ],
        "Поставщики / Provayderlar": [
            "foyda", "общая информация", "umumiy ma'lumot"
        ]
    }
}

# Pydantic Models
class MessageResponse(BaseModel):
    status: str
    chat_id: str
    response: str
    classification: Dict[str, str]
    escalation: bool
    history: List[str] = []

class EscalateRequest(BaseModel):
    chat_id: str

class MessageRequest(BaseModel):
    chat_id: str
    message: str

class EscalateResponse(BaseModel):
    status: str
    chat_id: str
    response: str
    history: List[str]

# Global Variables
vector_store: Optional[faiss.Index] = None
knowledge_base: Optional[List[str]] = None
db_pool: Optional[asyncpg.Pool] = None
redis_client: Optional[Redis] = None

# Redis Safe Operations
async def safe_redis_get(redis_client: Redis, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        result = await redis_client.get(key) or default
        return result
    except RedisError as e:
        logger.warning(f"Redis unavailable for get {key}: {e}")
        return default

async def safe_redis_set(redis_client: Redis, key: str, value: str, ex: Optional[int] = None) -> None:
    try:
        if ex:
            await redis_client.setex(key, ex, value)
        else:
            await redis_client.set(key, value)
    except RedisError as e:
        logger.warning(f"Redis unavailable for set {key}: {e}")

async def safe_redis_incr(redis_client: Redis, key: str) -> int:
    try:
        result = await redis_client.incr(key)
        return result
    except RedisError as e:
        logger.warning(f"Redis unavailable for incr {key}: {e}")
        return 0

async def safe_redis_delete(redis_client: Redis, *keys: str) -> None:
    try:
        await redis_client.delete(*keys)
    except RedisError as e:
        logger.warning(f"Redis unavailable for delete {keys}: {e}")

async def safe_redis_lpush(redis_client: Redis, key: str, value: str) -> None:
    try:
        await redis_client.lpush(key, value)
    except RedisError as e:
        logger.warning(f"Redis unavailable for lpush {key}: {e}")

async def safe_redis_ltrim(redis_client: Redis, key: str, start: int, end: int) -> None:
    try:
        await redis_client.ltrim(key, start, end)
    except RedisError as e:
        logger.warning(f"Redis unavailable for ltrim {key}: {e}")

async def safe_redis_lrange(redis_client: Redis, key: str, start: int, end: int) -> List[str]:
    try:
        result = await redis_client.lrange(key, start, end)
        return result
    except RedisError as e:
        logger.warning(f"Redis unavailable for lrange {key}: {e}")
        return []

async def safe_redis_scan_iter(redis_client: Redis, pattern: str) -> List[str]:
    try:
        keys = []
        async for key in redis_client.scan_iter(match=pattern, count=1000):
            keys.append(key)
        return keys
    except RedisError as e:
        logger.warning(f"Redis unavailable for scan_iter {pattern}: {e}")
        return []

async def safe_redis_ping(redis_client: Redis) -> bool:
    try:
        await redis_client.ping()
        return True
    except RedisError:
        return False

# Synonyms Loading and Normalization
def load_synonyms() -> None:
    global synonyms_dict, synonyms_last_modified
    
    try:
        if not os.path.exists(synonyms_file_path):
            logger.warning(f"Synonyms file not found: {synonyms_file_path}. Creating empty structure.")
            default_synonyms = {"ru": {}, "uz": {}}
            with open(synonyms_file_path, "w", encoding="utf-8") as f:
                json.dump(default_synonyms, f, ensure_ascii=False, indent=2)
            synonyms_dict = default_synonyms
            return
        
        current_modified = os.path.getmtime(synonyms_file_path)
        if current_modified == synonyms_last_modified and synonyms_dict:
            return
        
        with open(synonyms_file_path, "r", encoding="utf-8") as f:
            synonyms_dict = json.load(f)
        
        synonyms_last_modified = current_modified
        
        if not isinstance(synonyms_dict, dict):
            raise ValueError("Synonyms must be a dictionary")
        
        for lang in ["ru", "uz"]:
            if lang not in synonyms_dict:
                synonyms_dict[lang] = {}
                logger.warning(f"Language '{lang}' not found in synonyms, created empty section")
        
        logger.info(f"Synonyms loaded successfully. RU: {len(synonyms_dict.get('ru', {}))}, UZ: {len(synonyms_dict.get('uz', {}))}")
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in synonyms file: {e}")
        synonyms_dict = {"ru": {}, "uz": {}}
    except Exception as e:
        logger.error(f"Error loading synonyms: {e}")
        synonyms_dict = {"ru": {}, "uz": {}}

def normalize_text_with_synonyms(text: str, language: str) -> str:
    """Нормализует текст с использованием синонимов для определённого языка"""
    load_synonyms()
    
    if language not in synonyms_dict:
        logger.warning(f"Language '{language}' not found in synonyms")
        return text
    
    normalized_text = text.lower()
    replacements_made = []
    
    for standard_term, synonyms_list in synonyms_dict[language].items():
        if not isinstance(synonyms_list, list):
            logger.warning(f"Synonyms for '{standard_term}' must be a list, got: {type(synonyms_list)}")
            continue
            
        for synonym in synonyms_list:
            if isinstance(synonym, str) and synonym.lower() in normalized_text:
                pattern = r'\b' + re.escape(synonym.lower()) + r'\b'
                if re.search(pattern, normalized_text):
                    normalized_text = re.sub(pattern, standard_term.lower(), normalized_text)
                    replacements_made.append(f"'{synonym}' -> '{standard_term}'")
                    SYNONYMS_USAGE_COUNT.labels(language=language, standard_term=standard_term).inc()
    
    if replacements_made:
        logger.info(f"Text normalization for {language}: {'; '.join(replacements_made)}")
        logger.info(f"Original: {text}")
        logger.info(f"Normalized: {normalized_text}")
    
    return normalized_text

# Language Detection
_request_language = ContextVar('request_language', default=None)

@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT, max=MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
    before_sleep=lambda retry_state: logger.warning(
        f"Retrying OpenAI language detection call: attempt {retry_state.attempt_number} due to {retry_state.outcome.exception()}"
    ),
    after=lambda retry_state: logger.info(
        f"OpenAI language detection call {'succeeded' if retry_state.outcome.exception() is None else 'failed'} after {retry_state.attempt_number} attempts"
    )
)
async def detect_language_openai(text: str, chat_id: Optional[str] = None, redis_client: Redis = None) -> tuple[str, bool]:
    start_time = time.time()
    
    # Проверяем язык в контексте запроса
    cached_in_request = _request_language.get()
    if cached_in_request is not None:
        logger.info(f"Using request-cached language: '{cached_in_request}' for text: '{text}'")
        LANGUAGE_DETECTION_COUNT.labels(method="request_cache", detected_language=cached_in_request).inc()
        detection_time = time.time() - start_time
        LANGUAGE_DETECTION_LATENCY.labels(method="request_cache").observe(detection_time)
        return cached_in_request, False

    text_clean = text.strip()
    is_uncertain = (
        len(text_clean) < 3 or
        not any(c.isalpha() for c in text_clean) or
        re.match(r'^\d+$', text_clean)
    )
    
    logger.info(f"Language detection for text: '{text_clean}', is_uncertain: {is_uncertain}")
    
    # Простой fallback для неопределенных текстов - используем last_language БЕЗ рекурсии
    if is_uncertain and chat_id:
        last_language = await safe_redis_get(redis_client, f"chat:{chat_id}:last_language", default="uz")
        if last_language in ['ru', 'uz']:
            logger.info(f"Using last_language: '{last_language}' for uncertain text in chat {chat_id}")
            LANGUAGE_DETECTION_COUNT.labels(method="last_language", detected_language=last_language).inc()
            detection_time = time.time() - start_time
            LANGUAGE_DETECTION_LATENCY.labels(method="last_language").observe(detection_time)
            _request_language.set(last_language)
            return last_language, True
        
        # Если last_language отсутствует, используем дефолт без проверки истории
        logger.info("No last_language found, defaulting to 'uz' for uncertain text")
        LANGUAGE_DETECTION_COUNT.labels(method="default", detected_language="uz").inc()
        detection_time = time.time() - start_time
        LANGUAGE_DETECTION_LATENCY.labels(method="default").observe(detection_time)
        await safe_redis_set(redis_client, f"chat:{chat_id}:last_language", "uz", ex=3600)
        _request_language.set("uz")
        return "uz", True

    # Проверяем кэш Redis только для определённых текстов
    cache_key = f"language:{hashlib.md5(text.encode()).hexdigest()}"
    cached = await safe_redis_get(redis_client, cache_key)
    if cached:
        LANGUAGE_DETECTION_CACHE_HIT.inc()
        LANGUAGE_DETECTION_COUNT.labels(method="openai_cached", detected_language=cached).inc()
        logger.info(f"Using cached language detection: '{cached}' for text: '{text_clean}'")
        if chat_id:
            await safe_redis_set(redis_client, f"chat:{chat_id}:last_language", cached, ex=3600)
            logger.info(f"Saved last_language '{cached}' for chat {chat_id}")
        detection_time = time.time() - start_time
        LANGUAGE_DETECTION_LATENCY.labels(method="openai_cached").observe(detection_time)
        _request_language.set(cached)
        return cached, False

    # Определяем язык через OpenAI только для определенных текстов
    prompt = f"""Определи язык следующего текста. Верни только одно слово:
- "ru" если текст на русском языке
- "uz" если текст на узбекском языке

Если текст не является ни русским, ни узбекским, верни "uz".

Текст: "{text}"

Ответ:"""

    logger.info(f"Detecting language via OpenAI for: '{text_clean}'")
    
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=5
        )
        
        if not response.choices or not response.choices[0].message.content:
            logger.error("Invalid response from OpenAI for language detection")
            LANGUAGE_DETECTION_ERRORS.labels(method="openai", error_type="invalid_response").inc()
            if chat_id:
                last_language = await safe_redis_get(redis_client, f"chat:{chat_id}:last_language", "uz")
                logger.info(f"Invalid OpenAI response, using last_language: '{last_language}'")
                LANGUAGE_DETECTION_COUNT.labels(method="last_language", detected_language=last_language).inc()
                detection_time = time.time() - start_time
                LANGUAGE_DETECTION_LATENCY.labels(method="last_language").observe(detection_time)
                _request_language.set(last_language)
                return last_language, False
            _request_language.set("uz")
            return "uz", False
        
        detected_language = response.choices[0].message.content.strip().lower()
        
        if detected_language not in ['ru', 'uz']:
            logger.warning(f"Invalid language detected: '{detected_language}', defaulting to 'uz'")
            LANGUAGE_DETECTION_ERRORS.labels(method="openai", error_type="invalid_language").inc()
            detected_language = "uz"
        
        # Сохраняем результат в кэш только для определённых текстов
        if not is_uncertain:
            await safe_redis_set(redis_client, cache_key, detected_language, ex=3600)
            logger.info(f"Cached language '{detected_language}' for text: '{text_clean}'")
        if chat_id:
            await safe_redis_set(redis_client, f"chat:{chat_id}:last_language", detected_language, ex=3600)
            logger.info(f"Saved last_language '{detected_language}' for chat {chat_id}")
        
        detection_time = time.time() - start_time
        LANGUAGE_DETECTION_LATENCY.labels(method="openai").observe(detection_time)
        LANGUAGE_DETECTION_COUNT.labels(method="openai", detected_language=detected_language).inc()
        
        logger.info(f"Language detected via OpenAI: '{detected_language}' for text: '{text_clean}' (took {detection_time:.2f}s)")
        _request_language.set(detected_language)
        return detected_language, False
    
    except httpx.TimeoutException as e:
        logger.error(f"OpenAI language detection timed out: {e}")
        LANGUAGE_DETECTION_ERRORS.labels(method="openai", error_type="timeout").inc()
        if chat_id:
            last_language = await safe_redis_get(redis_client, f"chat:{chat_id}:last_language", "uz")
            logger.info(f"OpenAI timed out, using last_language: '{last_language}'")
            LANGUAGE_DETECTION_COUNT.labels(method="last_language", detected_language=last_language).inc()
            detection_time = time.time() - start_time
            LANGUAGE_DETECTION_LATENCY.labels(method="last_language").observe(detection_time)
            _request_language.set(last_language)
            return last_language, False
        _request_language.set("uz")
        return "uz", False
    except Exception as e:
        logger.error(f"OpenAI language detection failed: {e}")
        LANGUAGE_DETECTION_ERRORS.labels(method="openai", error_type=str(type(e).__name__)).inc()
        if chat_id:
            last_language = await safe_redis_get(redis_client, f"chat:{chat_id}:last_language", "uz")
            logger.info(f"OpenAI failed, using last_language: '{last_language}'")
            LANGUAGE_DETECTION_COUNT.labels(method="last_language", detected_language=last_language).inc()
            detection_time = time.time() - start_time
            LANGUAGE_DETECTION_LATENCY.labels(method="last_language").observe(detection_time)
            _request_language.set(last_language)
            return last_language, False
        _request_language.set("uz")
        return "uz", False

# Text Chunking Functions
def smart_sentence_split(text: str) -> List[str]:
    abbreviations = [
        'г', 'ул', 'д', 'кв', 'тел', 'факс', 'см', 'км', 'м', 'кг',
        'руб', 'коп', 'т', 'п', 'с', 'в', 'н', 'им', 'др', 'пр',
        'resp', "ya'ni", 'va', 'b', 'yil', 'kun', 'soat', 
        'tel', 'faks', 'email', 'www', 'http', 'https', 'uz'
    ]
    
    abbrev_pattern = '|'.join(re.escape(abbr) for abbr in abbreviations)
    
    sentences = []
    current_sentence = ""
    
    parts = re.split(r'(\. )', text)
    
    for i in range(0, len(parts), 2):
        part = parts[i]
        separator = parts[i + 1] if i + 1 < len(parts) else ""
        
        current_sentence += part
        
        if separator == ". ":
            words = current_sentence.strip().split()
            if words:
                last_word = words[-1].lower().rstrip('.')
                
                if not re.match(f'^({abbrev_pattern})$', last_word, re.IGNORECASE) and not re.match(r'^\d+$', last_word):
                    sentences.append(current_sentence.strip() + ".")
                    current_sentence = ""
                    continue
            
            current_sentence += separator
        
    if current_sentence.strip():
        sentences.append(current_sentence.strip())
    
    final_sentences = []
    for sentence in sentences:
        sub_sentences = re.split(r'([.!?]) ', sentence)
        current_sub = ""
        
        for j in range(0, len(sub_sentences), 2):
            part = sub_sentences[j]
            punct = sub_sentences[j + 1] if j + 1 < len(sub_sentences) else ""
            
            current_sub += part
            if punct in ['.', '!', '?']:
                final_sentences.append(current_sub + punct)
                current_sub = ""
            elif punct:
                current_sub += punct + " "
        
        if current_sub.strip():
            final_sentences.append(current_sub.strip())
    
    return [s.strip() for s in final_sentences if s.strip()]

def enhanced_text_chunking(text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
    sentences = smart_sentence_split(text)
    logger.info(f"Split into {len(sentences)} sentences")
    
    chunks = []
    current_chunk = ""
    
    for sentence in sentences:
        sentence = sentence.strip() + ('.' if not sentence.strip().endswith(('.', '!', '?')) else '') + ' '
        
        if len(current_chunk) + len(sentence) <= chunk_size:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    if len(chunks) > 1 and overlap > 0:
        overlapped_chunks = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                overlapped_chunks.append(chunk)
            else:
                prev_words = chunks[i-1].split()[-overlap//10:]
                if prev_words:
                    overlap_text = " ".join(prev_words)
                    overlapped_chunks.append(overlap_text + " " + chunk)
                else:
                    overlapped_chunks.append(chunk)
        return overlapped_chunks
    
    return chunks

# Knowledge Base Utilities
def get_file_hash(filepath: str) -> str:
    try:
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"Failed to compute hash for {filepath}: {e}")
        return ""

def save_faiss_index(index: faiss.Index, filename: str = "faiss_index.bin") -> None:
    try:
        faiss.write_index(index, filename)
        logger.info(f"FAISS index saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to save FAISS index: {e}")

def save_knowledge_base(chunks: List[str], filename: str = "knowledge_base.json") -> None:
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)
        logger.info(f"Knowledge base saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to save knowledge base: {e}")

def save_kb_hash(pdf_hash: str, filename: str = "kb_hash.json") -> None:
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump({"pdf_hash": pdf_hash}, f)
        logger.info(f"PDF hash saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to save PDF hash: {e}")

def load_faiss_index(filename: str = "faiss_index.bin") -> Optional[faiss.Index]:
    try:
        if os.path.exists(filename):
            index = faiss.read_index(filename)
            logger.info(f"FAISS index loaded from {filename}")
            return index
        return None
    except Exception as e:
        logger.error(f"Failed to load FAISS index: {e}")
        return None

def load_knowledge_base_chunks(filename: str = "knowledge_base.json") -> Optional[List[str]]:
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            logger.info(f"Knowledge base loaded from {filename}")
            return chunks
        return None
    except Exception as e:
        logger.error(f"Failed to load knowledge base: {e}")
        return None

def load_kb_hash(filename: str = "kb_hash.json") -> str:
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"PDF hash loaded from {filename}")
            return data.get("pdf_hash", "")
        return ""
    except Exception as e:
        logger.error(f"Failed to load PDF hash: {e}")
        return ""

async def load_knowledge_base() -> tuple[faiss.Index, List[str]]:
    pdf_path = "KB.pdf"
    index_filename = "faiss_index.bin"
    chunks_filename = "knowledge_base.json"
    hash_filename = "kb_hash.json"

    if not os.path.exists(pdf_path):
        logger.error(f"PDF not found: {pdf_path}")
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    current_pdf_hash = get_file_hash(pdf_path)
    if not current_pdf_hash:
        logger.error("Failed to compute PDF hash, rebuilding cache")
    else:
        cached_index = load_faiss_index(index_filename)
        cached_chunks = load_knowledge_base_chunks(chunks_filename)
        cached_pdf_hash = load_kb_hash(hash_filename)

        if cached_index is not None and cached_chunks is not None and cached_pdf_hash == current_pdf_hash:
            logger.info("Using cached FAISS index and knowledge base (PDF unchanged)")
            return cached_index, cached_chunks
        else:
            logger.info("PDF has changed or cache is invalid, rebuilding cache")

    logger.info(f"Loading knowledge base from {pdf_path}")
    
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"
    
    chunks = enhanced_text_chunking(full_text, chunk_size=800, overlap=100)
    logger.info(f"Created {len(chunks)} chunks with enhanced chunking")
    
    dimension = 1536
    index = faiss.IndexFlatL2(dimension)
    embeddings = await asyncio.gather(*(get_embedding_with_semaphore(chunk, redis_client) for chunk in chunks))
    
    valid_embeddings = [emb for emb in embeddings if emb]
    if len(valid_embeddings) != len(chunks):
        logger.warning(f"Some embeddings failed: {len(valid_embeddings)}/{len(chunks)}")
        chunks = [chunks[i] for i, emb in enumerate(embeddings) if emb]
        embeddings = valid_embeddings
    
    if not embeddings:
        logger.error("Failed to generate embeddings for some chunks")
        raise RuntimeError("Failed to generate embeddings for knowledge base")
    
    index.add(np.array(embeddings).astype("float32"))
    logger.info("FAISS index built")

    save_faiss_index(index, index_filename)
    save_knowledge_base(chunks, chunks_filename)
    if current_pdf_hash:
        save_kb_hash(current_pdf_hash, hash_filename)

    return index, chunks

async def get_embedding_with_semaphore(chunk: str, redis_client: Redis) -> Optional[List[float]]:
    async with semaphore:
        try:
            return await get_embedding(chunk, redis_client)
        except Exception as e:
            logger.error(f"Failed to get embedding for chunk: {chunk[:50]}...: {e}")
            return None
    
# Embedding Retrieval
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT, max=MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
    before_sleep=lambda retry_state: logger.warning(
        f"Retrying OpenAI embedding call: attempt {retry_state.attempt_number} due to {retry_state.outcome.exception()}"
    ),
    after=lambda retry_state: logger.info(
        f"OpenAI embedding call {'succeeded' if retry_state.outcome.exception() is None else 'failed'} after {retry_state.attempt_number} attempts"
    )
)
async def get_embedding(text: str, redis_client: Redis) -> List[float]:
    cached = await safe_redis_get(redis_client, f"embedding:{text}")
    if cached:
        logger.info(f"Using cached embedding for: {text[:50]}...")
        return json.loads(cached)

    logger.info(f"Fetching new embedding for: {text[:50]}...")
    try:
        response = await client.embeddings.create(model="text-embedding-ada-002", input=text)
        if not response.data or not response.data[0].embedding:
            logger.error("Invalid response from OpenAI: no embedding data")
            raise APIError("No embedding data in OpenAI response")
        embedding = response.data[0].embedding
        await safe_redis_set(redis_client, f"embedding:{text}", json.dumps(embedding), ex=3600)
        return embedding
    except httpx.TimeoutException as e:
        logger.error(f"OpenAI embedding request timed out: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to fetch embedding: {e}")
        raise

# Message Anonymization
def anonymize_message(message: str) -> str:
    logger.info("Anonymizing message")
    anonymized_text = message

    for pattern in PII_PATTERNS:
        matches = re.findall(pattern["regex"], anonymized_text)
        if matches:
            logger.info(f"Found {pattern['entity']}: {matches}")
            anonymized_text = re.sub(pattern["regex"], pattern["replacement"], anonymized_text)
    
    if anonymized_text != message:
        logger.info(f"Anonymized message: {anonymized_text[:50]}...")
    else:
        logger.info("No PII found in message")
    
    return anonymized_text

# Query Classification
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT, max=MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
    before_sleep=lambda retry_state: logger.warning(
        f"Retrying OpenAI classification call: attempt {retry_state.attempt_number} due to {retry_state.outcome.exception()}"
    ),
    after=lambda retry_state: logger.info(
        f"OpenAI classification call {'succeeded' if retry_state.outcome.exception() is None else 'failed'} after {retry_state.attempt_number} attempts"
    )
)
async def classify_query(query: str, language: str, redis_client: Redis) -> Dict[str, str]:
    """Классифицирует запрос с заранее определённым языком"""
    cached = await safe_redis_get(redis_client, f"classification:{query}")
    if cached:
        logger.info(f"Using cached classification: {query}")
        return json.loads(cached)

    normalized_query = normalize_text_with_synonyms(query, language)
    
    query_lower = normalized_query.lower()
    logger.info(f"Classifying query: {query}, normalized: {normalized_query}, language: {language}")

    for category, subcategories in CATEGORIES.items():
        for subcategory, keywords in subcategories.items():
            if any(keyword in query_lower for keyword in keywords):
                result = {
                    "theme": category.split(" / ")[0] if language == 'ru' else category.split(" / ")[1],
                    "category": subcategory.split(" / ")[0] if language == 'ru' else subcategory.split(" / ")[1],
                    "subcategory": keywords[0]
                }
                logger.info(f"Local classification matched: {result}")
                await safe_redis_set(redis_client, f"classification:{query}", json.dumps(result), ex=3600)
                CLASSIFICATIONS_COUNT.labels(
                    theme=result["theme"],
                    category=result["category"],
                    subcategory=result["subcategory"]
                ).inc()
                return result

    logger.info("No local match found, falling back to OpenAI")
    prompt = (
        f"Определи тематику, категорию и подкатегорию запроса пользователя на основе следующего текста:\n"
        f"Текст запроса: {normalized_query}\n"
        f"Язык запроса: {'русский' if language == 'ru' else 'узбекский'}\n"
        f"Доступные категории:\n"
        f"{json.dumps(CATEGORIES, ensure_ascii=False, indent=2)}\n"
        f"Выбери категорию и подкатегорию строго из списка выше. Если запрос не соответствует ни одной категории, верни 'Неизвестно' для всех полей.\n"
        f"Верни результат строго в формате JSON: {{\"theme\": \"тема\", \"category\": \"категория\", \"subcategory\": \"подкатегория\"}}. "
        f"Используй только двойные кавычки для ключей и значений. Не добавляй никакого дополнительного текста."
    )
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.5,
            max_tokens=200
        )
        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Raw response from OpenAI: '{raw_response}'")
        result = json.loads(raw_response)
        if not all(k in result for k in ["theme", "category", "subcategory"]):
            raise ValueError("Неверный формат ответа от OpenAI")
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse OpenAI response as JSON: {e}. Raw response: '{raw_response}'")
        result = {
            "theme": "Неизвестно" if language == 'ru' else "Noma'lum",
            "category": "Неизвестно" if language == 'ru' else "Noma'lum",
            "subcategory": "Неизвестно" if language == 'ru' else "Noma'lum"
        }
    except httpx.TimeoutException as e:
        logger.error(f"OpenAI classification request timed out: {e}")
        result = {
            "theme": "Неизвестно" if language == 'ru' else "Noma'lum",
            "category": "Неизвестно" if language == 'ru' else "Noma'lum",
            "subcategory": "Неизвестно" if language == 'ru' else "Noma'lum"
        }
    CLASSIFICATIONS_COUNT.labels(
        theme=result["theme"],
        category=result["category"],
        subcategory=result["subcategory"]
    ).inc()
    await safe_redis_set(redis_client, f"classification:{query}", json.dumps(result), ex=3600)
    logger.info(f"Classification result: {result}")
    return result

# Context Finding
async def find_relevant_context(query: str, query_embedding: List[float], index: faiss.Index, knowledge_base: List[str], language: str, top_k: int = 3) -> List[str]:
    """Находит релевантный контекст с заранее определённым языком и переданным эмбеддингом"""
    normalized_query = normalize_text_with_synonyms(query, language)
    
    logger.info(f"Normalized query for context search: {normalized_query}, language: {language}")
    
    if not query_embedding:
        logger.warning("No embedding for query, returning empty context")
        return []
    distances, indices = index.search(np.array([query_embedding]).astype("float32"), top_k)
    relevant_chunks = [
        knowledge_base[idx] for i, idx in enumerate(indices[0]) if distances[0][i] < 0.9
    ]
    logger.info(f"Found {len(relevant_chunks)} relevant chunks with distances: {distances[0][:top_k]}")
    return relevant_chunks

# Interaction Logging
async def log_interaction(chat_id: str, message: str, response: str, classification: Dict[str, str], tokens: int, escalation: bool) -> None:
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO interactions (chat_id, message, response, classification, tokens, escalation, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                chat_id, message, response, json.dumps(classification), tokens, escalation, datetime.now()
            )
    except Exception as e:
        logger.error(f"Error logging interaction: {e}")

# Inactive Sessions Cleanup
async def cleanup_inactive_sessions(redis_client: Redis) -> None:
    while True:
        try:
            keys = await safe_redis_scan_iter(redis_client, "chat:*:start_time")
            for key in keys:
                chat_id = key.split(":")[1]
                start_time = float(await safe_redis_get(redis_client, key) or "0")
                if time.time() - start_time > 3600:
                    duration = time.time() - start_time
                    SESSION_DURATION.observe(duration)
                    SESSIONS_TOTAL.inc()
                    await safe_redis_delete(redis_client, key, f"chat:{chat_id}:active", f"chat:{chat_id}:escalation_count", f"chat:{chat_id}:last_language")
                    logger.info(f"Session for chat {chat_id} timed out")
            active_chats = len(await safe_redis_scan_iter(redis_client, "chat:*:active"))
            ACTIVE_CHATS.set(active_chats)
        except Exception as e:
            logger.error(f"Error in session cleanup: {e}")
        await asyncio.sleep(60)

# Application Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    global vector_store, knowledge_base, db_pool, redis_client
    
    logger.info("Starting application lifespan")
    
    try:
        load_synonyms()
        logger.info("Synonyms loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load synonyms: {e}")
        synonyms_dict.clear()

    try:
        db_pool = await asyncpg.create_pool(
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "123"),
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            min_size=1,
            max_size=20
        )
        logger.info("Database pool created")
        async with db_pool.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id SERIAL PRIMARY KEY,
                    chat_id VARCHAR(255),
                    message TEXT,
                    response TEXT,
                    classification JSONB,
                    tokens INT,
                    escalation BOOLEAN,
                    timestamp TIMESTAMP
                )
            """)
        logger.info("Interactions table initialized")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        db_pool = None

    try:
        redis_client = await Redis.from_url(
            f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', 6379)}/{os.getenv('REDIS_DB', 0)}",
            decode_responses=True
        )
        await redis_client.ping()
        logger.info("Successfully connected to async Redis")
    except RedisError as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise SystemExit("Redis connection failed. Please ensure Redis is running in Docker.")

    try:
        vector_store, knowledge_base = await load_knowledge_base()
        logger.info("Knowledge base loaded successfully")
    except FileNotFoundError as e:
        logger.error(f"Knowledge base file not found: {e}")
        vector_store, knowledge_base = None, []
    except Exception as e:
        logger.error(f"Error loading knowledge base: {e}")
        vector_store, knowledge_base = None, []

    try:
        start_http_server(int(os.getenv("PROMETHEUS_PORT", 8001)))
        logger.info("Prometheus metrics server started")
    except Exception as e:
        logger.error(f"Failed to start Prometheus server: {e}")

    try:
        asyncio.create_task(cleanup_inactive_sessions(redis_client))
        logger.info("Session cleanup task started")
    except Exception as e:
        logger.error(f"Failed to start cleanup task: {e}")

    yield

    try:
        if db_pool:
            await db_pool.close()
            logger.info("Database pool closed")
    except Exception as e:
        logger.error(f"Error closing database pool: {e}")

    try:
        await redis_client.aclose()
        logger.info("Redis client closed")
    except Exception as e:
        logger.error(f"Error closing Redis client: {e}")

app = FastAPI(lifespan=lifespan)

# Response Generation
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=MIN_WAIT, max=MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
    before_sleep=lambda retry_state: logger.warning(
        f"Retrying OpenAI completion call: attempt {retry_state.attempt_number} due to {retry_state.outcome.exception()}"
    ),
    after=lambda retry_state: logger.info(
        f"OpenAI completion call {'succeeded' if retry_state.outcome.exception() is None else 'failed'} after {retry_state.attempt_number} attempts"
    )
)
async def generate_response(context: List[str], query: str, history: List[str], language: str) -> tuple[str, int]:
    logger.info(f"OpenAI language detection result: {language}")
    
    # if not context:
        # logger.warning("No relevant context found, returning default response")
        # no_info_msg = (
            # "Ma'lumot bilimlar bazasida topilmadi. Savolni boshqacha shaklda yozing yoki operatorga murojaat qiling."
            # if language == 'uz' else
            # "Информация не найдена в базе знаний. Попробуйте переформулировать вопрос или обратитесь к оператору."
        # )
        # return no_info_msg, 0

    # Define language-specific instructions
    uz_instruction = (
        "MUHIM: FAQAT O'ZBEK TILIDA VA FAQAT LOTIN YOZUVIDA javob bering!\n"
        "To'g'ri misol: \"To'lov qilish uchun...\"\n"
        "Noto'g'ri misol: \"Тўлов қилиш учун...\" (kirillitsa ishlatmang!)\n"
        "Noto'g'ri misol: \"Оплата через...\" (rus tilida yozmang!)\n"
        "Faqat lotin harflari: a, b, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, x, y, z\n"
        "O'zbek maxsus belgilar: o', g', sh, ch, ng"
    )
    ru_instruction = "Отвечай на русском языке, используя кириллицу."

    language_instruction = uz_instruction if language == 'uz' else ru_instruction

    # Prepare history context
    history_context = f"Предыдущие сообщения: {' | '.join(history[-2:])}" if history and len(history) > 1 else ""

    # Build prompt for Uzbek
    if language == 'uz':
        prompt = (
            "Siz Paynet texnik yordam xizmati chat-botisiz. Quyidagi bilimlar bazasi ma'lumotlari asosida javob bering.\n"
            "JUDA MUHIM: Javobingizni FAQAT O'ZBEK TILIDA va FAQAT LOTIN YOZUVIDA yozing!\n"
            "QOIDALAR:\n"
            "1. Agar bu avvalgi savolga aniqlik kiritilsa - \n dialog tarixi asosida javob bering"
            "2. Agar bu yangi savol bo'lsa, uchun bilimlar bazasiga javob bering\n"
            "3. Ma'lumotni faqat quyidagi kontekstdan foydalaning, boshqa har qanday ma'lumotga mos kelmaydi\n"
            "4. Agar ma'lumot etarli bo'lmasa yoki yo'q bo'lsa, javob ma'lumotlar bazasida taqdim etilmaganligini yozmang, savolni aniqlashtirishni yoki \ n operatoriga murojaat qilishni taklif qiling"
            "5. Muloyim va aniq bo'ling\n"
            "6. Amaliy ko'rsatmalar bering\n"
            "7. Javob bilimlar bazasida taqdim etilmaganligini yozmang\n"
            f"Bilimlar bazasi:{context}\n"
            f"{history_context  }\n"
            f"Foydalanuvchi savoli: {query}\n"
            "O'zbek tilida lotin yozuvida javob:"
        )
    else:
        # Build prompt for Russian
        prompt = (
            "Ты — чат-бот службы поддержки Paynet. Твоя задача помочь пользователю с услугами компании.\n"
            f"{language_instruction}\n"
            "ПРАВИЛА:\n"
            "1. Если это уточнение предыдущего вопроса - отвечайте на основе истории диалога\n"
            "2. Если это новый вопрос, для отвечай по базе знаний\n"
            "3. Используй информацию ТОЛЬКО из контекста ниже, любая другая информация не годится\n"
            "4. Если информации недостаточно или отсутствует,предложи уточнить вопрос или обратиться к оператору\n"
            "5. Будь вежливым и конкретным\n"
            "6. Давай практичные инструкции\n"
            "7. Если контекст из базы знаний пустой, не сообщай, что информация не найдена в базе знаний\n"
            "8. Если контекст пуст или не относится к вопросу, НЕ ГОВОРИ, что не можешь помочь или не нашел информацию.\n"
            f"Контекст из базы знаний:{context}\n"
            f"{history_context}\n"
            f"Запрос пользователя: {query}\n"
            "Ответ на русском языке:"
        )
    
    logger.info(f"Using {'Uzbek (Latin)' if language == 'uz' else 'Russian (Cyrillic)'} prompt")
    
    try:
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=600,
            temperature=0.6
        )
        
        if not response.choices or not response.choices[0].message.content:
            logger.error("Invalid response from OpenAI")
            raise APIError("No completion data in OpenAI response")
        
        ai_response = response.choices[0].message.content.strip()
        
        if language == 'uz':
            russian_words = ['через', 'можно', 'нужно', 'должен', 'приложение', 'оплата', 'платеж', 'карта', 'банк']
            russian_count = sum(1 for word in russian_words if word in ai_response.lower())
            
            if russian_count > 2:
                logger.warning(f"Uzbek response contains {russian_count} Russian words: {ai_response[:100]}...")
            
            cyrillic_replacements = {
                'ў': "o'", 'қ': 'q', 'ғ': "g'", 'ҳ': 'h',
                'Ў': "O'", 'Қ': 'Q', 'Ғ': "G'", 'Ҳ': 'H'
            }
            for cyr, lat in cyrillic_replacements.items():
                ai_response = ai_response.replace(cyr, lat)
        
        logger.info(f"Generated response for {language}: {ai_response[:100]}...")
        
        return ai_response, response.usage.total_tokens
    except httpx.TimeoutException as e:
        logger.error(f"OpenAI completion request timed out: {e}")
        raise
    except Exception as e:
        logger.error(f"OpenAI completion failed: {e}")
        raise

# Message Processing Endpoint
@app.post("/process_message", response_model=MessageResponse)
async def process_message(request: MessageRequest):
    REQUEST_COUNT.labels(endpoint="/process_message").inc()
    with REQUEST_LATENCY.labels(endpoint="/process_message").time():
        chat_id = request.chat_id
        message = request.message
        logger.info(f"Processing message: chat_id={chat_id}, message={message}")

        if not chat_id or not message:
            logger.error("Missing chat_id or message")
            raise HTTPException(status_code=400, detail="Missing chat_id or message")

        # Определяем язык и статус uncertain
        language, is_uncertain = await detect_language_openai(message, chat_id, redis_client)
        logger.info(f"Detected language: {language}, is_uncertain: {is_uncertain}")

        anonymized_message = anonymize_message(message)
        logger.info(f"Anonymized message for processing: {anonymized_message}")

        is_pii_only = not anonymized_message.strip() or anonymized_message.strip() in ["***", ""]
        if is_pii_only:
            logger.warning("Message is entirely PII or analysis failed")
            pii_response = "Xabar faqat shaxsiy ma'lumotlarni o'z ichiga oladi. Iltimos, shaxsiy ma'lumotlarsiz savol bering." if language == 'uz' else "Сообщение содержит только персональные данные. Пожалуйста, задайте вопрос без личных данных."
            
            return MessageResponse(
                status="success",
                chat_id=chat_id,
                response=pii_response,
                classification={"theme": "Ошибка", "category": "PII", "subcategory": "Анонимизация не удалась"},
                escalation=False,
                history=[]
            )

        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.get(f"chat:{chat_id}:seen")
            pipe.set(f"chat:{chat_id}:seen", "1")
            pipe.set(f"chat:{chat_id}:active", "1", ex=3600)
            pipe.get(f"chat:{chat_id}:start_time")
            pipe.lpush(f"chat:{chat_id}:history", anonymized_message)
            pipe.ltrim(f"chat:{chat_id}:history", 0, MAX_HISTORY_SIZE - 1)
            pipe.lrange(f"chat:{chat_id}:history", 0, -1)
            pipe.get(f"chat:{chat_id}:embedding")
            pipe.get(f"chat:{chat_id}:count")
            pipe.get(f"chat:{chat_id}:escalation_count")
            
            results = await pipe.execute()
        
        # Распаковка results
        seen = results[0]
        # results[1] - set seen
        # results[2] - set active
        start_time_str = results[3]
        # results[4] - lpush
        # results[5] - ltrim
        history = results[6] or [anonymized_message]
        last_embedding_str = results[7] or "[]"
        count_str = results[8] or "0"
        escalation_count_str = results[9] or "0"
        
        if seen is None:
            UNIQUE_CHATS_TOTAL.inc()
        if start_time_str is None:
            await safe_redis_set(redis_client, f"chat:{chat_id}:start_time", str(time.time()))
        logger.info(f"Chat history for {chat_id}: {history}")

        # Fetch embedding once and reuse
        embedding = await get_embedding(anonymized_message, redis_client)
        last_embedding = json.loads(last_embedding_str)
        count = int(count_str)
        if last_embedding and cosine_similarity([embedding], [last_embedding])[0][0] > SIMILARITY_THRESHOLD:
            count = await safe_redis_incr(redis_client, f"chat:{chat_id}:count")
            logger.info(f"Similar message detected, count increased to {count}")
        else:
            await safe_redis_set(redis_client, f"chat:{chat_id}:count", "1")
            count = 1
            logger.info(f"New message, count reset to {count}")
        await safe_redis_set(redis_client, f"chat:{chat_id}:embedding", json.dumps(embedding))

        classification = await classify_query(anonymized_message, language, redis_client)

        escalation_count = int(escalation_count_str)

        if any(keyword in anonymized_message.lower() for keyword in OPERATOR_KEYWORDS) or count >= 3:
            escalation_count = await safe_redis_incr(redis_client, f"chat:{chat_id}:escalation_count")

            if escalation_count >= 3:
                HIGH_LOAD_ESCALATIONS_TOTAL.inc()
                high_load_response = "Kechirasiz, hozirda yuklama yuqori. Iltimos, +998712020707 raqamiga qo'ng'iroq qiling." if language == 'uz' else "Извините, сейчас высокая нагрузка. Пожалуйста, позвоните в колл-центр по номеру +998712020707."
                
                logger.info(f"High load detected for chat {chat_id} (escalation count: {escalation_count})")
                await log_interaction(chat_id, anonymized_message, high_load_response, {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Высокая нагрузка"}, 0, False)
                return MessageResponse(
                    status="high_load",
                    chat_id=chat_id,
                    response=high_load_response,
                    classification={"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Высокая нагрузка"},
                    escalation=False,
                    history=history
                )

            ESCALATION_COUNT.inc()
            escalation_response = "Operatorga o'tkazamiz. Biroz kuting, iltimos." if language == 'uz' else "Передаём оператору. Пожалуйста, подождите."
            
            await log_interaction(chat_id, anonymized_message, escalation_response, {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Передача оператору"}, 0, True)
            logger.info(f"Escalating chat {chat_id} (escalation count: {escalation_count})")
            start_time_str = await safe_redis_get(redis_client, f"chat:{chat_id}:start_time")
            if start_time_str:
                duration = time.time() - float(start_time_str)
                SESSION_DURATION.observe(duration)
                SESSIONS_TOTAL.inc()
                await safe_redis_delete(redis_client, f"chat:{chat_id}:start_time", f"chat:{chat_id}:active", f"chat:{chat_id}:escalation_count")
            active_chats = len(await safe_redis_scan_iter(redis_client, "chat:*:active"))
            ACTIVE_CHATS.set(active_chats)
            return MessageResponse(
                status="escalation",
                chat_id=chat_id,
                response=escalation_response,
                classification={"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Передача оператору"},
                escalation=True,
                history=history
            )

        # Пропускаем поиск контекста для uncertain текстов
        if is_uncertain:
            logger.info(f"Uncertain text detected, skipping context search for chat {chat_id}")
            UNCERTAIN_REQUESTS.inc()
            context = []
        else:
            context = await find_relevant_context(anonymized_message, embedding, vector_store, knowledge_base, language)
        
        ai_response, tokens = await generate_response(context, anonymized_message, history, language=language)
        await log_interaction(chat_id, anonymized_message, ai_response, classification, tokens, False)
        logger.info(f"Response generated: {ai_response[:50]}...")
        return MessageResponse(
            status="success",
            chat_id=chat_id,
            response=ai_response,
            classification=classification,
            escalation=False,
            history=history
        )

# Escalate Endpoint
@app.post("/escalate", response_model=EscalateResponse)
async def escalate(request: EscalateRequest):
    REQUEST_COUNT.labels(endpoint="/escalate").inc()
    with REQUEST_LATENCY.labels(endpoint="/escalate").time():
        chat_id = request.chat_id
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.lrange(f"chat:{chat_id}:history", 0, -1)
            pipe.get(f"chat:{chat_id}:last_language")
            pipe.incr(f"chat:{chat_id}:escalation_count")
            results = await pipe.execute()
        
        history = results[0] or []
        last_language = results[1] or 'uz'
        escalation_count = int(results[2])

        if escalation_count >= 3:
            HIGH_LOAD_ESCALATIONS_TOTAL.inc()
            high_load_response = "Kechirasiz, hozirda yuklama yuqori. Iltimos, +998712020707 raqamiga qo'ng'iroq qiling." if last_language == 'uz' else "Извините, сейчас высокая нагрузка. Пожалуйста, позвоните в колл-центр по номеру +998712020707."
            
            logger.info(f"High load detected for chat {chat_id} (escalation count: {escalation_count})")
            await log_interaction(chat_id, "Manual escalation", high_load_response, {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Высокая нагрузка"}, 0, False)
            return EscalateResponse(
                status="high_load",
                chat_id=chat_id,
                response=high_load_response,
                history=history
            )

        ESCALATION_COUNT.inc()
        escalation_response = "Chat operatorga o'tkazildi. Tez orada siz bilan bog'lanishadi." if last_language == 'uz' else "Чат передан оператору. Скоро с вами свяжутся."
        
        await log_interaction(chat_id, "Manual escalation", escalation_response, {"theme": "Запрос оператора", "category": "Эскалация", "subcategory": "Передача оператору"}, 0, True)
        logger.info(f"Manual escalation for {chat_id} (escalation count: {escalation_count})")
        seen = await safe_redis_get(redis_client, f"chat:{chat_id}:seen")
        if seen is None:
            await safe_redis_set(redis_client, f"chat:{chat_id}:seen", "1")
            UNIQUE_CHATS_TOTAL.inc()
        start_time_str = await safe_redis_get(redis_client, f"chat:{chat_id}:start_time")
        if start_time_str:
            duration = time.time() - float(start_time_str)
            SESSION_DURATION.observe(duration)
            SESSIONS_TOTAL.inc()
            await safe_redis_delete(redis_client, f"chat:{chat_id}:start_time", f"chat:{chat_id}:active", f"chat:{chat_id}:escalation_count")
        active_chats = len(await safe_redis_scan_iter(redis_client, "chat:*:active"))
        ACTIVE_CHATS.set(active_chats)
        return EscalateResponse(
            status="success",
            chat_id=chat_id,
            response=escalation_response,
            history=history
        )

# Health Check Endpoint
@app.get("/health")
async def health_check():
    redis_status = "healthy" if await safe_redis_ping(redis_client) else "unhealthy"
    
    try:
        async with db_pool.acquire() as connection:
            await connection.execute("SELECT 1")
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
    
    kb_status = "healthy" if vector_store is not None else "unhealthy"
    
    synonyms_status = "healthy" if synonyms_dict and ("ru" in synonyms_dict or "uz" in synonyms_dict) else "unhealthy"
    
    return {
        "status": "healthy" if all(status == "healthy" for status in [redis_status, db_status, kb_status, synonyms_status]) else "unhealthy",
        "components": {
            "redis": redis_status,
            "database": db_status,
            "knowledge_base": kb_status,
            "synonyms": synonyms_status
        },
        "language_detection": "openai_api_with_history",
        "supported_languages": ["Russian", "Uzbek"],
        "synonyms_loaded": {
            "ru": len(synonyms_dict.get("ru", {})),
            "uz": len(synonyms_dict.get("uz", {}))
        }
    }

# Stats Endpoint
@app.get("/stats")
async def get_stats():
    active_chats = len(await safe_redis_scan_iter(redis_client, "chat:*:active"))
    total_seen_chats = len(await safe_redis_scan_iter(redis_client, "chat:*:seen"))
    escalation_counts = {}
    keys = await safe_redis_scan_iter(redis_client, "chat:*:escalation_count")
    for key in keys:
        chat_id = key.split(":")[1]
        count = int(await safe_redis_get(redis_client, key, "0"))
        escalation_counts[chat_id] = count
    
    return {
        "active_chats": active_chats,
        "total_seen_chats": total_seen_chats,
        "escalation_counts": escalation_counts,
        "knowledge_base_chunks": len(knowledge_base) if knowledge_base else 0,
        "language_detection_method": "openai_api_with_history",
        "supported_languages": ["ru", "uz"],
        "bilingual_support": True,
        "synonyms": {
            "ru_count": len(synonyms_dict.get("ru", {})),
            "uz_count": len(synonyms_dict.get("uz", {})),
            "last_updated": datetime.fromtimestamp(synonyms_last_modified).isoformat() if synonyms_last_modified else None
        }
    }

# Synonyms Reload Endpoint
@app.get("/synonyms/reload")
async def reload_synonyms():
    global synonyms_last_modified
    synonyms_last_modified = 0
    load_synonyms()
    
    return {
        "status": "success",
        "message": "Synonyms reloaded",
        "synonyms": {
            "ru_count": len(synonyms_dict.get("ru", {})),
            "uz_count": len(synonyms_dict.get("uz", {}))
        }
    }

# Synonyms Status Endpoint
@app.get("/synonyms/status")
async def synonyms_status():
    return {
        "file_exists": os.path.exists(synonyms_file_path),
        "file_path": synonyms_file_path,
        "last_modified": datetime.fromtimestamp(synonyms_last_modified).isoformat() if synonyms_last_modified else None,
        "ru_terms": len(synonyms_dict.get("ru", {})),
        "uz_terms": len(synonyms_dict.get("uz", {})),
        "sample_ru": dict(list(synonyms_dict.get("ru", {}).items())[:3]) if synonyms_dict.get("ru") else {},
        "sample_uz": dict(list(synonyms_dict.get("uz", {}).items())[:3]) if synonyms_dict.get("uz") else {}
    }

# Language Detection Stats Endpoint
@app.get("/language_detection_stats")
async def language_detection_stats():
    cache_keys = await safe_redis_scan_iter(redis_client, "language:*")
    cache_stats = {"total_cached": len(cache_keys)}
    
    cached_languages = {"ru": 0, "uz": 0}
    for key in cache_keys[:100]:
        lang = await safe_redis_get(redis_client, key)
        if lang in cached_languages:
            cached_languages[lang] += 1
    
    cache_stats.update(cached_languages)
    
    return {
        "cache_statistics": cache_stats,
        "detection_methods": {
            "primary": "openai_api (gpt-4o-mini)",
            "history": "last message language from Redis",
            "fallback": "default to uz",
            "default": "uz"
        },
        "supported_languages": ["ru", "uz"],
        "cache_ttl_seconds": 3600
    }

# Clear Language Cache Endpoint
@app.post("/clear_language_cache")
async def clear_language_cache():
    cache_keys = await safe_redis_scan_iter(redis_client, "language:*")
    if cache_keys:
        await safe_redis_delete(redis_client, *cache_keys)
        cleared_count = len(cache_keys)
    else:
        cleared_count = 0
        
    return {
        "status": "success",
        "cleared_entries": cleared_count,
        "message": f"Cleared {cleared_count} language cache entries"
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting bilingual bot server with OpenAI language detection...")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("APP_PORT", 8000)), log_level="info")