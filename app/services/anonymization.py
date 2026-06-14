"""Анонимизация PII в сообщениях (ТЗ Б.7 app/services/anonymization.py)."""
import logging
import re

logger = logging.getLogger(__name__)

PII_PATTERNS = [
    {
        "entity": "PHONE_NUMBER",
        "regex": r"\+998\s?(?:90|91|93|94|95|97|98|99|33|88)\s?\d{3}\s?\d{2}\s?\d{2}",
        "replacement": "***",
    },
    {"entity": "PASSPORT", "regex": r"[A-Z]{2}\d{7}", "replacement": "***"},
    {
        "entity": "CREDIT_CARD",
        "regex": r"\b\d{4}\s?-?\d{4}\s?-?\d{4}\s?-?\d{4}\b",
        "replacement": "***",
    },
    {"entity": "UZBEK_PINFL", "regex": r"\b\d{14}\b", "replacement": "***"},
    {
        "entity": "EMAIL_ADDRESS",
        "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "replacement": "***",
    },
]


def anonymize_message(message: str) -> str:
    anonymized_text = message
    for pattern in PII_PATTERNS:
        matches = re.findall(pattern["regex"], anonymized_text)
        if matches:
            logger.info("Found %s: %s", pattern["entity"], matches)
            anonymized_text = re.sub(
                pattern["regex"], pattern["replacement"], anonymized_text
            )
    if anonymized_text != message:
        logger.info("Anonymized message: %s...", anonymized_text[:50])
    else:
        logger.info("No PII found in message")
    return anonymized_text
