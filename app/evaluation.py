"""Offline evaluation of the rule-based triage against a labeled dataset."""

import json
import sys
import time
from pathlib import Path

from app.triage import predict_severity, suggest_runbook

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "data" / "labeled_incidents.json"
SEVERITIES = ("critical", "high", "medium", "low")


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict]:
    """Load and validate labeled incidents from a JSON file."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("Dataset must be a non-empty JSON list")
    for index, record in enumerate(records):
        for field in ("title", "description", "severity"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError(f"Record {index} is missing required field '{field}'")
        if record["severity"] not in SEVERITIES:
            raise ValueError(f"Record {index} has unknown severity '{record['severity']}'")
    return records


def evaluate(records: list[dict]) -> dict:
    """Compute accuracy, per-severity precision/recall, and mean latency."""
    predictions = []
    started = time.perf_counter()
    for record in records:
        predictions.append(predict_severity(record["title"], record["description"]))
    elapsed = time.perf_counter() - started

    per_class = {}
    for label in SEVERITIES:
        tp = sum(1 for r, p in zip(records, predictions, strict=True) if r["severity"] == label == p)
        fp = sum(1 for r, p in zip(records, predictions, strict=True) if r["severity"] != label == p)
        fn = sum(1 for r, p in zip(records, predictions, strict=True) if r["severity"] == label != p)
        per_class[label] = {
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "support": tp + fn,
        }

    correct = sum(1 for r, p in zip(records, predictions, strict=True) if r["severity"] == p)
    runbook_labeled = [r for r in records if r.get("runbook")]
    runbook_correct = sum(
        1
        for r in runbook_labeled
        if suggest_runbook(r["title"], r["description"], r.get("service")).startswith(r["runbook"])
    )
    return {
        "samples": len(records),
        "severity_accuracy": correct / len(records),
        "runbook_accuracy": runbook_correct / len(runbook_labeled) if runbook_labeled else None,
        "per_severity": per_class,
        "mean_latency_ms": elapsed / len(records) * 1000,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_DATASET
    print(json.dumps(evaluate(load_dataset(path)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
