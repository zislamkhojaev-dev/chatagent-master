"""Agent orchestrator with retrieval-first KB probe and function calling."""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
from openai import APIError, RateLimitError
from redis.asyncio import Redis
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.dependencies import get_openai_client
from app.services.agent.prompts import build_system_prompt, messages_to_openai
from app.services.agent.registry import TOOL_SCHEMAS
from app.services.agent.tools.escalate import execute_escalate
from app.services.agent.tools.search_kb import (
    execute_search_kb,
    format_kb_result_for_llm,
)
from app.services.context import get_chat_context
from app.services.kb_metadata import MetadataFilter, build_metadata_filter
from app.services.scenarios import (
    Scenario,
    build_search_query,
    extract_slot_value,
    get_missing_slots,
    get_slot_question,
    get_scenarios_config,
)
from app.services.scenario_router import resolve_scenario
from app.services.session import get_agent_state, save_agent_state
from app.utils.metrics import AGENT_CLARIFICATIONS, AGENT_STEPS, AGENT_TOOL_CALLS

from app.services.agent.kb_guard import probe_has_relevant_context

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    chat_id: str
    message: str
    language: str
    messages: List[dict]
    classification: Dict[str, str]
    app_state: Any
    redis_client: Redis
    query_embedding: Optional[List[float]] = None


@dataclass
class AgentResult:
    text: str
    escalation: bool = False
    status: str = "success"
    mode: str = "answering"
    tools_used: List[str] = field(default_factory=list)
    tokens: int = 0
    escalation_summary: Optional[str] = None
    escalation_reason: Optional[str] = None


def _apply_uz_postprocess(text: str) -> str:
    replacements = {
        "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
        "Ў": "O'", "Қ": "Q", "Ғ": "G'", "Ҳ": "H",
    }
    for cyr, lat in replacements.items():
        text = text.replace(cyr, lat)
    return text


def _update_slots_from_message(scenario: Optional[Scenario], agent_state: dict, message: str) -> dict:
    """Заполняет первый missing-слот только при распознанном значении."""
    if not scenario:
        return agent_state
    missing = get_missing_slots(scenario, agent_state)
    if not missing:
        return agent_state

    slot = missing[0]
    value = extract_slot_value(slot.id, message)
    agent_state = dict(agent_state)
    awaiting = agent_state.get("awaiting_slot")

    if value:
        slots = dict(agent_state.get("slots") or {})
        slots[slot.id] = value
        agent_state["slots"] = slots
        agent_state.pop("awaiting_slot", None)
        if awaiting:
            agent_state["clarification_count"] = int(agent_state.get("clarification_count") or 0) + 1
        return agent_state

    # Ждали ответ на уточнение, но значение не распарсили
    if awaiting == slot.id:
        agent_state["clarification_count"] = int(agent_state.get("clarification_count") or 0) + 1
    return agent_state


def _reset_scenario_state(agent_state: dict, scenario_id: str) -> dict:
    state = dict(agent_state)
    state["scenario_id"] = scenario_id
    state["slots"] = {}
    state["clarification_count"] = 0
    state.pop("awaiting_slot", None)
    return state


def _detect_mode(text: str, tools_used: List[str], escalation: bool, clarified: bool = False) -> str:
    if escalation:
        return "escalation"
    if clarified:
        return "clarifying"
    if "search_knowledge_base" in tools_used:
        return "answering"
    return "answering"


async def _return_escalation(
    ctx: AgentContext,
    agent_state: dict,
    reason: str,
    tools_used: List[str],
    total_tokens: int = 0,
) -> AgentResult:
    esc = await execute_escalate(
        reason,
        redis_client=ctx.redis_client,
        chat_id=ctx.chat_id,
        language=ctx.language,
        messages=ctx.messages,
        agent_state=agent_state,
        classification=ctx.classification,
    )
    await save_agent_state(ctx.redis_client, ctx.chat_id, agent_state)
    return AgentResult(
        text=esc.response,
        escalation=esc.escalation,
        status=esc.status,
        mode="escalation",
        tools_used=tools_used,
        tokens=total_tokens + esc.tokens,
        escalation_summary=esc.escalation_summary,
        escalation_reason=esc.escalation_reason,
    )


async def _probe_kb(
    ctx: AgentContext,
    scenario: Optional[Scenario],
    agent_state: dict,
    metadata_filter: Optional[MetadataFilter],
) -> dict:
    slots = agent_state.get("slots") or {}
    query = build_search_query(scenario, slots, ctx.message) if scenario else ctx.message
    return await execute_search_kb(
        query,
        ctx.language,
        app_state=ctx.app_state,
        redis_client=ctx.redis_client,
        scenario=scenario,
        slots=slots,
        metadata_filter=metadata_filter,
        query_embedding=ctx.query_embedding,
        original_message=ctx.message,
    )


async def _retrieval_first_probe(
    ctx: AgentContext,
    scenario: Optional[Scenario],
    agent_state: dict,
) -> Tuple[dict, str]:
    """
    Retrieval-first: сначала KB с фильтром по умолчанию (client), затем без фильтра.
    Возвращает (kb_result, status): sufficient | ambiguous | insufficient
    """
    slots = agent_state.get("slots") or {}
    default_filter = build_metadata_filter(scenario, slots)
    result = await _probe_kb(ctx, scenario, agent_state, default_filter)

    if result.get("has_relevant_context"):
        return result, "sufficient"
    if result.get("is_ambiguous"):
        return result, "ambiguous"

    broad_filter = MetadataFilter(
        channels=default_filter.channels,
        topics=default_filter.topics,
    )
    result_broad = await _probe_kb(ctx, scenario, agent_state, broad_filter)
    if result_broad.get("has_relevant_context"):
        return result_broad, "sufficient"
    if result_broad.get("is_ambiguous"):
        return result_broad, "ambiguous"

    result_all = await _probe_kb(ctx, scenario, agent_state, MetadataFilter())
    if result_all.get("has_relevant_context"):
        return result_all, "sufficient"
    if result_all.get("is_ambiguous"):
        return result_all, "ambiguous"
    return result_all, "insufficient"


def _should_clarify(
    scenario: Optional[Scenario],
    agent_state: dict,
    probe_status: str,
) -> bool:
    if not scenario or not scenario.required_slots:
        return False
    if not get_missing_slots(scenario, agent_state):
        return False
    policy = getattr(scenario, "clarify_policy", "if_ambiguous") or "if_ambiguous"
    if policy == "never":
        return False
    if policy == "always":
        return True
    # if_ambiguous: уточнять при ambiguous / insufficient (не при уже достаточном probe)
    return probe_status in ("ambiguous", "insufficient")


@retry(
    stop=stop_after_attempt(settings.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=settings.MIN_WAIT, max=settings.MAX_WAIT),
    retry=retry_if_exception_type((APIError, RateLimitError, httpx.TimeoutException)),
)
async def run_agent(ctx: AgentContext) -> AgentResult:
    config = get_scenarios_config()
    agent_state = await get_agent_state(ctx.redis_client, ctx.chat_id)
    scenario_just_matched = False
    scenario = None
    locked_id = agent_state.get("scenario_id")

    resolved = await resolve_scenario(
        ctx.message,
        ctx.language,
        query_embedding=ctx.query_embedding,
        redis_client=ctx.redis_client,
    )

    if locked_id:
        locked_scenario = next((s for s in config.scenarios if s.id == locked_id), None)
        if resolved and resolved.id != locked_id:
            # Смена темы: уверенный новый матч → unlock
            logger.info(
                "Scenario unlock chat_id=%s %s -> %s",
                ctx.chat_id,
                locked_id,
                resolved.id,
            )
            agent_state = _reset_scenario_state(agent_state, resolved.id)
            scenario = resolved
            scenario_just_matched = True
        else:
            scenario = locked_scenario
    elif resolved:
        agent_state = _reset_scenario_state(agent_state, resolved.id)
        scenario = resolved
        scenario_just_matched = True

    # Парсим слоты с первого и последующих сообщений (в т.ч. «я агент» в том же ходе)
    if scenario and get_missing_slots(scenario, agent_state):
        agent_state = _update_slots_from_message(scenario, agent_state, ctx.message)

    tools_used: List[str] = []
    total_tokens = 0

    probe_result, probe_status = await _retrieval_first_probe(ctx, scenario, agent_state)

    if _should_clarify(scenario, agent_state, probe_status):
        max_clar = (scenario.max_clarifications if scenario else None) or config.default_max_clarifications
        clar_count = int(agent_state.get("clarification_count") or 0)
        if clar_count >= max_clar:
            return await _return_escalation(
                ctx, agent_state, "max_clarifications", tools_used,
            )
        missing = get_missing_slots(scenario, agent_state)
        if missing:
            question = get_slot_question(missing[0], ctx.language)
            agent_state["awaiting_slot"] = missing[0].id
            await save_agent_state(ctx.redis_client, ctx.chat_id, agent_state)
            AGENT_CLARIFICATIONS.inc()
            return AgentResult(
                text=_apply_uz_postprocess(question) if ctx.language == "uz" else question,
                mode="clarifying",
                tools_used=tools_used,
            )

    if not probe_has_relevant_context(probe_status):
        logger.info(
            "KB probe insufficient for chat_id=%s status=%s — escalating",
            ctx.chat_id,
            probe_status,
        )
        return await _return_escalation(ctx, agent_state, "no_kb_match", tools_used)

    chat_context = await get_chat_context(ctx.redis_client, ctx.chat_id)
    # Answering with KB: do not inject contradicting «обязательно уточни»
    system_prompt = build_system_prompt(
        ctx.language,
        scenario,
        agent_state,
        chat_context,
        kb_answer_mode=True,
    )

    kb_block = format_kb_result_for_llm(probe_result)
    system_prompt += (
        "\n\nРезультаты поиска в базе знаний (используй для ответа):\n" + kb_block
    )
    tools_used.append("search_knowledge_base")
    AGENT_TOOL_CALLS.labels(tool_name="search_knowledge_base").inc()
    kb_context_confirmed = True

    openai_messages: List[dict] = [{"role": "system", "content": system_prompt}]
    openai_messages.extend(messages_to_openai(ctx.messages))

    client = get_openai_client()

    for step in range(settings.MAX_AGENT_STEPS):
        AGENT_STEPS.inc()
        response = await client.chat.completions.create(
            model=settings.AGENT_MODEL,
            messages=openai_messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            max_tokens=600,
            temperature=settings.AGENT_TEMPERATURE,
        )
        total_tokens += response.usage.total_tokens if response.usage else 0
        choice = response.choices[0]
        message = choice.message

        if message.tool_calls:
            openai_messages.append(message.model_dump(exclude_none=True))
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments or "{}")
                if fn_name not in tools_used:
                    tools_used.append(fn_name)
                AGENT_TOOL_CALLS.labels(tool_name=fn_name).inc()

                if fn_name == "search_knowledge_base":
                    search_query = fn_args.get("query") or build_search_query(
                        scenario, agent_state.get("slots") or {}, ctx.message
                    )
                    filt = build_metadata_filter(scenario, agent_state.get("slots") or {})
                    kb_result = await execute_search_kb(
                        search_query,
                        ctx.language,
                        app_state=ctx.app_state,
                        redis_client=ctx.redis_client,
                        scenario=scenario,
                        slots=agent_state.get("slots") or {},
                        metadata_filter=filt,
                        query_embedding=ctx.query_embedding,
                        original_message=ctx.message,
                    )
                    if not kb_result.get("has_relevant_context"):
                        logger.info(
                            "search_knowledge_base empty for chat_id=%s — escalating",
                            ctx.chat_id,
                        )
                        return await _return_escalation(
                            ctx, agent_state, "no_kb_match", tools_used, total_tokens,
                        )
                    kb_context_confirmed = True
                    tool_content = format_kb_result_for_llm(kb_result)
                elif fn_name == "escalate_to_operator":
                    reason = fn_args.get("reason", "out_of_scope")
                    return await _return_escalation(
                        ctx, agent_state, reason, tools_used, total_tokens,
                    )
                else:
                    tool_content = f"Unknown tool: {fn_name}"

                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_content,
                })
            continue

        if message.content:
            if not kb_context_confirmed:
                logger.warning(
                    "Blocked free-form answer without KB for chat_id=%s — escalating",
                    ctx.chat_id,
                )
                return await _return_escalation(
                    ctx, agent_state, "no_kb_match", tools_used, total_tokens,
                )
            text = message.content.strip()
            if ctx.language == "uz":
                text = _apply_uz_postprocess(text)
            agent_state["tools_used"] = tools_used
            await save_agent_state(ctx.redis_client, ctx.chat_id, agent_state)
            mode = _detect_mode(text, tools_used, False)
            return AgentResult(
                text=text,
                mode=mode,
                tools_used=tools_used,
                tokens=total_tokens,
            )

    return await _return_escalation(
        ctx, agent_state, "out_of_scope", tools_used, total_tokens,
    )
