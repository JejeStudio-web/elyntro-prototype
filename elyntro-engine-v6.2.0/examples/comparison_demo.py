"""Offline demonstration of paired model benchmarking with simulated adapters."""
from pathlib import Path
import tempfile

from elyntro import BenchmarkCase, BenchmarkComparisonRunner, EvaluationStore, ModelProfile


class DemoAdapter:
    def __init__(self, response: str):
        self.response = response

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        return self.response, 18, 7


def main():
    profiles = [
        ModelProfile("demo-good", "demo-good-provider", frozenset({"text"}), 0.2, 0.8, 8000, 1000),
        ModelProfile("demo-weak", "demo-weak-provider", frozenset({"text"}), 0.1, 0.4, 8000, 1000),
    ]
    adapters = {
        "demo-good-provider": DemoAdapter("42"),
        "demo-weak-provider": DemoAdapter("I cannot answer"),
    }
    cases = [
        BenchmarkCase("sum-17-25", "reasoning", ({"role": "user", "content": "17 + 25?"},), required_contains=("42",), split="holdout"),
        BenchmarkCase("answer-format", "instruction_following", ({"role": "user", "content": "Reply with the number 42 only."},), expected_exact="42", split="holdout"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        report = BenchmarkComparisonRunner(EvaluationStore(str(Path(tmp) / "eval.db"))).compare(
            profiles=profiles, adapters=adapters, cases=cases,
            comparison_id="offline-demo", split="holdout", repeats=2,
        )
        report.export_json(Path(tmp) / "comparison.json")
        report.export_csv(Path(tmp) / "comparison.csv")
        for summary in report.summaries:
            print(f"{summary.model_id}: success={summary.success_rate:.0%}, "
                  f"mean_quality={summary.mean_quality:.2f}, "
                  f"median_latency={summary.median_latency_ms:.0f}ms, "
                  f"median_cost=${summary.median_estimated_cost:.8f}")
        print(f"Paired comparisons: {len(report.paired_differences)}")
        print("Demo uses simulated adapters; results are not real model evaluations.")


if __name__ == "__main__":
    main()
