# Бэклог / ideas

## Сделать

### Убрать онлайн-классификацию из `/process_message` (оставить в analyzer)

**Цель:** −1 LLM/keyword-проход на сообщение; не дублировать таксономию с `analyzer.py`.

**Сейчас в hot path:** `classify_query` → hard escalation (`rudeness` / `auto_escalate_categories`), поле API `classification`, лог `interactions.classification`, summary оператору, метрики.

**Сценарии / RAG / embedding-router от theme/category не зависят.**

**План реализации:**
1. Убрать (или сделать optional) OpenAI-classify из `app/api/process.py` / `classification.py`.
2. Оставить **дешёвый keyword-only** guard для хамства и `auto_escalate_categories`.
3. В `interactions` / API писать заглушку или `scenario_id` + `escalation_reason` вместо полной таксономии.
4. Полная Категория / Подкатегория / Тематика — только в `analyzer.py` → `session_analytics_1`.
5. Обновить docs (`AGENT_PIPELINE.md`, README) и контракт API (breaking для клиентов, читающих `classification`).

**Риски:** потеря мгновенной эскалации хамства без keyword-guard; онлайн-отчёты по `interactions.classification` станут пустыми до ночного analyzer.

---

### Прочее (позже)

- Реранкер (был в ТЗ, не реализован)
- OpenAI Batch API для `analyzer.py` (ночные прогоны)
- Incremental rebuild embedding одного сценария (сейчас полный rebuild индекса)

## Уже сделано (не трогать)

- Metadata-фильтры KB / `[kb]` теги
- Порог «пустого RAG» → clarify / escalate (`kb_guard`)
- Hybrid embedding scenario router
