"""Pydantic-модели запросов и ответов API (ТЗ Б.7)."""
from typing import Dict, List, Optional

from pydantic import BaseModel


class MessageRequest(BaseModel):
    """Запрос на обработку сообщения. Опционально language (Б.3)."""
    chat_id: str
    message: str
    language: Optional[str] = None  # "uz" | "ru" — явная установка языка, без детекции


class MessageResponse(BaseModel):
    status: str
    chat_id: str
    response: str
    classification: Dict[str, str]
    escalation: bool
    history: List[str] = []
    mode: Optional[str] = None
    tools_used: List[str] = []
    escalation_summary: Optional[str] = None
    escalation_reason: Optional[str] = None


class EscalateRequest(BaseModel):
    chat_id: str


class EscalateResponse(BaseModel):
    status: str
    chat_id: str
    response: str
    history: List[str]
    escalation_summary: Optional[str] = None
    escalation_reason: Optional[str] = None
