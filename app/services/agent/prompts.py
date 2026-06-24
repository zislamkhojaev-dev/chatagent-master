"""System prompts for the support agent."""
from typing import List, Optional

from app.services.scenarios import Scenario, get_missing_slots, get_slot_question


def build_system_prompt(
    language: str,
    scenario: Optional[Scenario],
    agent_state: dict,
    chat_context: Optional[str] = None,
) -> str:
    lang_block = (
        "MUHIM: FAQAT O'ZBEK TILIDA VA FAQAT LOTIN YOZUVIDA javob bering!"
        if language == "uz"
        else "Отвечай на русском языке, используя кириллицу."
    )
    clarify_block = ""
    if scenario:
        missing = get_missing_slots(scenario, agent_state)
        if missing:
            q = get_slot_question(missing[0], language)
            clarify_block = (
                f"\nАКТИВНЫЙ СЦЕНАРИЙ: {scenario.id}\n"
                f"ОБЯЗАТЕЛЬНО задай уточняющий вопрос (не вызывай search_knowledge_base): {q}\n"
            )

    context_block = f"\nКонтекст диалога: {chat_context}\n" if chat_context else ""
    slots_block = ""
    if agent_state.get("slots"):
        slots_block = f"\nИзвестные факты: {agent_state['slots']}\n"

    if language == "uz":
        return (
            "Siz Paynet qo'llab-quvvatlash agentisiz.\n"
            f"{lang_block}\n"
            "QOIDALAR:\n"
            "1. Avval foydalanuvchi muammosini aniqlang\n"
            "2. Agar required_slots to'ldirilmagan bo'lsa — aniqlashtiruvchi savol bering, RAG chaqirmang\n"
            "3. Avval search_knowledge_base orqali KB ni tekshiring; ma'lumot yetarli bo'lsa — javob bering\n"
            "4. Faqat KB natijalari ziddiyatli bo'lsa yoki yetarli bo'lmasa — aniqlashtiruvchi savol bering\n"
            "5. FAQAT KB ma'lumotlaridan foydalaning, o'ylab topmang\n"
            "6. KB da javob yo'q bo'lsa — escalate_to_operator chaqiring, erkin javob yozmang\n"
            "7. Muloyim va aniq bo'ling\n"
            f"{clarify_block}{context_block}{slots_block}"
        )
    return (
        "Ты — агент службы поддержки Paynet.\n"
        f"{lang_block}\n"
        "ПРАВИЛА:\n"
        "1. Сначала пойми проблему пользователя\n"
        "2. Если required_slots не заполнены — задай уточняющий вопрос, НЕ вызывай search_knowledge_base\n"
        "3. Сначала проверь базу знаний через search_knowledge_base; если данных достаточно — отвечай\n"
        "4. Уточняющий вопрос задавай только если KB вернула конфликт или недостаточно данных\n"
        "5. Используй ТОЛЬКО информацию из KB, не выдумывай\n"
        "6. Если в KB нет ответа — вызови escalate_to_operator, НЕ пиши свободный ответ пользователю\n"
        "7. Будь вежливым и конкретным\n"
        f"{clarify_block}{context_block}{slots_block}"
    )


def messages_to_openai(messages: List[dict]) -> List[dict]:
    result = []
    for msg in messages:
        role = msg.get("role", "user")
        if role in ("user", "assistant"):
            result.append({"role": role, "content": msg.get("content", "")})
    return result
