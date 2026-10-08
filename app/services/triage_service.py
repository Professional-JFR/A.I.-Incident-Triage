"""Triage orchestration: classify, detect duplicates, suggest a runbook, persist."""

import logging

from app.exceptions import TriageDecisionError
from app.metrics import DUPLICATES_DETECTED, INCIDENTS_TRIAGED, TRIAGE_DECISIONS
from app.schemas import IncidentIn, TriageOut
from app.services import incident_service
from app.triage import find_duplicate, predict_severity, suggest_runbook

logger = logging.getLogger("app.triage_service")


def triage_incident(payload: IncidentIn, recent_limit: int = 50) -> TriageOut:
    """Triage and persist an incident.

    Args:
        payload: Validated incident details.
        recent_limit: Number of recent incidents to compare for duplicates.

    Returns:
        Severity, duplicate ID, runbook, status, and the new incident ID.

    Raises:
        psycopg.Error: If database reads or writes fail.
        TriageDecisionError: If the triage rules fail unexpectedly.

    Example:
        >>> triage_incident(IncidentIn(title="API timeout", description="slow"))  # doctest: +SKIP
    """
    recent = incident_service.fetch_recent_incidents(limit=recent_limit)
    try:
        severity = predict_severity(payload.title, payload.description)
        duplicate_of = find_duplicate(payload.title, payload.description, recent)
        runbook = suggest_runbook(payload.title, payload.description, payload.service)
    except Exception as exc:
        logger.exception("Triage rules failed", extra={"event": "triage.failed"})
        raise TriageDecisionError(details={"error_type": type(exc).__name__}) from exc

    TRIAGE_DECISIONS.labels(severity=severity).inc()
    INCIDENTS_TRIAGED.inc()
    if duplicate_of is not None:
        DUPLICATES_DETECTED.inc()
    logger.info(
        "Incident triage decision made",
        extra={
            "event": "triage.decision",
            "predicted_severity": severity,
            "duplicate_found": duplicate_of is not None,
            "duplicate_of": duplicate_of,
            "recent_incidents_compared": len(recent),
            "runbook_suggestion": runbook,
        },
    )
    record = incident_service.create_incident_record(
        title=payload.title,
        description=payload.description,
        service=payload.service,
        source=payload.source,
        occurred_at=payload.timestamp,
        predicted_severity=severity,
        duplicate_of=duplicate_of,
        runbook_suggestion=runbook,
    )
    return TriageOut(
        incident_id=record["incident_id"],
        predicted_severity=record["predicted_severity"],
        duplicate_of=record["duplicate_of"],
        runbook_suggestion=record["runbook_suggestion"],
        status=record["status"],
    )
