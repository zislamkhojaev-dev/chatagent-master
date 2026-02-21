# Paynet RAG Chatbot

Paynet RAG Chatbot — это API на основе FastAPI, реализующий чат-бота для обработки пользовательских запросов о платёжной системе Paynet. Бот использует Retrieval-Augmented Generation (RAG) для поиска релевантных ответов в базе знаний (PDF-документ), выполняет детекцию языка (ru/uz), нормализует текст по синонимам, классифицирует запросы, анонимизирует персональные данные (PII) и поддерживает эскалацию на оператора. Проект включает мониторинг и метрики (встроенный метрик-эндпоинт, Prometheus, Grafana), логирование взаимодействий в PostgreSQL и кэширование в Redis.

Репозиторий проекта: https://github.com/Zafar1997/OutRAGeiousChat

## Основные возможности

- **Обработка PDF**: Загрузка `KB.pdf`, разбиение текста на смысловые чанки с «мягкими» границами.
- **RAG**: FAISS-индекс + эмбеддинги OpenAI, кэширование эмбеддингов в Redis.
- **Детекция языка**: Определение ru/uz с кешированием результатов и fallback по истории.
- **Нормализация синонимов**: Подмена терминов по `synonyms.json` с метриками использования.
- **Классификация запросов**: Локальные правила + fallback к OpenAI при отсутствии совпадений.
- **Анонимизация PII**: Маскирование телефонов, паспортов, карт, PINFL, email.
- **Эскалация**: Автоэскалация при повторных/сложных запросах и ручная эскалация.
- **Мониторинг и метрики**: Встроенный метрик-сервер; docker-compose включает Prometheus и Grafana.
- **Логирование**: Запись взаимодействий в PostgreSQL.
- **Swagger UI**: Документация API.

## Требования

- Python 3.8+
- Docker (для Redis и PostgreSQL)
- OpenAI API ключ
- PDF-файл базы знаний (`KB.pdf`)

## Быстрый старт (docker-compose)

1. **Подготовьте `.env`** (в корне проекта):
   ```plaintext
   OPENAI_API_KEY=your-openai-api-key
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
   ```

2. **Положите `KB.pdf`** в корень проекта.

3. **Запустите весь стек** (API, Redis, Postgres, Prometheus, Grafana):
   ```bash
   docker compose up -d --build
   ```

4. **Доступы по умолчанию**:
   - API: `http://localhost:8000` (Swagger: `http://localhost:8000/docs`)
   - Метрики бота: `http://localhost:8001`
   - Prometheus: `http://localhost:9090`
   - Grafana: `http://localhost:3000`

5. **Остановка**:
   ```bash
   docker compose down
   ```

## Установка локально (без docker-compose)

1. Установите зависимости:
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   # source venv/bin/activate  # Linux/Mac
   pip install -r requirements.txt
   ```

2. Настройте `.env` (как выше), положите `KB.pdf` в корень.

3. Поднимите Redis и PostgreSQL (любой способ):
   ```bash
   docker run -d --name redis -p 6379:6379 redis:7
   docker run -d --name postgres -e POSTGRES_PASSWORD=123 -p 5432:5432 postgres:15
   ```

## Использование API

1. **Запустите сервер**:
   ```bash
   python bot9b.py
   ```
   Сервер будет доступен на `http://localhost:8000`.

2. **Основные эндпоинты**:
   - **POST `/process_message`** — обработка сообщения пользователя.
   - **POST `/escalate`** — ручная эскалация на оператора.
   - **GET `/health`** — статус Redis/DB/KB/синонимов.
   - **GET `/stats`** — агрегированные статистики и счётчики.
   - **GET `/synonyms/reload`** — перезагрузка `synonyms.json`.
   - **GET `/synonyms/status`** — состояние словаря синонимов.
   - **GET `/language_detection_stats`** — статистика кэша детекции языка.
   - **POST `/clear_language_cache`** — очистка кэша детекции языка в Redis.
   - Swagger: `http://localhost:8000/docs`

3. **Мониторинг**:
   - Встроенные метрики бота: `http://localhost:8001`
   - Prometheus (docker-compose): `http://localhost:9090`
   - Grafana (docker-compose): `http://localhost:3000`

## Анализатор сессий (QC) — `analyzer.py`

Скрипт для оффлайн-аналитики диалогов: классификация сессий и Quality Control (Q-Bot Index) с записью в БД.

- Требуемые переменные окружения (в `.env`): `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST1`, `DB_PORT`, `OPENAI_API_KEY`.
- Конфиг: `analyzer_config.json` (хранит `last_successful_analysis_date`).
- Таблица результатов по умолчанию: `session_analytics_1` (создайте в БД при необходимости).
- Запуск:
  ```bash
  python analyzer.py
  ```

## Развертывание (Dockerfile)

1. **Docker**:
   Создайте `Dockerfile`:
   ```dockerfile
   FROM python:3.8-slim
   WORKDIR /app
   COPY . .
   RUN pip install -r requirements.txt
   EXPOSE 8000
   CMD ["python", "bot9b.py"]
   ```
   Соберите и запустите:
   ```bash
   docker build -t paynet-rag-chatbot .
   docker run -p 8000:8000 --env-file .env -v $(pwd)/KB.pdf:/app/KB.pdf paynet-rag-chatbot
   ```

2. **Конфигурация**:
   - Убедитесь, что Redis и PostgreSQL доступны из контейнера (или используйте `docker compose`).
   - Настройте переменные окружения через `.env`.

## Структура проекта

```
archi4/
├── bot9b.py                # Основной файл API (FastAPI)
├── analyzer.py             # Оффлайн-анализатор (классификация и QC)
├── analyzer_config.json    # Конфигурация анализатора (дата последнего анализа)
├── Dockerfile
├── docker-compose.yml
├── prometheus.yml
├── requirements.txt
├── synonyms.json           # Словарь синонимов (ru/uz)
├── KB.pdf                  # База знаний (PDF)
├── faiss_index.bin         # Кэш индекса (генерируется)
├── knowledge_base.json     # Кэш чанков (генерируется)
├── kb_hash.json            # Хэш PDF для инвалидации кэша (генерируется)
└── README.md
```

## Зависимости

См. `requirements.txt`. Основные библиотеки:
- `fastapi`, `uvicorn`: API и сервер.
- `pdfplumber`: Обработка PDF.
- `faiss-cpu`: Векторный поиск.
- `openai`: Эмбеддинги и классификация.
- `asyncpg`, `redis`: Базы данных.
- `prometheus-client`: Мониторинг.
- `aiofiles`, `tiktoken`: Асинхронная работа с файлами и токенизация.
- `pytest`, `pytest-asyncio`, `pytest-mock`: Тестирование.

## Ограничения и улучшения

- **Ограничения**:
  - PDF должен быть текстовым (не сканированным).
  - OpenAI API требует стабильного интернета.
  - При изменении `KB.pdf` кэш будет автоматически перестроен при следующем запуске.

- **Возможные улучшения**:
  - Поддержка OCR для сканированных PDF.
  - Автоматический перевод/ответ на нескольких языках.
  - Дополнительные метрики Prometheus (например, размер чанков).
  - Расширение PII-шаблонов для ФИО и адресов.
  - Панель Grafana с преднастроенными дашбордами.

## Контакты

- Поддержка: support@paynet.xyz
- Разработчик: [Ваше имя или команда]

## Репозиторий

- Исходный код и обновления: https://github.com/Zafar1997/OutRAGeiousChat

## Лицензия

[Укажите лицензию, например, MIT]