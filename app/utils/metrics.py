"""Метрики Prometheus (ТЗ Б.7 app/utils/metrics.py)."""
from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter(
    "requests_total", "Total number of requests", ["endpoint"]
)
REQUEST_LATENCY = Histogram(
    "request_latency_seconds", "Request latency in seconds", ["endpoint"]
)
ESCALATION_COUNT = Counter("escalations_total", "Total number of escalations")
HIGH_LOAD_ESCALATIONS_TOTAL = Counter(
    "high_load_escalations_total", "Total number of high load escalations"
)
UNIQUE_CHATS_TOTAL = Counter(
    "unique_chats_total", "Total number of unique chat IDs ever interacted"
)
ACTIVE_CHATS = Gauge("active_chats", "Number of currently active chat IDs")
SESSIONS_TOTAL = Counter("sessions_total", "Total number of sessions completed")
SESSION_DURATION = Histogram(
    "session_duration_seconds",
    "Duration of a session in seconds",
    buckets=[30, 60, 120, 180, 300, 600, 900, 1800, 3600, 7200, float("inf")],
)
CLASSIFICATIONS_COUNT = Counter(
    "classifications_total",
    "Total number of classifications",
    ["theme", "category", "subcategory"],
)
SYNONYMS_USAGE_COUNT = Counter(
    "synonyms_usage_total",
    "Total number of synonym replacements",
    ["language", "standard_term"],
)
LANGUAGE_DETECTION_COUNT = Counter(
    "language_detection_total",
    "Total number of language detections",
    ["method", "detected_language"],
)
LANGUAGE_DETECTION_CACHE_HIT = Counter(
    "language_detection_cache_hits_total",
    "Number of cache hits for language detection",
)
LANGUAGE_DETECTION_LATENCY = Histogram(
    "language_detection_latency_seconds",
    "Language detection latency in seconds",
    ["method"],
)
LANGUAGE_DETECTION_ERRORS = Counter(
    "language_detection_errors_total",
    "Number of language detection errors",
    ["method", "error_type"],
)
UNCERTAIN_REQUESTS = Counter(
    "uncertain_requests_total", "Total number of uncertain requests"
)
AGENT_TOOL_CALLS = Counter(
    "agent_tool_calls_total", "Agent tool invocations", ["tool_name"]
)
AGENT_CLARIFICATIONS = Counter(
    "agent_clarifications_total", "Agent clarification questions"
)
AGENT_STEPS = Counter(
    "agent_steps_total", "Agent loop iterations"
)
