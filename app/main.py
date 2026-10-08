import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import psycopg
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import get_settings
from app.exceptions import (
    ConfigurationError,
    DatabaseConnectionError,
    TriageException,
)
from app.logger import configure_logging, correlation_id_var
from app.metrics import HTTP_LATENCY, VALIDATION_ERRORS
from app.persistence import cache, database
from app.schemas import (
    ErrorOut,
    HealthOut,
    IncidentDetailOut,
    IncidentIn,
    ReadyOut,
    TriageOut,
)
from app.services import incident_service, triage_service

logger = configure_logging()
CORRELATION_HEADER = "X-Request-ID"
_VALID_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

ERROR_RESPONSES = {
    400: {"model": ErrorOut, "description": "Domain validation failed."},
    403: {"model": ErrorOut, "description": "Forbidden."},
    404: {"model": ErrorOut, "description": "Resource not found."},
    409: {"model": ErrorOut, "description": "Conflicting request."},
    413: {"model": ErrorOut, "description": "Request body exceeds the size limit."},
    500: {"model": ErrorOut, "description": "Unexpected server error."},
    503: {"model": ErrorOut, "description": "PostgreSQL is unavailable."},
}


def run_startup_checks() -> None:
    """Validate configuration and dependencies, logging actionable diagnostics.

    PostgreSQL is critical: failures abort startup. Redis is optional: failures
    only log a warning and the API runs in degraded mode.

    Raises:
        ConfigurationError: If settings are invalid (permanent).
        DatabaseConnectionError: If PostgreSQL is unreachable or misconfigured.
    """
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        logger.error(
            "Configuration validation failed",
            extra={"event": "startup.config_invalid", "details": exc.details},
        )
        raise
    logger.setLevel(settings.log_level)
    logger.info(
        "Configuration validated",
        extra={
            "event": "startup.config_valid",
            "postgres_host": settings.postgres_host,
            "postgres_port": settings.postgres_port,
            "postgres_db": settings.postgres_db,
            "log_level": settings.log_level,
        },
    )
    if settings.skip_startup_init:
        logger.info("Storage initialization skipped", extra={"event": "storage.startup_skipped"})
        return

    try:
        database.wait_for_database()
    except DatabaseConnectionError as exc:
        logger.error(
            exc.message,
            extra={
                "event": "startup.database_failed",
                "failure_kind": "transient_exhausted" if exc.transient else "permanent",
                "details": exc.details,
            },
        )
        raise
    if not database.schema_ready():
        message = "Database schema is missing; run 'alembic upgrade head' before starting the API."
        logger.error(message, extra={"event": "startup.schema_missing"})
        raise DatabaseConnectionError(message, transient=False)
    logger.info("PostgreSQL connection verified", extra={"event": "startup.database_ok"})

    if cache.ping():
        logger.info("Redis connection verified", extra={"event": "startup.cache_ok"})
    else:
        logger.warning(
            "Redis unavailable; running in degraded mode (reads fall back to PostgreSQL)",
            extra={"event": "startup.cache_degraded"},
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Run startup checks and release resources on graceful shutdown."""
    logger.info("Starting application", extra={"event": "startup.begin"})
    run_startup_checks()
    yield
    logger.info("Shutting down application", extra={"event": "shutdown.begin"})
    try:
        cache.close_client()
    except Exception:  # pragma: no cover - best-effort cleanup
        logger.warning("Redis client close failed", extra={"event": "shutdown.cache_close_failed"})
    logger.info("Shutdown complete", extra={"event": "shutdown.complete"})


app = FastAPI(
    title="A.I. Incident Triage Copilot",
    version="0.2.0",
    description="AI-assisted incident triage: severity, duplicate detection, and runbook suggestions.",
    lifespan=lifespan,
)


class RequestTooLarge(Exception):
    """Raised when a streamed request body exceeds the configured size limit."""


class RequestSizeLimitMiddleware:
    """Reject oversized HTTP request bodies before passing them to the API."""

    def __init__(self, app, max_request_bytes: Optional[int] = None) -> None:
        """Store the wrapped app and an optional override for the size limit."""
        self.app = app
        self._max_request_bytes = max_request_bytes

    @property
    def max_request_bytes(self) -> int:
        """Return the configured limit, resolved lazily from settings."""
        if self._max_request_bytes is not None:
            return self._max_request_bytes
        return get_settings().max_request_bytes

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


app.add_middleware(RequestSizeLimitMiddleware)


@app.middleware("http")
async def log_requests_and_set_security_headers(request: Request, call_next):
    """Attach a correlation ID, log request outcomes, record latency, set security headers.

    A valid inbound ``X-Request-ID`` is reused; otherwise one is generated and
    returned in the response header.
    """
    inbound = request.headers.get(CORRELATION_HEADER, "")
    correlation_id = inbound if _VALID_CORRELATION_ID.match(inbound) else uuid.uuid4().hex
    token = correlation_id_var.set(correlation_id)
    started_at = time.monotonic()
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Request failed before a response was produced",
                extra={"event": "http.request_failed", "method": request.method, "path": request.url.path},
            )
            raise

        duration = time.monotonic() - started_at
        route = request.scope.get("route")
        path_label = getattr(route, "path", "unmatched")
        HTTP_LATENCY.labels(
            method=request.method, path=path_label, status=str(response.status_code)
        ).observe(duration)
        response.headers[CORRELATION_HEADER] = correlation_id
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
                "duration_ms": round(duration * 1000, 2),
            },
        )
        return response
    finally:
        correlation_id_var.reset(token)


@app.get("/health", response_model=HealthOut, summary="Liveness check")
def health() -> HealthOut:
    """Return a liveness response without checking external dependencies."""
    return HealthOut(status="ok")


@app.get(
    "/ready",
    response_model=ReadyOut,
    summary="Readiness check",
    responses={503: {"model": ReadyOut, "description": "PostgreSQL (critical) is unavailable."}},
)
def ready() -> ReadyOut:
    """Report database readiness and Redis availability.

    PostgreSQL is critical: its failure returns 503. Redis is optional: its
    failure is reported as ``"redis": "degraded"`` while the API stays ready.
    """
    db_ok = database.ping()
    redis_ok = cache.ping()
    body = {
        "status": "ready" if db_ok else "not_ready",
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "degraded",
    }
    if db_ok:
        return ReadyOut(**body)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=body)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Expose Prometheus metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _error_response(status_code: int, code: str, message: str, details: Optional[dict] = None) -> JSONResponse:
    """Build the structured error envelope."""
    detail: dict = {"code": code, "message": message}
    if details:
        detail["details"] = details
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(psycopg.Error)
async def handle_database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
    """Return a stable 503 when PostgreSQL fails, without exposing database details."""
    logger.error(
        "Database operation failed",
        extra={"event": "storage.request_failed", "error_type": type(exc).__name__},
    )
    return _error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "database_unavailable",
        "Incident storage is temporarily unavailable.",
    )


@app.exception_handler(TriageException)
async def handle_triage_exception(request: Request, exc: TriageException) -> JSONResponse:
    """Map domain exceptions to structured responses and log their context."""
    logger.error(
        "Domain error while handling request",
        extra={
            "event": "request.domain_error",
            "error_type": type(exc).__name__,
            "error_code": exc.code,
            "path": request.url.path,
            "details": exc.details,
        },
    )
    return _error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    """Count validation errors per field, then return FastAPI's standard 422 body."""
    for error in exc.errors():
        loc = [str(part) for part in error.get("loc", ()) if part != "body"]
        VALIDATION_ERRORS.labels(field=loc[-1] if loc else "request").inc()
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return HTTP errors in the structured envelope (dict details pass through)."""
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
    code = "not_found" if exc.status_code == 404 else "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": code, "message": str(exc.detail)}},
        headers=exc.headers,
    )


@app.post(
    "/incidents/triage",
    response_model=TriageOut,
    summary="Triage and store an incident",
    responses=ERROR_RESPONSES,
)
def triage_incident(payload: IncidentIn) -> TriageOut:
    """Classify, match, persist, and return a newly submitted incident.

    Predicts severity, looks for duplicates among recent incidents, suggests a
    runbook, then stores the incident. Invalid input returns 422.
    """
    return triage_service.triage_incident(payload, get_settings().recent_incident_limit)


@app.get(
    "/incidents/{incident_id}",
    response_model=IncidentDetailOut,
    summary="Get a stored incident",
    responses=ERROR_RESPONSES,
)
def read_incident(incident_id: str) -> IncidentDetailOut:
    """Return a stored incident (cache first, PostgreSQL fallback) or 404."""
    record = incident_service.get_incident(incident_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return IncidentDetailOut(**record)
