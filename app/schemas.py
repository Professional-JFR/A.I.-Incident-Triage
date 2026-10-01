from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class IncidentIn(BaseModel):
    """Validated incident fields accepted by the triage endpoint."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=5000)
    service: Optional[str] = Field(None, min_length=1, max_length=100)
    source: Optional[str] = Field(None, min_length=1, max_length=100)
    timestamp: Optional[datetime] = None

    @field_validator("title", "description", "service", "source", mode="before")
    @classmethod
    def reject_blank_text(cls, value: object) -> object:
        """Trim text values and reject fields made only of whitespace."""
        if value is None:
            return value
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("must contain at least one non-whitespace character")
            return trimmed
        return value


class TriageOut(BaseModel):
    """Public triage result returned after incident persistence."""

    incident_id: str
    predicted_severity: str
    duplicate_of: Optional[str] = None
    runbook_suggestion: str
    status: str
