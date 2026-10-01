import logging
import re
from typing import Optional

logger = logging.getLogger("app.triage")

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
DUPLICATE_THRESHOLD = 0.6


def _normalize_text(value: str) -> str:
    """Lowercase text and collapse repeated whitespace."""
    return re.sub(r"\s+", " ", value.lower()).strip()


def _tokens(value: str) -> set[str]:
    """Return unique lowercase alphanumeric tokens from text."""
    return set(re.findall(r"[a-z0-9]+", _normalize_text(value)))


def _contains_keyword(text: str, keyword: str) -> bool:
    """Match a keyword as a whole word or phrase rather than as a substring."""
    pattern = r"(?<![a-z0-9])" + re.escape(keyword).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def predict_severity(title: str, description: str) -> str:
    """Predict severity from the title and description.

    Args:
        title: Short incident summary.
        description: Longer incident details.

    Returns:
        The highest-priority matching severity, or ``"low"`` if none match.
    """
    text = _normalize_text(f"{title} {description}")
    for severity, keywords in SEVERITY_RULES:
        matched_keyword = next(
            (keyword for keyword in sorted(keywords) if _contains_keyword(text, keyword)),
            None,
        )
        if matched_keyword:
            logger.info(
                "Severity rule matched incident text",
                extra={
                    "event": "triage.severity_rule",
                    "predicted_severity": severity,
                    "matched_keyword": matched_keyword,
                },
            )
            return severity
    logger.info(
        "No severity rule matched incident text",
        extra={"event": "triage.severity_default", "predicted_severity": "low"},
    )
    return "low"


def suggest_runbook(title: str, description: str, service: Optional[str]) -> str:
    """Recommend a runbook based on incident context.

    Args:
        title: Short incident summary.
        description: Longer incident details.
        service: Optional name of the affected service.

    Returns:
        The first matching runbook suggestion, or the default checklist.
    """
    text = _normalize_text(f"{service or ''} {title} {description}")
    for keyword, runbook in RUNBOOK_RULES.items():
        if _contains_keyword(text, keyword):
            logger.info(
                "Runbook rule matched incident context",
                extra={
                    "event": "triage.runbook_rule",
                    "runbook_suggestion": runbook,
                    "matched_keyword": keyword,
                },
            )
            return runbook
    logger.info(
        "No runbook rule matched incident context",
        extra={"event": "triage.runbook_default", "runbook_suggestion": DEFAULT_RUNBOOK},
    )
    return DEFAULT_RUNBOOK


def find_duplicate(title: str, description: str, recent_incidents: list[dict]) -> Optional[str]:
    """Return a similar recent incident when token overlap crosses the threshold.

    Args:
        title: Short summary of the new incident.
        description: Details of the new incident.
        recent_incidents: Existing incidents with IDs, titles, and descriptions.

    Returns:
        The matching incident ID, or ``None`` if no candidate is similar enough.
    """
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

    duplicate_id = best_id if best_score >= DUPLICATE_THRESHOLD else None
    logger.info(
        "Duplicate detection completed",
        extra={
            "event": "triage.duplicate_detection",
            "duplicate_of": duplicate_id,
            "similarity_score": round(best_score, 4),
            "threshold": DUPLICATE_THRESHOLD,
        },
    )
    return duplicate_id
