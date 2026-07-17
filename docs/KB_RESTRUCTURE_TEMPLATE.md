# Шаблон переделки базы знаний (diff)

Документ для миграции **старой плоской БЗ** → **структурированной БЗ с метаданными**, которую понимает гибридный поиск и агент.

После правок: загрузить PDF (или положить `kb/kb.txt`) → `/admin/kb` → **Проиндексировать**.

Связано: [`AGENT_PIPELINE.md`](AGENT_PIPELINE.md) (фильтры поиска по сценарию), `app/services/kb_metadata.py`.

---

## 1. Как индексатор читает БЗ

При индексации (`chunk_text_with_metadata` в `app/services/kb_metadata.py`):

### Primary: явные теги `[kb ...]`

1. Текст режется по **каждому** вхождению `[kb audience=... channel=... topic=...]` — **независимо от `\n\n`** (устойчиво к «склеенному» PDF-экспорту).
2. Атрибуты тега задают metadata чанка; тело блока — текст после тега до следующего `[kb`.
3. Частые опечатки нормализуются: `audience=general` → `both`, `channel=sms` → `mobile_app`.
4. В Qdrant: `audience`, `channel`, `topic`, `section`, `text`.

Пример блока:

```text
[kb audience=client channel=mobile_app topic=sms]
SMS / OTP код не приходит
1. Проверьте номер телефона.
2. Отключите VPN и запросите код повторно через 2 минуты.
```

### Fallback (legacy)

Если тегов нет: абзацы по `\n\n`, короткие заголовки разделов с ключевыми словами канала, эвристический `topic`. Менее надёжно — для новой БЗ используйте **только** `[kb]` теги.

### Допустимые значения

| Поле | Значения |
|------|----------|
| `audience` | `client`, `agent`, `both` |
| `channel` | `mobile_app`, `agent`, `infokiosk`, `general` |
| `topic` | свободная строка из таксономии / сценариев: `qr`, `payment`, `refund`, `sms`, `identification`, `general`, … |

### Legacy-триггеры в заголовках (только fallback)

| Слова в заголовке | audience | channel |
|-------------------|----------|---------|
| мобильн, приложен, ilova, paynet app | `client` | `mobile_app` |
| агент, agent, cashout, агентск | `agent` | `agent` |
| инфокиоск, infokiosk, киоск, kiosk, терминал | `both` | `infokiosk` |
| общ, umumiy, general, для всех | `both` | `general` |

| topic | Ключевые слова (legacy) |
|-------|-------------------------|
| `qr` | qr, qr-kod, сканирован, skaner |
| `payment` | оплат, to'lov, платеж, payment |
| `refund` | возврат, qaytarish, отмен, bekor, ошибочн |
| `sms` | sms, смс, код не приходит |
| `identification` | идентификац, identifikats |

---

## 2. Общий diff: структура документа

Предпочтительный формат — **явный `[kb]` перед каждым блоком**. Заголовки можно оставить для людей; для индексатора важен тег.

```diff
- Paynet — база знаний
-
- QR-код
- Если QR не работает, клиенту перезапустите приложение. Агенту проверьте cashout и перепечатайте чек.

+ [kb audience=both channel=general topic=general]
+ Общая информация. Контакты поддержки: ...
+
+ [kb audience=client channel=mobile_app topic=sms]
+ SMS не приходит / kod kelmayapti
+ ...
+
+ [kb audience=client channel=mobile_app topic=qr]
+ QR-код не сканируется
+ ...
+
+ [kb audience=agent channel=agent topic=qr]
+ QR при приёме оплаты (агент)
+ ...
+
+ [kb audience=both channel=infokiosk topic=payment]
+ Инфокиоск: ошибка оплаты
+ ...
```

**Правило:** один блок с `[kb]` = одна инструкция для **одной** аудитории и **одного** канала.

---

## 3. Diff по сценариям (`scenarios/scenarios.json`)

### 3.1 `qr_issue` — QR не работает

Фильтр поиска: `topic=qr`, по умолчанию `audience=client`.

```diff
- QR-код
- Если QR не сканируется: перезапустите приложение или проверьте cashout у агента.

+ [kb audience=client channel=mobile_app topic=qr]
+ QR-kod skanerlanmayapti / QR-код не сканируется
+ 1. Закройте и откройте приложение Paynet.
+ 2. Проверьте камеру и освещение.
+ 3. Если не помогло — обратитесь в поддержку с номером телефона.
+
+ [kb audience=agent channel=agent topic=qr]
+ QR-kod to'lovda ishlamayapti / QR при приёме оплаты
+ 1. Проверьте подключение терминала к cashout.
+ 2. Перезапустите смену в агентском ПО.
+ 3. Перепечатайте чек, если оплата прошла без чека.
```

Ожидаемые метаданные после индексации:

| Чанк | audience | channel | topic |
|------|----------|---------|-------|
| Клиентский | `client` | `mobile_app` | `qr` |
| Агентский | `agent` | `agent` | `qr` |

---

### 3.2 `payment_failed` — оплата не прошла

Фильтр: `topic=payment`; канал уточняется слотом `payment_channel`.

```diff
- Оплата не прошла
- Попробуйте ещё раз или обратитесь в банк.

+ Мобильное приложение Paynet
+
+ To'lov xatosi / Ошибка оплаты в приложении
+ 1. Проверьте баланс карты.
+ 2. Повторите платёж через 5 минут.
+ 3. Сохраните скриншот ошибки для поддержки.
+
+ Агенты и cashout
+
+ Agentda to'lov o'tmadi / Ошибка оплаты у агента
+ 1. Проверьте лимит агента.
+ 2. Убедитесь, что услуга доступна в cashout.
+ 3. При повторной ошибке — эскалация на линию поддержки агентов.
+
+ Инфокиоск
+
+ Infokioskda to'lov o'tmadi / Ошибка оплаты в инфокиоске
+ 1. Проверьте купюроприёмник и связь терминала.
+ 2. Не вносите повторно ту же сумму без подтверждения статуса.
+ 3. Запишите номер терминала и время операции.
```

---

### 3.3 `refund` — возврат / отмена

Фильтр: `topic=refund`.

```diff
- Возврат средств
- Возврат возможен в течение 3 рабочих дней через банк.

+ Мобильное приложение Paynet
+
+ Ilovada qaytarish / Возврат платежа в приложении
+ Условия, сроки, что приложить клиенту (дата, сумма, скрин).
+
+ Агенты и cashout
+
+ Agent orqali qaytarish / Возврат через агента
+ Порядок оформления возврата агентом, документы, сроки.
+
+ Инфокиоск
+
+ Infokiosk orqali qaytarish / Возврат через инфокиоск
+ Когда возврат на терминале невозможен, куда направить клиента.
```

---

### 3.4 `sms_not_received` — SMS не приходит

Фильтр: `topic=sms`, `audience=client`, `channel=mobile_app`. Уточняющих вопросов нет (`clarify_policy: never`).

```diff
- SMS
- Код может задерживаться. Подождите.

+ [kb audience=client channel=mobile_app topic=sms]
+ SMS kod kelmayapti / SMS-код не приходит
+ 1. Проверьте, что номер введён верно.
+ 2. Отключите VPN и режим «Не беспокоить».
+ 3. Запросите код повторно через 2 минуты.
+ 4. Если SMS не приходит более 15 минут — обратитесь к оператору связи или в поддержку Paynet.
```

**Не используйте** `channel=general` / `topic=general` для прикладных инструкций — фильтр сценария перестанет попадать в чанк.

---

### 3.5 `agent_payment` — проблемы агента / чек

Фильтр: `audience=agent`, `channel=agent`, `topic=payment`.

```diff
- Агент
- Если чек не вышел, перезагрузите терминал.

+ Агенты и cashout
+
+ Agent chek bermadi / Чек не выдался после cashout
+ 1. Проверьте статус транзакции в cashout.
+ 2. Перепечатайте чек из истории операций.
+ 3. Если операция в статусе «ошибка» — не принимайте повторную оплату без проверки.
```

---

### 3.6 `infokiosk` — инфокиоск

Фильтр: `channel=infokiosk`, `topic=payment`.

```diff
- Терминал
- При сбое перезагрузите устройство.

+ Инфокиоск
+
+ Infokiosk ishlamayapti / Инфокиоск не работает
+ 1. Проверьте наличие связи (индикатор на экране).
+ 2. Сообщите номер терминала в поддержку.
+ 3. Не оставляйте купюры при зависшем экране до подтверждения статуса платежа.
```

---

### 3.7 `mobile_app` — общие вопросы приложения

Фильтр: `audience=client`, `channel=mobile_app`.

```diff
- Приложение Paynet
- Скачайте из Store, войдите по номеру телефона.

+ Мобильное приложение Paynet
+
+ Ilovani o'rnatish / Установка и вход
+ ...
+
+ Identifikatsiya / Идентификация в приложении
+ ...
+
+ Profil va sozlamalar / Профиль и настройки
+ ...
```

---

## 4. Антипаттерны (что вырезать)

```diff
- Если вы клиент — сделайте A. Если агент — сделайте B. В инфокиоске — C.
+ (разбить на три абзаца в трёх разделах)

- Paynet FAQ (50 страниц без заголовков)
+ Добавить заголовки разделов каждые 1–3 экрана

- QR-kod (без указания канала)
+ Подзаголовок внутри «Мобильное приложение» или «Агенты и cashout»

- Umumiy ma'lumot: barcha to'lovlar bo'yicha bir xil
+ В «Общая информация» только универсальные правила; канал-специфичное — в канал
```

---

## 5. Целевой каркас тегов (скопировать и заполнить)

```text
[kb audience=both channel=general topic=general]
Общая информация
...

[kb audience=client channel=mobile_app topic=identification]
Идентификация в приложении
...

[kb audience=client channel=mobile_app topic=sms]
SMS не приходит
...

[kb audience=client channel=mobile_app topic=qr]
QR-код не сканируется
...

[kb audience=client channel=mobile_app topic=payment]
Ошибка оплаты в приложении
...

[kb audience=client channel=mobile_app topic=refund]
Возврат платежа в приложении
...

[kb audience=agent channel=agent topic=qr]
QR при приёме оплаты
...

[kb audience=agent channel=agent topic=payment]
Ошибка оплаты / чек у агента
...

[kb audience=both channel=infokiosk topic=payment]
Ошибка оплаты в инфокиоске
...
```

Можно хранить как `kb/kb.txt` (UTF-8) без PDF — `IndexDB` подхватит TXT, если PDF нет.

---

## 6. Проверка после переиндексации

1. Открыть `kb/knowledge_base.json`.
2. Убедиться, что **нет массового** `audience=both` + `channel=general` + `topic=general` у прикладных инструкций.
3. В логах индексации: `tagged` ≫ `legacy` (лучше 100% tagged).
4. Примеры ожидаемых записей:

```json
{
  "text": "1. Закройте и откройте приложение Paynet...",
  "audience": "client",
  "channel": "mobile_app",
  "topic": "qr"
}
```

5. Тестовые запросы:

| Сообщение | Ожидание |
|-----------|----------|
| «QR не работает» | Ответ для клиента (если клиентский чанк однозначен) |
| «Я агент, QR не сканируется» | agent + qr |
| «SMS не приходит» | mobile_app + sms, без уточнений |
| «Оплата не прошла в инфокиоске» | infokiosk + payment |
| «Агент не выдал чек» | agent + payment |

---

## 7. Workflow для редактора БЗ

1. Экспортировать текущий `KB.pdf` в текст (или править `kb.txt` напрямую).
2. Перед каждым логическим блоком вставить `[kb audience=... channel=... topic=...]`.
3. Не полагаться на пустые строки после PDF-экспорта — разделитель для индексатора это сам тег.
4. PDF → `/admin/kb` → загрузка → **Проиндексировать** (или IndexDB при наличии `kb/kb.txt`).
5. Проверка по разделу 6.

---

## 8. Связь с кодом

| Компонент | Файл |
|-----------|------|
| `[kb]` split, coerce, legacy fallback | `app/services/kb_metadata.py` |
| PDF/TXT → Qdrant | `app/services/knowledge_base.py` |
| Фильтры поиска по сценарию | `build_metadata_filter()` в `kb_metadata.py` |
| Сценарии (`default_*`, descriptions для router) | `scenarios/scenarios.json` |
| Embedding / hybrid матчинг сценариев | `app/services/scenario_router.py` |

При новой теме: обновите **и** `scenarios.json` (triggers + `description_ru/uz` + topic), **и** блоки `[kb topic=...]` в БЗ.
