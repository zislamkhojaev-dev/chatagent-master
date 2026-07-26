# Paynet RAG Chatbot

Чат-бот колл-центра Paynet на FastAPI: ответы на запросы на **русском** и **узбекском** (латиница) по базе знаний с RAG, детекцией языка, keyword rudeness-guard, анонимизацией PII и эскалацией на оператора. Полная тематическая классификация сессий — в offline `analyzer.py`.

**Репозиторий:** [https://github.com/Zafar1997/OutRAGeiousChat](https://github.com/Zafar1997/OutRAGeiousChat)

---

## Архитектура

- **API:** FastAPI (`app/`), точка входа `uvicorn app.main:app`.
- **Агент:** thinking-agent с сценариями, уточняющими слотами, tool-calling и жёстким KB-guard (без релевантных чанков — уточнение или эскалация, не свободный ответ).
- **Матчинг сценариев:** hybrid embedding-router (cosine по описаниям сценариев) + substring fallback по `triggers`; эмбеддинг сообщения **переиспользуется** из `/process_message`.
- **RAG:** гибридный поиск — **Qdrant** + **BM25**, RRF; чанки с метаданными `audience` / `channel` / `topic` (явные теги `[kb ...]` в PDF/TXT).
- **Хранение:** PostgreSQL (логи), Redis (сессии, кэш, контекст чата, agent_state), Qdrant (векторный индекс).
- **Админка:** `/admin/kb` (БЗ), `/admin/scenarios` (сценарии) — Basic Auth.

Источник БЗ — каталог `**kb/**` (`KB.pdf` или `kb.txt`). Индекс: админка → «Проиндексировать»; при старте подхватывается alias Qdrant и `kb/knowledge_base.json`.


| Документ                                                             | Содержание                                                      |
| -------------------------------------------------------------------- | --------------------------------------------------------------- |
| [`docs/AGENT_PIPELINE.md`](docs/AGENT_PIPELINE.md)                   | Пайплайн агента, сценарии, router, RAG, эскалация, env          |
| [`docs/KB_RESTRUCTURE_TEMPLATE.md`](docs/KB_RESTRUCTURE_TEMPLATE.md) | Разметка БЗ тегами `[kb ...]`, таксономия                       |
| [`docs/KB_MANAGEMENT_FEATURE.md`](docs/KB_MANAGEMENT_FEATURE.md)     | Админка загрузки/индексации                                     |


---

## Возможности

- Обработка сообщений: язык (ru/uz), синонимы, keyword rudeness-guard (без OpenAI-classify), анонимизация PII, hard escalation.
- Сценарии из `scenarios/scenarios.json`: уточнения, фильтры KB, hot-reload; редактор `/admin/scenarios`.
- Hybrid scenario router (`SCENARIO_ROUTER_MODE=hybrid|embedding|substring`).
- RAG: Qdrant + BM25 + metadata filters по сценарию/слотам.
- Параметр **`language`** в API: передавайте `"uz"` или `"ru"` с клиента — без вызова детекции языка (~экономия 1–3 с).
- Эскалация: hard guards + tool `escalate`; ручная `POST /escalate`.
- Мониторинг: Prometheus (8001), `/health`, `/stats`.
- Управление БЗ: `/admin/kb` (PDF → индексация), Basic Auth.

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

# Админка БЗ и сценариев — обязательно задайте пароль
ADMIN_USER=admin
ADMIN_PASSWORD=your-secure-password
KB_MAX_FILE_SIZE_MB=20

# Qdrant (имена сервисов из docker-compose)
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# Агент и матчинг сценариев
AGENT_ENABLED=true
AGENT_TEMPERATURE=0.2
SCENARIO_ROUTER_MODE=hybrid
SCENARIO_ROUTER_THRESHOLD=0.55
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

### Шаг 3. Первый запуск: база знаний и сценарии

После старта RAG не работает, пока не загружен и не проиндексирован источник БЗ.

1. Откройте **[http://localhost:8000/admin/kb](http://localhost:8000/admin/kb)** (логин/пароль из `ADMIN_`*)
2. Загрузите PDF (≤ 20 МБ) с разметкой `[kb audience=... channel=... topic=...]` — см. `[docs/KB_RESTRUCTURE_TEMPLATE.md](docs/KB_RESTRUCTURE_TEMPLATE.md)`
3. Нажмите **«Проиндексировать»** и дождитесь окончания (30–120 сек)
4. При необходимости отредактируйте сценарии: **[http://localhost:8000/admin/scenarios](http://localhost:8000/admin/scenarios)**

Данные БЗ в volume `kb_data`: при перезапуске PDF заново загружать не нужно. Эмбеддинги сценариев пересобираются при старте и после save в админке сценариев.

### Шаг 4. Проверка работы

- **API и Swagger:** [http://localhost:8000](http://localhost:8000) и [http://localhost:8000/docs](http://localhost:8000/docs)  
- **Админка БЗ:** [http://localhost:8000/admin/kb](http://localhost:8000/admin/kb)  
- **Админка сценариев:** [http://localhost:8000/admin/scenarios](http://localhost:8000/admin/scenarios)  
- **Здоровье:** [http://localhost:8000/health](http://localhost:8000/health)  
- **Метрики:** [http://localhost:8001](http://localhost:8001) · **Prometheus:** [http://localhost:9090](http://localhost:9090) · **Grafana:** [http://localhost:3000](http://localhost:3000)

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


| Переменная                                                | Описание                                          | По умолчанию                 |
| --------------------------------------------------------- | ------------------------------------------------- | ---------------------------- |
| `OPENAI_API_KEY`                                          | Ключ OpenAI                                       | — (обязательно)              |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL                                        | см. пример выше              |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`                    | Redis (сессии, кэш, контекст, agent_state)        | localhost, 6379, 0           |
| `PROMETHEUS_PORT`, `APP_PORT`                             | Порты приложения и метрик                         | 8001, 8000                   |
| `ADMIN_USER`, `ADMIN_PASSWORD`                            | Basic Auth для `/admin/*`, Upload/Index/KB status | admin, — (пароль обязателен) |
| `KB_MAX_FILE_SIZE_MB`                                     | Макс. размер загружаемого PDF (МБ)                | 20                           |
| `QDRANT_HOST`, `QDRANT_PORT`                              | Qdrant                                            | localhost, 6333              |
| `AGENT_ENABLED`                                           | Режим агента (иначе 503 на process)               | true                         |
| `AGENT_TEMPERATURE`                                       | Temperature LLM агента                            | 0.2                          |
| `SCENARIO_ROUTER_MODE`                                    | `hybrid` | `embedding` | `substring`              | hybrid                       |
| `SCENARIO_ROUTER_THRESHOLD`                               | Мин. cosine для embedding-матча сценария          | 0.55                         |
| `CONTEXT_MAX_CHARS`                                       | Макс. размер контекста чата                       | 2000                         |
| `MAX_HISTORY_SIZE`, `SIMILARITY_THRESHOLD`                | История / порог повтора → эскалация               | 3, 0.9                       |


Полный список параметров агента и RAG — в `[docs/AGENT_PIPELINE.md](docs/AGENT_PIPELINE.md)`. Redis в docker-compose: **512 МБ**, `allkeys-lru`.

---

## API

### Основные эндпоинты (без авторизации)

- **POST `/process_message`** — обработка сообщения. Тело: `chat_id`, `message`, опционально `language` (`"uz"` | `"ru"`).
- **POST `/escalate`** — ручная эскалация на оператора. Тело: `chat_id`.
- **GET `/health`** — статус Redis, PostgreSQL, БЗ, синонимов.
- **GET `/stats`** — активные чаты, счётчики эскалаций, размер БЗ и т.д.
- **GET `/synonyms/reload`**, **GET `/synonyms/status`** — перезагрузка и состояние словаря синонимов.
- **GET `/language_detection_stats`**, **POST `/clear_language_cache`** — статистика и очистка кэша детекции языка.

### Админка (HTTP Basic Auth)

- **GET `/admin/kb`** — загрузка PDF и переиндексация БЗ.
- **POST `/UploadFile`** — загрузка PDF (`file`, `name=KB.pdf`).
- **POST `/IndexDB`** — индексация PDF/TXT → чанки с metadata → Qdrant + BM25 (409, если уже идёт).
- **GET `/kb/status`** — файл, даты, index_ready, indexing_in_progress, backup.
- **GET `/admin/scenarios`** — редактор сценариев (карточки + JSON).
- **GET/PUT `/scenarios`**, **POST `/scenarios/reload`** — API сценариев; после save/reload пересобираются embeddings роутера.

Документация API: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Локальный запуск (без Docker для приложения)

1. Установите зависимости и создайте виртуальное окружение:

```bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
# или: venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

1. Поднимите Redis, PostgreSQL и Qdrant (например, через docker-compose только эти сервисы или локально).
2. Настройте `.env`: укажите `REDIS_HOST`, `DB_HOST`, `QDRANT_HOST` по вашей конфигурации.
3. Запустите приложение:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

БЗ нужно один раз загрузить и проиндексировать через [http://localhost:8000/admin/kb](http://localhost:8000/admin/kb) (или положить уже собранные `kb/KB.pdf` и `kb/knowledge_base.json` и выполнить индексацию через админку).

---

## Структура проекта

```
app/
├── main.py                 # FastAPI, lifespan (в т.ч. scenario embeddings)
├── core/config.py          # env, в т.ч. SCENARIO_ROUTER_*
├── api/
│   ├── process.py          # /process_message → run_agent (+ query_embedding)
│   ├── admin.py            # /admin/kb, Upload, IndexDB
│   ├── scenarios_admin.py  # API сценариев + rebuild router
│   └── scenarios_admin_ui.py
├── services/
│   ├── agent/              # orchestrator, kb_guard, prompts, tools
│   ├── scenario_router.py  # hybrid embedding / substring матчинг
│   ├── scenarios.py        # загрузка scenarios.json
│   ├── kb_metadata.py      # [kb] теги, фильтры, evaluate_kb_hits
│   ├── knowledge_base.py   # индексация PDF/TXT → Qdrant
│   ├── search.py           # hybrid Qdrant + BM25 (RRF)
│   ├── embeddings.py       # OpenAI embeddings, кэш Redis
│   ├── escalation.py       # эскалация + summary
│   └── session.py          # Redis session / agent_state
scenarios/scenarios.json    # сценарии агента
kb/                         # KB.pdf или kb.txt, knowledge_base.json
docs/
├── README.md
├── AGENT_PIPELINE.md
├── KB_RESTRUCTURE_TEMPLATE.md
└── KB_MANAGEMENT_FEATURE.md
tests/
├── test_scenario_router.py
└── test_scenarios.py
```

---

## Зависимости (основные)

- `fastapi`, `uvicorn` — API
- `qdrant-client` — векторный поиск (Qdrant)
- `rank_bm25` — BM25 по чанкам
- `openai` — эмбеддинги, детекция языка, ответ агента; offline-классификация сессий — в `analyzer.py`
- `pdfplumber` — извлечение текста из PDF
- `asyncpg`, `redis` — PostgreSQL и Redis
- `prometheus-client` — метрики
- `python-multipart` — загрузка файлов в админке

Полный список — в `requirements.txt`.

---

## Анализатор сессий (analyzer.py)

Отдельный скрипт для оффлайн-аналитики диалогов: **один** вызов LLM на сессию (классификация + QC → JSON), расчёт Q-Bot Index, запись в `session_analytics_1`.

- Конфиг даты: `analyzer_config.json`
- Модель / параллелизм: `ANALYZER_MODEL`, `ANALYZER_MAX_WORKERS`, `ANALYZER_CHUNK_SIZE`
- Схема классификации и критерии QC — в **system** prompt (кэшируемый префикс); в user уходит только диалог

Запуск: `python analyzer.py`. Нужны переменные БД (`DB_HOST1` и др.) и `OPENAI_API_KEY`.

---

## Документация

Индекс: [`docs/README.md`](docs/README.md)

- **Пайплайн агента (актуально):** [`docs/AGENT_PIPELINE.md`](docs/AGENT_PIPELINE.md)
- **Разметка БЗ `[kb]`:** [`docs/KB_RESTRUCTURE_TEMPLATE.md`](docs/KB_RESTRUCTURE_TEMPLATE.md)
- **Админка БЗ:** [`docs/KB_MANAGEMENT_FEATURE.md`](docs/KB_MANAGEMENT_FEATURE.md)

---

## Лицензия

MIT