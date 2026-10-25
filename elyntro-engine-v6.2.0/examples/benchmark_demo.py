"""Run the benchmark harness without external API calls using a demo adapter."""
from pathlib import Path
import tempfile

from elyntro.benchmark import BenchmarkRunner, basic_text_cases
from elyntro.model_evaluation import EvaluationStore
from elyntro.model_registry import ModelProfile


class DemoAdapter:
    """Deterministic adapter for checking the harness only; not a real model."""
    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        prompt = messages[-1]["content"].casefold()
        if "capital of france" in prompt:
            answer = "Paris is the capital of France."
        elif "17 + 25" in prompt:
            answer = "42"
        else:
            answer = "I do not have enough information to know your favorite color."
        return answer, 30, max(1, len(answer) // 4)


with tempfile.TemporaryDirectory() as directory:
    store = EvaluationStore(str(Path(directory) / "benchmark.db"))
    profile = ModelProfile(
        model_id="demo-model", provider="demo", capabilities=frozenset({"text", "reasoning"}),
        input_cost_per_million=1.0, output_cost_per_million=2.0,
        max_input_tokens=8000, max_output_tokens=1000,
    )
    report = BenchmarkRunner(store).run(
        profile=profile, adapter=DemoAdapter(), cases=basic_text_cases(),
        benchmark_id="elyntro-smoke-v1",
    )
    print(f"Benchmark: {report.benchmark_id}")
    for task_type, summary in report.summary.items():
        print(f"{task_type}: success={summary['success_rate']:.0%}, "
              f"median_quality={summary['median_quality']:.2f}, "
              f"samples={summary['sample_count']}")
    print("Note: deterministic demo adapter; these are not real model measurements.")
