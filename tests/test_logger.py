import json
import logging

from app.logger import JsonFormatter


def test_json_formatter_includes_structured_fields() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "triage complete", (), None)
    record.event = "triage.decision"
    record.incident_id = "INC-123"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "triage complete"
    assert payload["event"] == "triage.decision"
    assert payload["incident_id"] == "INC-123"
