"""Triage provider implementations and strict decision validation."""

import json
import time
from collections.abc import Callable
from typing import Literal, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import IncidentIn
from app.triage import find_duplicate, predict_severity, suggest_runbook


class TriageDecision(BaseModel):
    """Validated decision returned by any triage provider."""

    model_config = ConfigDict(strict=True, extra="forbid")

    predicted_severity: Literal["low", "medium", "high", "critical"]
    duplicate_of: str | None
    runbook_suggestion: str = Field(min_length=1, max_length=500)


class TriageProvider(Protocol):
    """Interface implemented by deterministic and model-backed providers."""

    name: str

    def triage(self, payload: IncidentIn, recent: list[dict]) -> TriageDecision:
        """Return a validated decision for an incident."""


class RuleBasedProvider:
    """Existing deterministic rules, retained as the default and fallback."""

    name = "rules"

    def triage(self, payload: IncidentIn, recent: list[dict]) -> TriageDecision:
        return TriageDecision(
            predicted_severity=cast(
                Literal["low", "medium", "high", "critical"],
                predict_severity(payload.title, payload.description),
            ),
            duplicate_of=find_duplicate(payload.title, payload.description, recent),
            runbook_suggestion=suggest_runbook(payload.title, payload.description, payload.service),
        )


class LLMProvider:
    """OpenAI-compatible JSON endpoint client; injectable request and sleep functions."""

    name = "llm"

    def __init__(
        self,
        endpoint: str,
        model: str,
        timeout_seconds: float,
        retries: int,
        retry_delay_seconds: float,
        api_key: str | None = None,
        post: Callable = httpx.post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.api_key = api_key
        self._post = post
        self._sleep = sleep

    def triage(self, payload: IncidentIn, recent: list[dict]) -> TriageDecision:
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only a JSON object with predicted_severity (low, medium, high, or "
                        "critical), duplicate_of (an incident ID or null), and runbook_suggestion "
                        "(a non-empty string). Do not invent duplicate IDs."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "incident": payload.model_dump(mode="json"),
                            "recent_incidents": recent,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        response = None
        for attempt in range(self.retries + 1):
            try:
                response = self._post(
                    self.endpoint,
                    json=body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= self.retries:
                    raise
                self._sleep(self.retry_delay_seconds * (2**attempt))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise
                if attempt >= self.retries:
                    raise
                self._sleep(self.retry_delay_seconds * (2**attempt))

        if response is None:
            raise RuntimeError("LLM provider returned no response")
        output = response.json()
        if isinstance(output, dict) and "choices" in output:
            output = output["choices"][0]["message"]["content"]
        if isinstance(output, str):
            output = json.loads(output)
        decision = TriageDecision.model_validate(output)
        valid_ids = {item.get("incident_id") for item in recent}
        if decision.duplicate_of is not None and decision.duplicate_of not in valid_ids:
            raise ValueError("LLM provider returned a duplicate ID outside the supplied incidents")
        return decision
