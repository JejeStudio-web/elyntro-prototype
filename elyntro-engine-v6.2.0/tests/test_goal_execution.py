import json
import tempfile
import unittest
from pathlib import Path

from elyntro.goal_execution import GoalExecutionService, GoalInputError
from elyntro.persistent_jobs import PersistentJobStore


EVIDENCE = [
    {"id": "a", "name": "Option A", "cost": 10, "strengths": ["simple"], "risks": ["limited"]},
    {"id": "b", "name": "Option B", "cost": 20, "strengths": ["flexible"], "risks": ["expensive"]},
]


def report(recommendation="a", cited=None, comparison=None):
    return {
        "recommendation_id": recommendation,
        "rationale": "Option A costs less based on supplied evidence.",
        "comparison": comparison or [{"id": "a", "assessment": "Lower cost"}, {"id": "b", "assessment": "Higher cost"}],
        "cited_evidence_ids": cited if cited is not None else ["a", "b"],
        "limitations": ["No external research was performed."],
    }


class FakeModel:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else report()
        self.error = error
        self.calls = 0

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        if self.error:
            raise self.error
        return json.dumps(self.payload), 20, 30


class GoalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(Path(self.tmp.name) / "jobs.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, model=None):
        return GoalExecutionService(self.store, model or FakeModel(), model_id="test-model")

    def run_with(self, model, evidence=None):
        service = self.service(model)
        jid = service.submit(goal="Choose the best option based on supplied evidence.",
                             evidence=EVIDENCE if evidence is None else evidence,
                             idempotency_key="key-" + str(id(model)))
        return service.run(jid)

    def test_valid_report_succeeds_and_verifies(self):
        result = self.run_with(FakeModel())
        self.assertEqual(result.state, "succeeded")
        self.assertTrue(result.verified)
        self.assertIsNotNone(result.result)

    def test_unknown_recommendation_is_rejected(self):
        result = self.run_with(FakeModel(report(recommendation="invented")))
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.verified)

    def test_unknown_citation_is_rejected(self):
        result = self.run_with(FakeModel(report(cited=["a", "external-source-99"])))
        self.assertEqual(result.state, "failed")

    def test_missing_option_comparison_is_rejected(self):
        result = self.run_with(FakeModel(report(comparison=[{"id": "a", "assessment": "Only A"}])))
        self.assertEqual(result.state, "failed")

    def test_invalid_json_is_rejected(self):
        class BadJSON:
            def complete(self, *args):
                return "not json", 1, 1
        result = self.run_with(BadJSON())
        self.assertEqual(result.state, "failed")
        self.assertIn("invalid JSON", result.error)

    def test_model_timeout_is_not_retried_for_non_idempotent_step(self):
        model = FakeModel(error=TimeoutError("simulated timeout"))
        result = self.run_with(model)
        self.assertEqual(result.state, "failed")
        self.assertEqual(model.calls, 1)

    def test_duplicate_evidence_ids_fail_closed(self):
        duplicate = [EVIDENCE[0], dict(EVIDENCE[1], id="a")]
        result = self.run_with(FakeModel(), duplicate)
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.verified)

    def test_submit_rejects_blank_goal(self):
        with self.assertRaises(GoalInputError):
            self.service().submit(goal=" ", evidence=EVIDENCE, idempotency_key="blank")

    def test_evidence_validator_rejects_one_option(self):
        result = self.run_with(FakeModel(), [EVIDENCE[0]])
        self.assertEqual(result.state, "failed")
        self.assertEqual(self.service().model_id, "test-model")

    def test_malformed_unhashable_recommendation_is_rejected_not_crashed(self):
        result = self.run_with(FakeModel(report(recommendation=["a"])))
        self.assertEqual(result.state, "failed")
        self.assertFalse(result.verified)

    def test_duplicate_comparison_ids_are_rejected(self):
        result = self.run_with(FakeModel(report(comparison=[
            {"id": "a", "assessment": "A"}, {"id": "a", "assessment": "A duplicate"}
        ])))
        self.assertEqual(result.state, "failed")

    def test_live_model_metadata_is_recorded(self):
        result = self.run_with(FakeModel())
        self.assertTrue(result.verified)
        output = result.result["outputs"]["synthesize_comparison"]
        self.assertEqual(output["model_metadata"]["model_id"], "test-model")
        self.assertEqual(output["model_metadata"]["input_tokens"], 20)


if __name__ == "__main__":
    unittest.main()
