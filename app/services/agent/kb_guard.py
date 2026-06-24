"""Правила допуска ответа только при подтверждённом контексте KB."""


def probe_has_relevant_context(probe_status: str) -> bool:
    """Ответ из KB разрешён только если retrieval-first probe вернул sufficient."""
    return probe_status == "sufficient"
