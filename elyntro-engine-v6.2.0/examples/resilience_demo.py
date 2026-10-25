"""Run deterministic fault-injection checks for durable workflow recovery."""
import json

from elyntro.resilience import ResilienceHarness


if __name__ == "__main__":
    report = ResilienceHarness.run()
    print(json.dumps(report.to_dict(), indent=2))
    if not report.passed:
        raise SystemExit(1)
