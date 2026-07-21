from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class IncidentIn(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    service: Optional[str] = None
    source: Optional[str] = None
    timestamp: Optional[datetime] = None


class TriageOut(BaseModel):
    incident_id: str
    predicted_severity: str
    duplicate_of: Optional[str] = None
    runbook_suggestion: str
    status: str
