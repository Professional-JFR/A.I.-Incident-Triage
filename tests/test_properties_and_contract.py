import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.schemas import IncidentIn
from app.triage import find_duplicate


@given(st.text(min_size=1, max_size=200).filter(lambda text: bool(text.strip())))
def test_valid_title_is_trimmed_and_within_contract(title: str) -> None:
    incident = IncidentIn(title=title, description="description")
    assert incident.title == title.strip()
    assert 1 <= len(incident.title) <= 200


@given(st.text(max_size=200).filter(lambda text: not text.strip()))
def test_whitespace_only_titles_are_rejected(title: str) -> None:
    with pytest.raises(ValidationError):
        IncidentIn(title=title, description="description")


@given(st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz"))
def test_duplicate_detection_is_case_and_whitespace_insensitive(word: str) -> None:
    incident = {"incident_id": "INC-PROPERTY", "title": word.lower(), "description": ""}
    result = find_duplicate(f"  {word.upper()}  ", "", [incident])
    assert result == "INC-PROPERTY"


def test_openapi_triage_contract() -> None:
    from app.main import app

    schema = app.openapi()
    operation = schema["paths"]["/incidents/triage"]["post"]

    assert operation["operationId"] == "triage_incident_incidents_triage_post"
    assert "requestBody" in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/TriageOut"
    )
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["in"] == "header"
        for parameter in operation["parameters"]
    )
