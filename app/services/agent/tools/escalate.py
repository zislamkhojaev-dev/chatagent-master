"""Tool: escalate to operator."""
from typing import Any, Dict, List

from app.services.escalation import EscalationResult, perform_escalation


async def execute_escalate(
    reason: str,
    *,
    redis_client: Any,
    chat_id: str,
    language: str,
    messages: List[dict],
    agent_state: dict,
    classification: dict,
) -> EscalationResult:
    return await perform_escalation(
        redis_client,
        chat_id,
        language,
        reason,
        messages=messages,
        agent_state=agent_state,
        classification=classification,
        manual=False,
        increment_count=True,
    )
