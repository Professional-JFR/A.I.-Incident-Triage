import json
import logging
import os
from contextvars import ContextVar
from datetime import UTC, datetime

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class JsonFormatter(logging.Formatter):
    """Format application logs as one JSON object per line."""

    _standard_fields = set(logging.makeLogRecord({}).__dict__) | {
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record and its structured fields as JSON."""
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in self._standard_fields and not key.startswith("_")
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _JsonStreamHandler(logging.StreamHandler):
    _incident_json_handler = True


def configure_logging() -> logging.Logger:
    """Configure and return the application's structured logger."""
    logger = logging.getLogger("app")
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    if not any(getattr(handler, "_incident_json_handler", False) for handler in logger.handlers):
        handler = _JsonStreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
