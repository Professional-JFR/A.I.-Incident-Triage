import logging
import os
import time

import psycopg
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.logger import configure_logging
from app.schemas import IncidentIn, TriageOut
from app.storage import (
    create_incident_record,
    fetch_recent_incidents,
    get_incident,
    init_storage,
    ping_db,
    ping_redis,
)
from app.triage import find_duplicate, predict_severity, suggest_runbook

logger = configure_logging()
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "1048576"))
RECENT_INCIDENT_LIMIT = 50

app = FastAPI(title="A.I. Incident Triage Copilot", version="0.1.0")


class RequestTooLarge(Exception):
    """Raised when a streamed request body exceeds the configured size limit."""


class RequestSizeLimitMiddleware:
    """Reject oversized HTTP request bodies before passing them to the API."""

    def __init__(self, app, max_request_bytes: int) -> None:
        """Store the wrapped app and maximum permitted request-body size."""
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope, receive, send) -> None:
        """Enforce the body-size limit for HTTP requests."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        try:
            declared_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared_length = 0
        if declared_length > self.max_request_bytes:
            response = JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "detail": {
                        "code": "request_too_large",
                        "message": "Request body exceeds the configured size limit.",
                    }
                },
            )
            await response(scope, receive, send)
            return

        received_bytes = 0
        response_started = False

        async def limited_receive():
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_request_bytes:
                    raise RequestTooLarge
            return message

        async def track_response_start(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, track_response_start)
        except RequestTooLarge:
            if not response_started:
                response = JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "detail": {
                            "code": "request_too_large",
                            "message": "Request body exceeds the configured size limit.",
                        }
                    },
                )
                await response(scope, receive, send)


app.add_middleware(RequestSizeLimitMiddleware, max_request_bytes=MAX_REQUEST_BYTES)


@app.middleware("http")
async def log_requests_and_set_security_headers(request: Request, call_next):
    """Log request outcomes and add baseline security headers to responses.

    Args:
        request: Incoming HTTP request.
        call_next: ASGI middleware callback for the next application layer.

    Returns:
        The response with security headers applied.
    """
    started_at = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Request failed before a response was produced",
            extra={"event": "http.request_failed", "method": request.method, "path": request.url.path},
        )
        raise

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        "HTTP request completed",
        extra={
            "event": "http.request",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
        },
    )
    return response


@app.on_event("startup")
def startup_event() -> None:
    if os.getenv("SKIP_STARTUP_INIT", "").lower() in {"1", "true", "yes"}:
        logger.info("Storage initialization skipped", extra={"event": "storage.startup_skipped"})
        return
    logger.info("Initializing storage", extra={"event": "storage.startup"})
    init_storage()


@app.get("/health")
def health() -> dict:
    """Return a liveness response without checking external dependencies.

    Returns:
        A small JSON object indicating that the API process is running.
    """
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    """Report database readiness and Redis cache availability.

    Returns:
        Dependency status; Redis degradation does not prevent readiness.

    Raises:
        HTTPException: If PostgreSQL is unavailable.
    """
    db_ok = ping_db()
    redis_ok = ping_redis()
    if db_ok:
        return {
            "status": "ready",
            "database": "ok",
            "redis": "ok" if redis_ok else "degraded",
        }

    detail = {
        "status": "not_ready",
        "database": "error",
        "redis": "ok" if redis_ok else "degraded",
    }
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


@app.exception_handler(psycopg.Error)
async def handle_database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
    """Return a stable service-unavailable error when PostgreSQL fails.

    Args:
        request: Request that triggered the storage operation.
        exc: PostgreSQL error raised by storage access.

    Returns:
        A generic error response that does not expose database details.
    """
    logger.error(
        "Database operation failed",
        extra={"event": "storage.request_failed", "error_type": type(exc).__name__},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": {
                "code": "database_unavailable",
                "message": "Incident storage is temporarily unavailable.",
            }
        },
    )


@app.post("/incidents/triage", response_model=TriageOut)
def triage_incident(payload: IncidentIn) -> TriageOut:
    """Classify, match, persist, and return a newly submitted incident.

    Args:
        payload: Validated incident details from the request body.

    Returns:
        The resulting severity, duplicate ID, runbook, and incident ID.

    Raises:
        psycopg.Error: If required database reads or persistence fail.
    """
    recent = fetch_recent_incidents(limit=RECENT_INCIDENT_LIMIT)

    predicted_severity = predict_severity(payload.title, payload.description)
    duplicate_of = find_duplicate(payload.title, payload.description, recent)
    runbook = suggest_runbook(payload.title, payload.description, payload.service)
    logger.info(
        "Incident triage decision made",
        extra={
            "event": "triage.decision",
            "predicted_severity": predicted_severity,
            "duplicate_found": duplicate_of is not None,
            "runbook_suggestion": runbook,
        },
    )

    record = create_incident_record(
        title=payload.title,
        description=payload.description,
        service=payload.service,
        source=payload.source,
        occurred_at=payload.timestamp,
        predicted_severity=predicted_severity,
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


@app.get("/incidents/{incident_id}")
def read_incident(incident_id: str) -> dict:
    """Return a stored incident or a 404 response when it does not exist.

    Args:
        incident_id: ID of the incident to retrieve.

    Returns:
        The incident data.

    Raises:
        HTTPException: If there is no incident with the requested ID.
        psycopg.Error: If PostgreSQL cannot retrieve the incident.
    """
    record = get_incident(incident_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return record
