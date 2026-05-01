"""Alert event normalizer — maps raw event dicts to AlertCreate fields."""

import logging

logger = logging.getLogger(__name__)

TITLE_KEYS = [
    "alertname", "name", "summary", "title", "description",
    "alert_name", "event_name", "rule_name",
]
SEVERITY_KEYS = ["severity", "level", "urgency", "priority", "severity_level"]
SEVERITY_MAP: dict[str, str] = {
    "critical": "critical", "crit": "critical", "p0": "critical", "p1": "critical",
    "severe": "critical", "fatal": "critical", "emergency": "critical",
    "warning": "warning", "warn": "warning", "p2": "warning",
    "major": "warning", "medium": "warning", "moderate": "warning",
    "info": "info", "information": "info", "informational": "info",
    "p3": "info", "p4": "info", "minor": "info", "low": "info",
    "ok": "info", "normal": "info", "clear": "info",
}


def normalize(raw: dict, source_hint: str | None = None) -> dict:
    """Convert a raw event dict into AlertCreate fields."""
    title = _extract_title(raw)
    severity = _extract_severity(raw)
    source = source_hint or raw.get("source", raw.get("system", "unknown"))
    event_id = raw.get("event_id") or raw.get("id") or raw.get("alert_id")

    return {
        "title": title,
        "source": str(source)[:64],
        "severity": severity,
        "raw_event": raw,
        "event_id": str(event_id) if event_id else None,
    }


def _extract_title(raw: dict) -> str:
    for key in TITLE_KEYS:
        val = raw.get(key)
        if val and isinstance(val, str):
            return val[:512]
    for val in raw.values():
        if isinstance(val, str) and len(val) > 3:
            return val[:512]
    return "Unknown Alert"


def _extract_severity(raw: dict) -> str:
    for key in SEVERITY_KEYS:
        val = raw.get(key)
        if val and isinstance(val, str):
            mapped = SEVERITY_MAP.get(val.lower(), "")
            if mapped:
                return mapped
    for key in SEVERITY_KEYS:
        val = raw.get(key)
        if isinstance(val, (int, float)):
            if val <= 2:
                return "critical"
            elif val <= 4:
                return "warning"
            else:
                return "info"
    return "warning"
