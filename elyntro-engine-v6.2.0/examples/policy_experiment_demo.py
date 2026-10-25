"""Offline demonstration with synthetic data only; not a real model benchmark."""
from elyntro.policy_experiments import (
    PolicyExperimentRunner, PolicyRegistry, PolicySpec, RoutingObservation,
)

observations = []
for split in ("development", "holdout"):
    for model, quality, success, latency, cost in (
        ("steady-model", .90, True, 850, .02),
        ("cheap-model", .75, True, 250, .004),
        ("unreliable-model", .98, False, 700, .03),
    ):
        for i in range(6):
            observations.append(RoutingObservation(
                model_id=model, task_type="reasoning", quality=quality,
                success=success, latency_ms=latency, cost=cost,
                split=split, verified=True,
                observation_id=f"{split}-{model}-{i}",
            ))

baseline = PolicySpec("baseline", min_quality=.70, min_success_rate=.70,
                      max_latency_ms=2_000, max_cost=.10, min_samples=3)
candidate = PolicySpec("conservative-candidate", min_quality=.85,
                       min_success_rate=.90, max_latency_ms=1_500,
                       max_cost=.05, min_samples=3)
report = PolicyExperimentRunner().run(
    baseline, candidate, observations, min_holdout_samples=5,
)
print("Baseline:", report.baseline)
print("Candidate:", report.candidate)
print("Promotion eligible:", report.eligible_for_promotion)
print("Reasons:", report.reasons)

# Registry example; this uses synthetic records and is not evidence for production.
registry = PolicyRegistry("policy_demo.db")
version = registry.add_candidate(candidate)
if report.eligible_for_promotion:
    registry.activate(version, report)
    print("Activated demo policy version:", version)
else:
    print("Candidate stored but not activated; version:", version)
