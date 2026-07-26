# Бэклог / ideas

## Сделать

### Прочее (позже)

- Реранкер (был в ТЗ, не реализован)
- OpenAI Batch API для `analyzer.py` (ночные прогоны)
- Incremental rebuild embedding одного сценария (сейчас полный rebuild индекса)
- Расширить keyword rudeness-list (сейчас только слова из `CATEGORIES` «Хулиганство»)

## Уже сделано (не трогать)

- Metadata-фильтры KB / `[kb]` теги
- Порог «пустого RAG» → clarify / escalate (`kb_guard`)
- Hybrid embedding scenario router
- Убран OpenAI-classify из `/process_message`; keyword rudeness + stub classification (полная таксономия в analyzer)
- Critical fixes: word-boundary rudeness, clear count/embedding on escalate, slot parse + scenario unlock, MIN_RRF_SCORE, reuse query_embedding, kb_answer_mode prompt
