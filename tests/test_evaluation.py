import json

import pytest

from app import evaluation


def test_default_dataset_meets_baseline():
    result = evaluation.evaluate(evaluation.load_dataset())
    assert result["severity_accuracy"] >= 0.9
    assert result["per_severity"]["critical"]["recall"] == 1.0


def test_metrics_are_computed_per_class():
    records = [
        {"title": "outage", "description": "x", "severity": "critical"},
        {"title": "all fine", "description": "x", "severity": "high"},
    ]
    result = evaluation.evaluate(records)
    assert result["severity_accuracy"] == 0.5
    assert result["per_severity"]["critical"]["precision"] == 1.0
    assert result["per_severity"]["high"]["recall"] == 0.0
    assert result["runbook_accuracy"] is None


@pytest.mark.parametrize(
    "payload",
    [[], {}, [{"title": "t", "description": "d"}], [{"title": "t", "description": "d", "severity": "bad"}]],
)
def test_load_dataset_rejects_invalid(tmp_path, payload):
    path = tmp_path / "d.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        evaluation.load_dataset(path)


def test_main_prints_report(capsys):
    assert evaluation.main([]) == 0
    assert "severity_accuracy" in capsys.readouterr().out
