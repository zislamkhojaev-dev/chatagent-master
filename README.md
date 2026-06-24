# Paynet RAG Chatbot

Чат-бот колл-центра Paynet на FastAPI: ответы на запросы на **русском** и **узбекском** (латиница) по базе знаний с RAG, детекцией языка, классификацией, анонимизацией PII и эскалацией на оператора.

**Репозиторий:** https://github.com/Zafar1997/OutRAGeiousChat

---

## Архитектура

- **API:** FastAPI (модульное приложение в `app/`), точка входа `uvicorn app.main:app`.
- **RAG:** гибридный поиск — **Qdrant** (векторный) + **BM25** (текстовый), объединение по RRF. Семантический чанкинг PDF, эмбеддинги OpenAI.
- **Хранение:** PostgreSQL (логи взаимодействий), **Redis** (только ключи: сессии, кэш эмбеддингов/языка/классификации, контекст чата), Qdrant (векторный индекс чанков).
- **Контекст чата:** сохранённое краткое изложение диалога в Redis, асинхронное обновление после каждого ответа (LLM).
- **Админка:** веб-страница `/admin/kb` — загрузка PDF в `kb/KB.pdf` и переиндексация БЗ (Basic Auth).

Единственный источник базы знаний — каталог **`kb/`** (файл `kb/KB.pdf`). Индекс строится через админку (загрузка PDF → «Проиндексировать»); при старте приложение подхватывает уже созданный индекс по alias Qdrant и чанки из `kb/knowledge_base.json`.

Шаблон переделки БЗ под метаданные поиска: [`docs/KB_RESTRUCTURE_TEMPLATE.md`](docs/KB_RESTRUCTURE_TEMPLATE.md).

Полный пайплайн агента (уточнения, RAG, эскалация, параметры): [`docs/AGENT_PIPELINE.md`](docs/AGENT_PIPELINE.md).

---

## Возможности

- Обработка сообщений: детекция языка (ru/uz), нормализация синонимов, классификация, анонимизация PII.
- RAG по базе знаний: гибридный поиск (Qdrant + BM25), генерация ответа с учётом контекста чата.
- Опциональный параметр **`language`** в API: явная установка языка ответа (`"uz"` или `"ru"`) без детекции.
- Эскалация: автоматическая и ручная (`POST /escalate`).
- Мониторинг: Prometheus (порт 8001), эндпоинты `/health`, `/stats`.
- Управление БЗ: страница `/admin/kb` (загрузка PDF, запуск переиндексации), защита Basic Auth.

---

## Требования

- **Docker** и **Docker Compose** (для запуска всего стека в контейнерах)
- **OpenAI API ключ**
- Для локального запуска без Docker приложения: Python 3.11+

---

## Запуск в Docker

Всё приложение и сервисы (API, Redis, PostgreSQL, Qdrant, Prometheus, Grafana) запускаются в Docker. База знаний хранится в volume `kb_data` (каталог `kb/` внутри контейнера приложения).

### Шаг 1. Подготовка `.env`

В корне проекта создайте файл `.env`:

```env
OPENAI_API_KEY=sk-your-openai-api-key
DB_HOST=postgres
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=123
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
PROMETHEUS_PORT=8001
APP_PORT=8000

# Админка БЗ — обязательно задайте пароль
ADMIN_USER=admin
ADMIN_PASSWORD=your-secure-password
KB_MAX_FILE_SIZE_MB=20

# Qdrant (имена сервисов из docker-compose)
QDRANT_HOST=qdrant
QDRANT_PORT=6333
```

Без `OPENAI_API_KEY` и `ADMIN_PASSWORD` приложение не запустится или админка будет недоступна.

### Шаг 2. Сборка и запуск контейнеров

Из корня проекта выполните:

```bash
docker compose up -d --build
```

Будут запущены:

- **app** — FastAPI-приложение (порты 8000, 8001)
- **qdrant** — векторная БД (порт 6333)
- **postgres** — PostgreSQL (порт 5432)
- **redis** — Redis, лимит 512 МБ (порт 6379)
- **prometheus** — сбор метрик (порт 9090)
- **grafana** — дашборды (порт 3000)

Проверить статус контейнеров:

```bash
docker compose ps
```

Логи приложения:

```bash
docker compose logs -f app
```

### Шаг 3. Первый запуск: загрузка базы знаний

После старта контейнеров RAG не работает, пока не загружен и не проиндексирован PDF.

1. Откройте в браузере: **http://localhost:8000/admin/kb**
2. Введите логин и пароль из `ADMIN_USER` и `ADMIN_PASSWORD`
3. Выберите PDF-файл базы знаний (не более 20 МБ) и нажмите **«Загрузить файл»**
4. После успешной загрузки нажмите **«Проиндексировать базу знаний»** и подтвердите
5. Дождитесь окончания индексации (30–120 сек в зависимости от размера PDF). После этого бот начнёт отвечать по базе знаний

Данные БЗ сохраняются в volume `kb_data`: при перезапуске контейнеров заново загружать PDF не нужно, индекс подхватится при старте.

### Шаг 4. Проверка работы

- **API и Swagger:** http://localhost:8000 и http://localhost:8000/docs  
- **Здоровье и БЗ:** http://localhost:8000/health  
- **Метрики приложения:** http://localhost:8001  
- **Prometheus:** http://localhost:9090  
- **Grafana:** http://localhost:3000  

Пример запроса к боту (после индексации):

```bash
curl -X POST http://localhost:8000/process_message \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "test-1", "message": "Как пополнить карту?"}'
```

### Остановка и повторный запуск

Остановить все контейнеры:

```bash
docker compose down
```

Запустить снова (образы и volumes сохраняются):

```bash
docker compose up -d
```

Полная очистка (включая данные БЗ в volume `kb_data`, Redis, PostgreSQL):

```bash
docker compose down -v
```

---

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `OPENAI_API_KEY` | Ключ OpenAI | — (обязательно) |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL | см. пример выше |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | Redis (ключи: сессии, кэш, контекст) | localhost, 6379, 0 |
| `PROMETHEUS_PORT`, `APP_PORT` | Порты приложения и метрик | 8001, 8000 |
| `ADMIN_USER`, `ADMIN_PASSWORD` | Basic Auth для `/admin/kb`, `/UploadFile`, `/IndexDB`, `/kb/status` | admin, — (пароль обязателен) |
| `KB_MAX_FILE_SIZE_MB` | Макс. размер загружаемого PDF (МБ) | 20 |
| `QDRANT_HOST`, `QDRANT_PORT` | Qdrant (векторный поиск) | localhost, 6333 |
| `CONTEXT_MAX_CHARS` | Макс. размер контекста чата в символах | 2000 |
| `MAX_HISTORY_SIZE`, `SIMILARITY_THRESHOLD` | Пайплайн (история, порог повтора) | 3, 0.9 |

Redis в docker-compose запускается с лимитом памяти **512 МБ** и политикой вытеснения `allkeys-lru` (хранение только ключей).

---

## API

### Основные эндпоинты (без авторизации)

- **POST `/process_message`** — обработка сообщения. Тело: `chat_id`, `message`, опционально `language` (`"uz"` | `"ru"`).
- **POST `/escalate`** — ручная эскалация на оператора. Тело: `chat_id`.
- **GET `/health`** — статус Redis, PostgreSQL, БЗ, синонимов.
- **GET `/stats`** — активные чаты, счётчики эскалаций, размер БЗ и т.д.
- **GET `/synonyms/reload`**, **GET `/synonyms/status`** — перезагрузка и состояние словаря синонимов.
- **GET `/language_detection_stats`**, **POST `/clear_language_cache`** — статистика и очистка кэша детекции языка.

### Админка БЗ (HTTP Basic Auth)

- **GET `/admin/kb`** — веб-страница управления БЗ (загрузка PDF, кнопка переиндексации).
- **POST `/UploadFile`** — загрузка PDF (form: `file`, `name=KB.pdf`). Валидация: MIME, magic bytes, размер ≤ 20 МБ, читаемость.
- **POST `/IndexDB`** — переиндексация: PDF → чанки → Qdrant + BM25. При уже идущей индексации — 409.
- **GET `/kb/status`** — состояние БЗ (файл, даты, index_ready, indexing_in_progress, backup).

Документация API: http://localhost:8000/docs

---

## Локальный запуск (без Docker для приложения)

1. Установите зависимости и создайте виртуальное окружение:

```bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
# или: venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

2. Поднимите Redis, PostgreSQL и Qdrant (например, через docker-compose только эти сервисы или локально).

3. Настройте `.env`: укажите `REDIS_HOST`, `DB_HOST`, `QDRANT_HOST` по вашей конфигурации.

4. Запустите приложение:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

БЗ нужно один раз загрузить и проиндексировать через http://localhost:8000/admin/kb (или положить уже собранные `kb/KB.pdf` и `kb/knowledge_base.json` и выполнить индексацию через админку).

---

## Структура проекта

```
app/
├── main.py              # FastAPI, lifespan, роутеры
├── core/
│   ├── config.py       # настройки из env
│   └── dependencies.py # get_redis, get_openai_client, get_knowledge_base
├── api/
│   ├── process.py      # /process_message, /escalate
│   ├── health.py       # /health, /stats
│   ├── synonyms.py    # /synonyms/*
│   ├── language.py    # /language_detection_stats, /clear_language_cache
│   └── admin.py       # /UploadFile, /IndexDB, /kb/status, /admin/kb
├── models/
│   └── schemas.py     # MessageRequest, MessageResponse, EscalateRequest, ...
├── services/
│   ├── language.py    # детекция языка (OpenAI, кэш)
│   ├── embeddings.py  # эмбеддинги OpenAI, кэш Redis
│   ├── knowledge_base.py  # индексация PDF в Qdrant, чанки, kb_hash.json
│   ├── search.py      # гибридный поиск (Qdrant + BM25, RRF)
│   ├── bm25_store.py  # BM25-индекс (rank_bm25)
│   ├── qdrant_store.py # клиент Qdrant, коллекции, alias
│   ├── classification.py # классификация (правила + OpenAI)
│   ├── response.py    # генерация ответа (OpenAI), опциональный язык
│   ├── context.py    # контекст чата: get, асинхронное переписывание
│   ├── anonymization.py # PII-анонимизация
│   └── session.py    # сессия в Redis, cleanup_inactive_sessions
├── utils/
│   ├── synonyms.py    # загрузка и нормализация синонимов
│   ├── redis_utils.py # безопасные обёртки Redis
│   └── metrics.py    # метрики Prometheus
kb/                     # единственный источник БЗ
├── KB.pdf
├── KB_backup.pdf
├── knowledge_base.json # чанки (после индексации)
└── kb_hash.json        # upload_date, index_date, pdf_hash
docs/
├── ARCHITECTURE_AND_TZ.md
└── KB_MANAGEMENT_FEATURE.md
analyzer.py             # оффлайн-анализатор сессий (QC)
analyzer_config.json
synonyms.json
requirements.txt
docker-compose.yml
Dockerfile              # CMD uvicorn app.main:app ...
.env
```

---

## Зависимости (основные)

- `fastapi`, `uvicorn` — API
- `qdrant-client` — векторный поиск (Qdrant)
- `rank_bm25` — BM25 по чанкам
- `openai` — эмбеддинги, детекция языка, классификация, генерация ответа
- `pdfplumber` — извлечение текста из PDF
- `asyncpg`, `redis` — PostgreSQL и Redis
- `prometheus-client` — метрики
- `python-multipart` — загрузка файлов в админке

Полный список — в `requirements.txt`.

---

## Анализатор сессий (analyzer.py)

Отдельный скрипт для оффлайн-аналитики диалогов: классификация сессий и расчёт Q-Bot Index, запись в таблицу `session_analytics_1`. Конфиг: `analyzer_config.json`. Запуск: `python analyzer.py`. Требуются переменные окружения для БД и OpenAI (см. конфиг и скрипт).

---

## Документация

- **Архитектура и ТЗ:** `docs/ARCHITECTURE_AND_TZ.md`
- **Управление БЗ (админка):** `docs/KB_MANAGEMENT_FEATURE.md`

---

## Лицензия

[Укажите лицензию при необходимости]
