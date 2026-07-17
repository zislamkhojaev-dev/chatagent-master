# Документация Paynet RAG Chatbot

| Документ | Назначение |
|----------|------------|
| [../README.md](../README.md) | Запуск, env, API, структура репозитория |
| [AGENT_PIPELINE.md](AGENT_PIPELINE.md) | Пайплайн агента, сценарии, hybrid router, RAG, эскалация |
| [KB_RESTRUCTURE_TEMPLATE.md](KB_RESTRUCTURE_TEMPLATE.md) | Разметка БЗ тегами `[kb ...]`, таксономия, чеклист |
| [KB_MANAGEMENT_FEATURE.md](KB_MANAGEMENT_FEATURE.md) | Админка `/admin/kb`, Upload/IndexDB |

Бэклог идей: [`../plans to do.md`](../plans%20to%20do.md)

Кратко по текущему стеку:

1. `POST /process_message` → язык, PII, hard guards (оператор / repeat / keyword rudeness) → **один** embedding.
2. Stub `classification` (`scenario_id` / mode / reason) — **без** OpenAI-classify; полная таксономия в `analyzer.py`.
3. `run_agent` → `resolve_scenario` (hybrid) → probe KB → ответ или эскалация.
4. БЗ индексируется из PDF/TXT с `[kb]`-тегами в Qdrant + BM25.
