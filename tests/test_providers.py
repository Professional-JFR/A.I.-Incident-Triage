import httpx
import pytest
from pydantic import ValidationError

from app.config import reset_settings_cache
from app.metrics import TRIAGE_PROVIDER_FALLBACKS
from app.providers import LLMProvider, RuleBasedProvider
from app.schemas import IncidentIn
from app.services import triage_service

PAYLOAD = IncidentIn(title="API timeout", description="Checkout requests time out")
RECENT = [
    {
        "incident_id": "INC-1",
        "title": "API timeout",
        "description": "Checkout requests time out",
    }
]


@pytest.fixture
def llm_settings(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "llm")
    monkeypatch.setenv("TRIAGE_LLM_ENDPOINT", "https://llm.example.test")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _llm(**kwargs):
    return LLMProvider(
        endpoint="https://llm.example.test/v1/chat/completions",
        model="test",
        timeout_seconds=1,
        retries=kwargs.pop("retries", 0),
        retry_delay_seconds=0,
        **kwargs,
    )


def test_rule_provider_preserves_rule_based_behavior() -> None:
    decision = RuleBasedProvider().triage(PAYLOAD, RECENT)

    assert decision.predicted_severity == "high"
    assert decision.duplicate_of == "INC-1"
    assert decision.runbook_suggestion.startswith("RB-003")


def test_llm_provider_validates_mocked_model_output() -> None:
    def fake_post(*args, **kwargs):
        assert kwargs["timeout"] == 1
        assert kwargs["json"]["model"] == "test"
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://llm.example.test"),
            json={
                "predicted_severity": "critical",
                "duplicate_of": "INC-1",
                "runbook_suggestion": "RB-003",
            },
        )

    decision = _llm(post=fake_post).triage(PAYLOAD, RECENT)

    assert decision.predicted_severity == "critical"
    assert decision.duplicate_of == "INC-1"


@pytest.mark.parametrize(
    "output",
    [
        {"predicted_severity": "urgent", "duplicate_of": None, "runbook_suggestion": "RB-1"},
        {"predicted_severity": "low", "duplicate_of": "INC-unknown", "runbook_suggestion": "RB-1"},
        {"predicted_severity": "low", "duplicate_of": None, "runbook_suggestion": ""},
        {
            "predicted_severity": "low",
            "duplicate_of": None,
            "runbook_suggestion": "RB-1",
            "extra": 1,
        },
    ],
)
def test_llm_provider_rejects_invalid_or_untrusted_output(output) -> None:
    provider = _llm(
        post=lambda *args, **kwargs: httpx.Response(
            200, request=httpx.Request("POST", "https://llm.example.test"), json=output
        )
    )

    with pytest.raises((ValidationError, ValueError)):
        provider.triage(PAYLOAD, RECENT)


def test_llm_provider_retries_transient_failures_with_bounded_backoff() -> None:
    attempts, delays = [], []

    def flaky_post(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.TimeoutException("timed out")
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://llm.example.test"),
            json={"predicted_severity": "low", "duplicate_of": None, "runbook_suggestion": "RB-0"},
        )

    provider = _llm(retries=1, post=flaky_post, sleep=delays.append)
    assert provider.triage(PAYLOAD, []).predicted_severity == "low"
    assert len(attempts) == 2
    assert delays == [0]


def test_service_falls_back_and_records_metric(monkeypatch, llm_settings) -> None:
    monkeypatch.setattr(triage_service.incident_service, "fetch_recent_incidents", lambda limit: [])
    monkeypatch.setattr(
        triage_service.incident_service,
        "create_incident_record",
        lambda **kwargs: {
            "incident_id": "INC-FALLBACK",
            "predicted_severity": kwargs["predicted_severity"],
            "duplicate_of": kwargs["duplicate_of"],
            "runbook_suggestion": kwargs["runbook_suggestion"],
            "status": "triaged",
        },
    )
    monkeypatch.setattr(
        triage_service,
        "LLMProvider",
        lambda **kwargs: type("BrokenLLM", (), {"name": "llm", "triage": lambda *args: 1 / 0})(),
    )
    before = TRIAGE_PROVIDER_FALLBACKS.labels(provider="llm")._value.get()

    result = triage_service.triage_incident(PAYLOAD)

    assert result.predicted_severity == "high"
    assert TRIAGE_PROVIDER_FALLBACKS.labels(provider="llm")._value.get() == before + 1


def test_service_idempotency_cache_replay_skips_provider_and_storage(monkeypatch) -> None:
    replay = {
        "incident_id": "INC-REPLAY",
        "predicted_severity": "high",
        "duplicate_of": None,
        "runbook_suggestion": "RB-003",
        "status": "triaged",
    }
    monkeypatch.setattr(
        triage_service.incident_service, "get_idempotent_result", lambda key_hash: replay
    )
    monkeypatch.setattr(
        triage_service.incident_service,
        "fetch_recent_incidents",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should use cached result")),
    )

    result = triage_service.triage_incident(PAYLOAD, idempotency_key="hashed-key")

    assert result.incident_id == "INC-REPLAY"
