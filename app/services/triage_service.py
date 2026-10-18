"""Triage orchestration: classify, detect duplicates, suggest a runbook, persist."""

import logging

from app.config import get_settings
from app.exceptions import TriageDecisionError
from app.metrics import (
    DUPLICATES_DETECTED,
    INCIDENTS_TRIAGED,
    TRIAGE_DECISIONS,
    TRIAGE_PROVIDER_FALLBACKS,
)
from app.providers import LLMProvider, RuleBasedProvider, TriageProvider
from app.schemas import IncidentIn, TriageOut
from app.services import incident_service

logger = logging.getLogger("app.triage_service")


def triage_incident(
    payload: IncidentIn,
    recent_limit: int = 50,
    idempotency_key: str | None = None,
    provider: TriageProvider | None = None,
) -> TriageOut:
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
    if idempotency_key:
        cached = incident_service.get_idempotent_result(idempotency_key)
        if cached is not None:
            try:
                return TriageOut.model_validate(cached)
            except Exception:
                logger.warning(
                    "Invalid cached idempotency result ignored",
                    extra={"event": "idempotency.cache_invalid"},
                )
    recent = incident_service.fetch_recent_incidents(limit=recent_limit)
    try:
        settings = get_settings()
        if provider is None:
            if settings.triage_provider == "llm":
                provider = LLMProvider(
                    endpoint=settings.triage_llm_endpoint or "",
                    model=settings.triage_llm_model,
                    timeout_seconds=settings.triage_llm_timeout_seconds,
                    retries=settings.triage_llm_retries,
                    retry_delay_seconds=settings.triage_llm_retry_delay_seconds,
                    api_key=settings.triage_llm_api_key,
                )
            else:
                provider = RuleBasedProvider()
        try:
            decision = provider.triage(payload, recent)
        except Exception as exc:
            if provider.name != "llm":
                raise
            TRIAGE_PROVIDER_FALLBACKS.labels(provider=provider.name).inc()
            logger.warning(
                "LLM triage failed; falling back to rule-based provider",
                extra={
                    "event": "triage.provider_fallback",
                    "provider": provider.name,
                    "error_type": type(exc).__name__,
                },
            )
            decision = RuleBasedProvider().triage(payload, recent)
    except Exception as exc:
        logger.exception("Triage rules failed", extra={"event": "triage.failed"})
        raise TriageDecisionError(details={"error_type": type(exc).__name__}) from exc

    severity = decision.predicted_severity
    duplicate_of = decision.duplicate_of
    runbook = decision.runbook_suggestion
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
        idempotency_key=idempotency_key,
    )
    result = TriageOut(
        incident_id=record["incident_id"],
        predicted_severity=record["predicted_severity"],
        duplicate_of=record["duplicate_of"],
        runbook_suggestion=record["runbook_suggestion"],
        status=record["status"],
    )
    if idempotency_key:
        incident_service.cache_idempotent_result(idempotency_key, result.model_dump())
    return result
