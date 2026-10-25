import tempfile
import unittest
from pathlib import Path

from elyntro.policy_experiments import (
    PolicyExperimentRunner, PolicyRegistry, PolicySpec, RoutingObservation,
)


def dataset():
    rows = []
    for split in ("development", "holdout"):
        for model, quality, success, latency, cost in (
            ("steady", .88, True, 900, .02),
            ("cheap", .72, True, 300, .005),
            ("flaky", .96, False, 700, .03),
        ):
            for i in range(6):
                rows.append(RoutingObservation(model, "reasoning", quality, success, latency, cost,
                    split=split, verified=True, observation_id=f"{split}-{model}-{i}"))
    return rows


class PolicyExperimentTests(unittest.TestCase):
    def test_candidate_is_evaluated_without_mutating_active_policy(self):
        runner = PolicyExperimentRunner()
        baseline = PolicySpec("baseline", min_quality=.70, min_success_rate=.7, max_latency_ms=2000, max_cost=.1, min_samples=3)
        candidate = PolicySpec("candidate", min_quality=.85, min_success_rate=.9, max_latency_ms=1500, max_cost=.05, min_samples=3)
        report = runner.run(baseline, candidate, dataset(), min_holdout_samples=5)
        self.assertEqual(report.baseline.selected_models["reasoning"], "steady")
        self.assertEqual(report.candidate.selected_models["reasoning"], "steady")
        self.assertTrue(report.eligible_for_promotion)

    def test_unverified_data_not_used(self):
        rows = [RoutingObservation("m", "coding", .99, True, 10, .01, split="development", verified=False)]
        with self.assertRaisesRegex(ValueError, "No verified development"):
            PolicyExperimentRunner().run(PolicySpec("a"), PolicySpec("b"), rows)

    def test_holdout_required(self):
        rows = [RoutingObservation("m", "coding", .99, True, 10, .01, split="development", verified=True)]
        with self.assertRaisesRegex(ValueError, "holdout"):
            PolicyExperimentRunner().run(PolicySpec("a"), PolicySpec("b"), rows)

    def test_registry_requires_eligible_report_and_supports_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            reg = PolicyRegistry(Path(td) / "policies.db")
            v1 = reg.add_candidate(PolicySpec("v1"))
            v2 = reg.add_candidate(PolicySpec("v2"))
            report = PolicyExperimentRunner().run(
                PolicySpec("baseline", min_quality=.7, min_success_rate=.7, max_latency_ms=2000, max_cost=.1, min_samples=3),
                PolicySpec("candidate", min_quality=.85, min_success_rate=.9, max_latency_ms=1500, max_cost=.05, min_samples=3),
                dataset(), min_holdout_samples=5)
            reg.activate(v1, report)
            self.assertEqual(reg.active()[0], v1)
            bad = type(report)(report.baseline, report.candidate, -1, -1, 0, 0, 6, False, ("failed",))
            with self.assertRaisesRegex(ValueError, "did not pass"):
                reg.activate(v2, bad)
            reg.activate(v2, report)
            self.assertEqual(reg.active()[0], v2)
            reg.rollback(v1)
            self.assertEqual(reg.active()[0], v1)

    def test_invalid_split_rejected(self):
        with self.assertRaisesRegex(ValueError, "split"):
            RoutingObservation("m", "coding", .5, True, 1, split="test")


if __name__ == "__main__":
    unittest.main()
