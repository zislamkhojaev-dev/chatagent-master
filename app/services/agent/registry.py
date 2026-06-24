"""OpenAI tool schemas for the agent."""
from typing import List

TOOL_SCHEMAS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Search the Paynet knowledge base for relevant information. "
                "Use after clarifying the user's issue. Pass a specific search query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific search query enriched with context (user type, channel, issue).",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_operator",
            "description": (
                "Escalate the conversation to a human operator when KB has no answer, "
                "max clarifications reached, or issue requires human handling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": [
                            "no_kb_match",
                            "max_clarifications",
                            "user_request",
                            "out_of_scope",
                        ],
                        "description": "Why escalation is needed.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]
