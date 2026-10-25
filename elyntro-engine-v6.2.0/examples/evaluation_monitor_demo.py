"""Synthetic demonstration of evaluation regression detection and quarantine."""
import json
import tempfile
from pathlib import Path

from elyntro.evaluation_monitor import ModelHealthMonitor, RegressionPolicy
from elyntro.model_evaluation import EvaluationResult, EvaluationStore


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = EvaluationStore(root / "evaluations.db")
        for i in range(5):
            store.add(EvaluationResult("demo-model", "coding", .92, 400, 500, 250,
                                      True, benchmark_id=f"baseline-{i % 2}"))
        for i in range(5):
            store.add(EvaluationResult("demo-model", "coding", .61, 850, 500, 250,
                                      i != 0, benchmark_id=f"recent-{i % 2}"))
        monitor = ModelHealthMonitor(store, root / "health.db", RegressionPolicy(window_size=5))
        assessment = monitor.assess_and_quarantine("demo-model", "coding")
        print(json.dumps({
            "status": assessment.status,
            "reasons": list(assessment.reasons),
            "quarantined": monitor.is_quarantined("demo-model", "coding"),
            "note": "Synthetic demonstration data; not a live provider evaluation.",
        }, indent=2))


if __name__ == "__main__":
    main()
