# session_analyzer_qc.py - Анализатор сессий с классификацией и Quality Control (QC)
# Использует таблицу session_analytics_1 и поддерживает МНОГОПОТОЧНУЮ ОБРАБОТКУ.

import os
import psycopg2
import json
import logging
from openai import OpenAI, RateLimitError, APIError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz 
import concurrent.futures
import time

# --- 1. Конфигурация ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

required_vars = ["DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST1", "DB_PORT", "OPENAI_API_KEY"]
for var in required_vars:
    if not os.getenv(var):
        raise ValueError(f"Ошибка: переменная окружения {var} не найдена в .env файле.")

# Использование модели gpt-4o-mini (один вызов: классификация + QC)
ANALYSIS_MODEL = os.getenv("ANALYZER_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Имя файла конфигурации
CONFIG_FILE = "analyzer_config.json"
# ИМЯ ТАБЛИЦЫ АНАЛИТИКИ
ANALYTICS_TABLE = "session_analytics_1"

# Параметры параллельной обработки
MAX_WORKERS = int(os.getenv("ANALYZER_MAX_WORKERS", "10"))
CHUNK_SIZE = int(os.getenv("ANALYZER_CHUNK_SIZE", "50"))  # Размер пачки для записи в БД

# --- 2. Схемы для анализа ---

# Схема для Классификации (взята из session_analyzer.py)
CLASSIFICATION_SCHEMA = """
Категория: Претензия/Жалоба
  Подкатегория: Оплата услуг через агента
    Тематика: Агент не совершил платеж
    Тематика: Платежный агент не выдал чек
    Тематика: Платежный агент отказался оказывать услуги Paynet
  Подкатегория: Оплата услуг через инфокиоск
    Тематика: Инфокиоск не выдал чек оплаты
  Подкатегория: Оплата через Paynet Avia
    Тематика: Невозможно купить билет
  Подкатегория: Прочие жалобы
    Тематика: Отказ от использования МП Paynet
    Тематика: Не согласен с решением заявки
    Тематика: Мошенничество
    Тематика: Жалоба на сотрудника офиса
    Тематика: Жалоба на сотрудника КЦ
Категория: Сервисное обслуживание
  Подкатегория: Документы
    Тематика: Просит предоставить чек оплаты
    Тематика: Реквизиты компании
Категория: Вакансия
  Подкатегория: Вакансия
    Тематика: Узнать о вакансиях
    Тематика: куда отправить резюме
Категория: Paynet Avia
  Подкатегория: Информация
    Тематика: Отправка билета
Категория: Хулиганство
  Подкатегория: Хулиганство
    Тематика: Мат
    Тематика: Хулиганство
Категория: Предложения
  Подкатегория: Предложения
    Тематика: Вопросы по инфокиоску (покупка, отказ)
    Тематика: Прочие предложения
    Тематика: Хочет стать агентом
    Тематика: Хочет стать партнером
Категория: Информация
  Подкатегория: Акции
    Тематика: Беспроцентный перевод
    Тематика: Монеты
  Подтема: Запрос
    Тематика: Коммуникация
  Подкатегория: Оплата услуг через агента
    Тематика: CashOut|CashOut NFC
    Тематика: Отмена ошибочного платежа через платежных агентов
    Тематика: Пополнение HUMO/UZCARD
    Тематика: Проверка оплат
    Тематика: Результат претензии
  Подкатегория: Оплата услуг через инфокиоск
    Тематика: CashIn 3%4%5%
    Тематика: Не отображается сумма на балансе абонентского номера
    Тематика: Не работает инфокиоск
    Тематика: Общая информация
    Тематика: Оплата через другие платежные инструменты
    Тематика: Отмена ошибочного платежа
    Тематика: Пополнение VISA/MasterCard
    Тематика: Проверка оплат
    Тематика: Проверка оплат за пополнение HUMO/UZCARD
    Тематика: Просит предоставить чек
    Тематика: Результат претензии
    Тематика: Электронный кошелек
  Подкатегория: Оплата услуг через мобильное приложение
    Тематика: Crypto
    Тематика: P2P Wallet
    Тематика: Paynet GOLD
    Тематика: Paynet Nasiya
    Тематика: VISA Direct
    Тематика: Идентификация
    Тематика: ЖД билеты
    Тематика: Информация о бонусах и кешбек
    Тематика: Мошенничество
    Тематика: Не приходит SMS (спам)
    Тематика: Общая информация о мобильном приложении
    Тематика: Проверка P2P
  Подкатегория: Оплата через Paynet Avia
    Тематика: Paynet Avia
    Тематика: Общая информация
    Тематика: Результат претензии
  Подкатегория: Поставщики
    Тематика: Foyda
    Тематика: Общая информация
"""

# Схема для Quality Control (QC)
WEIGHTS = {
    "K_РЕЛ": 5,   # Релевантность
    "K_ПОВТ": 4,  # Повторное обращение
    "K_ЧП": 3,    # Четкость и полнота
    "K_ПОВ": 2,   # Поведение клиента
    "K_НЕГ": 1    # Негативная лексика
}
TOTAL_WEIGHT = sum(WEIGHTS.values())  # 15

# Схема + QC в system: одинаковый префикс → prompt caching на стороне OpenAI.
# User message — только диалог (без повторной схемы).
ANALYSIS_SYSTEM_PROMPT = f"""
Ты — эксперт по анализу клиентских обращений и контролю качества (QC) чат-бота Paynet.
За один проход по диалогу сделай ДВЕ задачи и верни ОДИН JSON.

══════════════════════════════════════
ЗАДАЧА 1. КЛАССИФИКАЦИЯ
══════════════════════════════════════
Выбери ОДНУ наиболее подходящую Категорию, Подкатегорию и Тематику из схемы ниже.

**Схема классификации:**
{CLASSIFICATION_SCHEMA}

══════════════════════════════════════
ЗАДАЧА 2. QUALITY CONTROL (QC)
══════════════════════════════════════
Оцени качество ответов бота по 5 критериям.

**Шкала (для всех критериев):**
- 3: высокий уровень
- 2: средний / нейтральный
- 1: низкий уровень / провал

1. **K_РЕЛ (Релевантность)**: 3 — полностью по теме; 2 — частично; 1 — не по теме / неверно.
2. **K_ЧП (Четкость и полнота)**: 3 — полный и ясный; 2 — частично; 1 — неясный / пустой.
3. **K_ПОВ (Поведение клиента в конце)**: 3 — позитив/нейтрал; 2 — нет реакции; 1 — негатив.
4. **K_НЕГ (Негативная лексика клиента)**: 3 — нет негатива; 2 — умеренно; 1 — грубость/мат.
5. **K_ПОВТ (Повторное обращение)**: только 3 (нет признаков) или 1 (явные признаки «уже писал»).

══════════════════════════════════════
ФОРМАТ ОТВЕТА
══════════════════════════════════════
Верни СТРОГО один JSON-объект (без markdown и пояснений) с полями:
{{
  "Категория": "string",
  "Подкатегория": "string",
  "Тематика": "string",
  "K_РЕЛ": 1|2|3,
  "K_ЧП": 1|2|3,
  "K_ПОВ": 1|2|3,
  "K_НЕГ": 1|2|3,
  "K_ПОВТ": 1|3
}}

Индекс Q-Bot на стороне клиента не нужен — его посчитает код.
"""

# --- 3. Функции для работы с данными ---

def load_start_date() -> datetime:
    """Читает дату последнего успешного анализа из конфигурационного файла."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        date_str = config.get("last_successful_analysis_date")
        if date_str:
            # Преобразуем строку в datetime
            start_date_naive = datetime.strptime(date_str, '%Y-%m-%d')
            # Делаем объект aware (осведомленным о часовом поясе +05:00)
            start_date_aware = pytz.timezone('Asia/Tashkent').localize(start_date_naive)
            logging.info(f"Начальная дата анализа (из config): {start_date_aware}")
            return start_date_aware
        
        logging.warning("Дата 'last_successful_analysis_date' не найдена в конфиге. Анализ начнется с минимальной даты (1 год назад).")
        start_date_naive = datetime.now() - timedelta(days=365)
        return pytz.timezone('Asia/Tashkent').localize(start_date_naive)
    except FileNotFoundError:
        logging.error(f"Конфигурационный файл {CONFIG_FILE} не найден. Анализ начнется с минимальной даты (1 год назад).")
        start_date_naive = datetime.now() - timedelta(days=365)
        return pytz.timezone('Asia/Tashkent').localize(start_date_naive)
    except Exception as e:
        logging.error(f"Ошибка при чтении конфига: {e}. Анализ начнется с минимальной даты (1 год назад).")
        start_date_naive = datetime.now() - timedelta(days=365)
        return pytz.timezone('Asia/Tashkent').localize(start_date_naive)

def save_analysis_date(date_to_save: datetime):
    """Сохраняет текущую дату (минус 1 день, чтобы избежать пропусков) как начальную для следующего запуска."""
    
    # Откатываемся на 1 день, чтобы захватить сессии, которые могли завершиться поздно
    save_date_naive = date_to_save - timedelta(days=1)
    date_str = save_date_naive.strftime('%Y-%m-%d')
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}

    config["last_successful_analysis_date"] = date_str
    
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logging.info(f"Дата для следующего анализа успешно сохранена: {date_str}")
    except Exception as e:
        logging.error(f"Ошибка при записи в конфигурационный файл {CONFIG_FILE}: {e}")


def get_db_connection():
    """Устанавливает соединение с PostgreSQL."""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST1"),
            port=os.getenv("DB_PORT")
        )
        return conn
    except psycopg2.OperationalError as e:
        logging.error(f"Ошибка подключения к базе данных: {e}")
        raise

def fetch_new_chat_sessions(conn, start_date: datetime):
    """
    Извлекает сессии, начавшиеся не ранее start_date, и которых нет в ANALYTICS_TABLE, 
    используя LEFT JOIN для надежного исключения.
    """
    sessions = {}
    try:
        with conn.cursor() as cur:
            
            # --- Самый надежный запрос с LEFT JOIN для исключения записей ---
            sql_query = f"""
                WITH session_starts AS (
                    SELECT 
                        chat_id, 
                        MIN("timestamp") as session_start_time
                    FROM interactions
                    GROUP BY chat_id
                    HAVING 
                        -- Условие даты: сессия должна начаться после указанной даты
                        MIN("timestamp") >= %s
                )
                SELECT
                    i.chat_id, i.message, i.response, i."timestamp", i.escalation
                FROM
                    interactions AS i
                INNER JOIN
                    session_starts AS ss ON i.chat_id = ss.chat_id
                -- LEFT JOIN для поиска сессий, которые ЕЩЕ НЕ ПРОАНАЛИЗИРОВАНЫ
                LEFT JOIN
                    {ANALYTICS_TABLE} AS sa ON i.chat_id = sa.chat_id
                WHERE
                    -- Исключаем все сессии, которые уже есть в ANALYTICS_TABLE
                    sa.chat_id IS NULL
                ORDER BY
                    i.chat_id, i."timestamp";
            """
            
            # Передаем aware-объект даты в запрос
            cur.execute(sql_query, (start_date,))

            for row in cur.fetchall():
                chat_id, message, response, timestamp, escalation = row
                if chat_id not in sessions:
                    sessions[chat_id] = { 'messages_text': [], 'timestamps': [], 'escalations': [] }
                
                # Добавляем только осмысленные части диалога
                session_message = ""
                if message and message.strip():
                    session_message += f"Пользователь: {message}"
                if response and response.strip():
                    session_message += f"\nБот: {response}"
                    
                if session_message:
                    sessions[chat_id]['messages_text'].append(session_message)
                
                sessions[chat_id]['timestamps'].append(timestamp)
                sessions[chat_id]['escalations'].append(escalation)

        logging.info(f"Найдено {len(sessions)} новых сессий для анализа, удовлетворяющих условию даты.")
        return sessions
    except Exception as e:
        logging.error(f"Ошибка при извлечении новых сессий из БД: {e}")
        return {}
        
def _clamp_score(value, *, allowed=(1, 2, 3), default=1) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return default
    if score not in allowed:
        return default
    return score


def _build_qc_from_scores(payload: dict, raw_json: str) -> dict:
    k_rel = _clamp_score(payload.get("K_РЕЛ"))
    k_chp = _clamp_score(payload.get("K_ЧП"))
    k_pov = _clamp_score(payload.get("K_ПОВ"))
    k_neg = _clamp_score(payload.get("K_НЕГ"))
    k_povt = _clamp_score(payload.get("K_ПОВТ"), allowed=(1, 3), default=3)

    total_score = (
        k_rel * WEIGHTS["K_РЕЛ"]
        + k_povt * WEIGHTS["K_ПОВТ"]
        + k_chp * WEIGHTS["K_ЧП"]
        + k_pov * WEIGHTS["K_ПОВ"]
        + k_neg * WEIGHTS["K_НЕГ"]
    )
    return {
        "Q_BOT_INDEX": round(total_score / TOTAL_WEIGHT, 2),
        "K_РЕЛ": k_rel,
        "K_ЧП": k_chp,
        "K_ПОВ": k_pov,
        "K_НЕГ": k_neg,
        "K_ПОВТ": k_povt,
        "RAW_DATA": raw_json,
    }


def _failed_analysis(error: Exception) -> tuple:
    classification = {
        "Категория": "Ошибка OpenAI",
        "Подкатегория": type(error).__name__,
        "Тематика": str(error),
    }
    qc_result = {
        "Q_BOT_INDEX": 1.00,
        "K_РЕЛ": 1,
        "K_ЧП": 1,
        "K_ПОВ": 1,
        "K_НЕГ": 1,
        "K_ПОВТ": 1,
        "RAW_DATA": json.dumps({"error": str(error), "note": "analyze_session failed"}, ensure_ascii=False),
    }
    return classification, qc_result


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((RateLimitError, APIError)),
    reraise=True,
)
def analyze_session_with_openai(full_dialogue: str) -> tuple:
    """
    Один вызов OpenAI: классификация + QC.
    Схема и критерии — в system (кэшируемый префикс); в user — только диалог.
    """
    try:
        response = client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Проанализируй диалог: классифицируй обращение и оцени QC.\n"
                        f"---\n{full_dialogue}\n---"
                    ),
                },
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        payload = json.loads(raw)

        classification = {
            "Категория": payload.get("Категория") or "Не определено",
            "Подкатегория": payload.get("Подкатегория") or "Не определено",
            "Тематика": payload.get("Тематика") or "Не определено",
        }
        qc_result = _build_qc_from_scores(payload, raw)
        return classification, qc_result
    except (RateLimitError, APIError):
        raise
    except Exception as e:
        logging.error(f"Ошибка API OpenAI при analyze_session: {e}")
        return _failed_analysis(e)


def process_single_session_qc(chat_id, data):
    """
    Обработка одной сессии (классификация + QC одним LLM-вызовом).
    Выполняется в потоках.
    """
    full_dialogue = "\n---\n".join(data["messages_text"])

    start_time = data["timestamps"][0]
    end_time = data["timestamps"][-1]
    duration = int((end_time - start_time).total_seconds())
    msg_count = len(data["messages_text"]) * 2
    is_session_escalated = any(data["escalations"])

    classification, qc_result = analyze_session_with_openai(full_dialogue)

    return {
        "chat_id": chat_id,
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "msg_count": msg_count,
        "is_escalated": is_session_escalated,
        "classification": classification,
        "qc_result": qc_result,
    }

def save_pending_results(conn, results_list):
    """
    Серийно записывает результаты в БД (INSERT).
    """
    if not conn:
        logging.error("Соединение с БД недоступно. Невозможно сохранить данные.")
        return 0
        
    saved_count = 0
    logging.info(f"Начало записи {len(results_list)} результатов в БД.")
    
    try:
        with conn.cursor() as cur:
            for result in results_list:
                classification = result['classification']
                qc_result = result['qc_result']

                cur.execute(f"""
                    INSERT INTO {ANALYTICS_TABLE} (
                        chat_id, session_start_time, session_end_time, session_duration_seconds,
                        message_count, category, subcategory, theme, is_escalated,
                        q_bot_index, k_relevance, k_completeness, k_client_behavior, 
                        k_negative_lexicon, k_repeat_session, raw_qc_data
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    result['chat_id'], result['start_time'], result['end_time'], result['duration'],
                    result['msg_count'], classification.get('Категория'),
                    classification.get('Подкатегория'), classification.get('Тематика'),
                    result['is_escalated'],
                    # QC Metrics
                    qc_result["Q_BOT_INDEX"],
                    qc_result["K_РЕЛ"],
                    qc_result["K_ЧП"],
                    qc_result["K_ПОВ"],
                    qc_result["K_НЕГ"],
                    qc_result["K_ПОВТ"],
                    qc_result["RAW_DATA"]
                ))
                saved_count += 1
        
        conn.commit()
        logging.info(f"Успешно сохранено и зафиксировано {saved_count} записей.")
        return saved_count
        
    except Exception as e:
        logging.error(f"Ошибка при сохранении данных в БД. Откат транзакции: {e}")
        conn.rollback()
        return 0

# --- 4. Основной блок исполнения ---
def main():
    """Главная функция для анализа, QC и записи в БД с параллельной обработкой."""
    global global_conn
    
    try:
        global_conn = get_db_connection()
        
        # 0. Чтение даты начала анализа из config-файла
        start_date = load_start_date()
        
        # Запоминаем текущую дату, чтобы сохранить ее в конфиг после успешного выполнения
        current_date_for_save = datetime.now()
        
        with global_conn.cursor() as cur:
            # Проверяем, сколько сессий уже проанализировано (только для логгирования)
            cur.execute(f"SELECT chat_id FROM {ANALYTICS_TABLE}")
            existing_ids_count = len(cur.fetchall())
            logging.info(f"Найдено {existing_ids_count} уже проанализированных сессий в {ANALYTICS_TABLE}. Они будут пропущены на этапе выборки.")

        # Получаем только новые сессии, начавшиеся после start_date
        sessions_to_process = fetch_new_chat_sessions(global_conn, start_date)

        if not sessions_to_process:
            logging.info("Новых сессий для анализа и QC нет. Работа завершена.")
            return

        logging.info(
            f"Начало анализа (classify+QC, 1 LLM/сессия) с {MAX_WORKERS} потоками. "
            f"Модель={ANALYSIS_MODEL}. Всего новых сессий: {len(sessions_to_process)}"
        )
        start_time_total = time.time()
        
        results_chunk = []
        processed_count = 0
        
        # --- ПАРАЛЛЕЛЬНАЯ ОБРАБОТКА ---
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_chat_id = {
                executor.submit(process_single_session_qc, chat_id, data): chat_id
                for chat_id, data in sessions_to_process.items()
            }
            
            for future in concurrent.futures.as_completed(future_to_chat_id):
                chat_id = future_to_chat_id[future]
                
                try:
                    result = future.result()
                    if result is not None:
                        results_chunk.append(result)
                        processed_count += 1
                        logging.info(f"Обработана сессия {processed_count}/{len(sessions_to_process)}: chat_id={chat_id}, Индекс QC={result['qc_result']['Q_BOT_INDEX']}")
                    
                    if len(results_chunk) >= CHUNK_SIZE:
                        # Серийная запись и COMMIT в БД (главный поток)
                        save_pending_results(global_conn, results_chunk)
                        results_chunk = [] # Очищаем пачку
                        
                except Exception as exc:
                    logging.error(f"Сессия {chat_id} вызвала исключение: {exc}")

        # --- ФИНАЛЬНАЯ ЗАПИСЬ ОСТАТКОВ ---
        if results_chunk:
            save_pending_results(global_conn, results_chunk)
            
        end_time_total = time.time()
        logging.info(f"Анализ завершен. Общее время выполнения: {end_time_total - start_time_total:.2f} секунд с {MAX_WORKERS} потоками. Обработано записей: {processed_count}")
        
        # 4. Сохранение даты для следующего запуска (ТОЛЬКО после успешного коммита)
        save_analysis_date(current_date_for_save)
        
    except Exception as e:
        logging.error(f"Произошла критическая ошибка в main: {e}")
        if global_conn:
            global_conn.rollback()
        logging.warning("Дата для следующего анализа НЕ была сохранена из-за критической ошибки.")
    finally:
        if global_conn:
            global_conn.close()

if __name__ == "__main__":
    main()
