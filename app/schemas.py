from datetime import datetime
from typing import Any, Optional

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

    incident_id: str = Field(..., description="Generated incident ID.", examples=["INC-3003131B"])
    predicted_severity: str = Field(
        ..., description="One of low, medium, high, critical.", examples=["high"]
    )
    duplicate_of: Optional[str] = Field(
        None, description="ID of a similar recent incident, if any.", examples=["INC-7D962A80"]
    )
    runbook_suggestion: str = Field(
        ..., examples=["RB-002: Validate payment gateway and API error rates"]
    )
    status: str = Field(..., description="Incident workflow status.", examples=["triaged"])


class IncidentDetailOut(TriageOut):
    """Stored incident including the original submission and triage output."""

    title: str
    description: str
    service: Optional[str] = None
    source: Optional[str] = None
    timestamp: Optional[str] = Field(None, description="ISO 8601 time the incident occurred.")


class HealthOut(BaseModel):
    """Liveness response."""

    status: str = Field("ok", examples=["ok"])


class ReadyOut(BaseModel):
    """Readiness response. PostgreSQL is critical; Redis is optional."""

    status: str = Field(..., examples=["ready"])
    database: str = Field(..., description="ok or error.", examples=["ok"])
    redis: str = Field(..., description="ok or degraded.", examples=["degraded"])


class ErrorDetail(BaseModel):
    """Machine-readable error description."""

    code: str = Field(..., examples=["database_unavailable"])
    message: str = Field(..., examples=["Incident storage is temporarily unavailable."])
    details: Optional[dict[str, Any]] = None


class ErrorOut(BaseModel):
    """Error envelope used by all non-validation error responses."""

    detail: ErrorDetail
