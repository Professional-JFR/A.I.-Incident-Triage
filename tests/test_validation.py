import pytest
from pydantic import ValidationError

from app.schemas import IncidentIn


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "   "),
        ("description", "\t\n"),
        ("service", " "),
        ("source", "\n"),
    ],
)
def test_rejects_whitespace_only_values(field: str, value: str) -> None:
    payload = {"title": "Failure", "description": "Request failed", field: value}

    with pytest.raises(ValidationError):
        IncidentIn(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * 201),
        ("description", "x" * 5001),
        ("service", "x" * 101),
        ("source", "x" * 101),
    ],
)
def test_rejects_values_over_maximum_length(field: str, value: str) -> None:
    payload = {"title": "Failure", "description": "Request failed", field: value}

    with pytest.raises(ValidationError):
        IncidentIn(**payload)


def test_trims_valid_text_and_accepts_missing_optional_fields() -> None:
    payload = IncidentIn(title="  Failure  ", description=" Request failed ")

    assert payload.title == "Failure"
    assert payload.description == "Request failed"
    assert payload.service is None
    assert payload.source is None


def test_accepts_text_at_maximum_field_lengths() -> None:
    payload = IncidentIn(
        title="t" * 200,
        description="d" * 5000,
        service="s" * 100,
        source="c" * 100,
    )

    assert len(payload.title) == 200
    assert len(payload.description) == 5000
