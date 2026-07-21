import re
from typing import Optional

SEVERITY_RULES = [
    ("critical", {"outage", "down", "data loss", "breach", "sev1", "p0"}),
    ("high", {"error spike", "latency spike", "degraded", "timeout", "sev2", "p1"}),
    ("medium", {"slow", "warning", "retry", "sev3", "p2"}),
]

RUNBOOK_RULES = {
    "database": "RB-001: Check database health, connections, and slow queries",
    "db": "RB-001: Check database health, connections, and slow queries",
    "payment": "RB-002: Validate payment gateway and API error rates",
    "api": "RB-003: Investigate API latency and error budgets",
    "redis": "RB-004: Verify Redis memory, eviction, and connectivity",
    "auth": "RB-005: Check authentication provider and token validation",
}

DEFAULT_RUNBOOK = "RB-000: General incident triage checklist"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _normalize_text(value)))


def predict_severity(title: str, description: str) -> str:
    text = _normalize_text(f"{title} {description}")
    for severity, keywords in SEVERITY_RULES:
        if any(keyword in text for keyword in keywords):
            return severity
    return "low"


def suggest_runbook(title: str, description: str, service: Optional[str]) -> str:
    text = _normalize_text(f"{service or ''} {title} {description}")
    for keyword, runbook in RUNBOOK_RULES.items():
        if keyword in text:
            return runbook
    return DEFAULT_RUNBOOK


def find_duplicate(title: str, description: str, recent_incidents: list[dict]) -> Optional[str]:
    incoming = _tokens(f"{title} {description}")
    if not incoming:
        return None

    best_id = None
    best_score = 0.0
    for incident in recent_incidents:
        existing = _tokens(f"{incident.get('title', '')} {incident.get('description', '')}")
        if not existing:
            continue
        overlap = len(incoming & existing)
        union = len(incoming | existing)
        score = overlap / union if union else 0.0
        if score > best_score:
            best_score = score
            best_id = incident.get("incident_id")

    return best_id if best_score >= 0.6 else None
