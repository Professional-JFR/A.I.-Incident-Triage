from app.triage import find_duplicate, predict_severity, suggest_runbook


def test_predict_severity_uses_keyword_word_boundaries() -> None:
    assert predict_severity("Download API", "File downloads take longer") == "low"
    assert predict_severity("API down", "Requests fail") == "critical"


def test_predict_severity_handles_phrase_and_case_variations() -> None:
    assert predict_severity("PAYMENTS", "A latency   spike was detected") == "high"


def test_suggest_runbook_matches_service_and_avoids_partial_words() -> None:
    assert suggest_runbook("Account issue", "authentication is delayed", None).startswith("RB-000")
    assert suggest_runbook("Database issue", "", None).startswith("RB-001")


def test_find_duplicate_returns_similar_incident() -> None:
    recent = [
        {
            "incident_id": "INC-123",
            "title": "Payment API timeout",
            "description": "Checkout request timeout",
        },
        {
            "incident_id": "INC-456",
            "title": "Unrelated warning",
            "description": "Storage cleanup",
        },
    ]

    assert find_duplicate("Payment API timeout", "Checkout request timeout", recent) == "INC-123"


def test_find_duplicate_handles_empty_and_nonmatching_incidents() -> None:
    assert find_duplicate("   ", "", []) is None
    assert find_duplicate("Payment timeout", "Checkout", [{"incident_id": None}]) is None


def test_find_duplicate_accepts_exact_similarity_threshold() -> None:
    recent = [{"incident_id": "INC-BOUNDARY", "title": "a b c", "description": ""}]

    assert find_duplicate("a b c d e", "", recent) == "INC-BOUNDARY"
