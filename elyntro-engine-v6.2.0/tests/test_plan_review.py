import unittest
from elyntro.plan_review import PlanQualityReviewer
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition


class RepairBackend:
    name = "test-repair"
    def revise(self, goal, plan, issues, constraints, success_criteria, tools):
        return [{"step_id": "s1", "name": "Create report with references", "handler_key": "writer",
                 "inputs": {}, "depends_on": [], "rationale": "Create report and include references for the requested result."}]


class PlanReviewTests(unittest.TestCase):
    def setUp(self):
        self.tools = [ToolDefinition("writer", "Create report", ("create",)),
                      ToolDefinition("checker", "Verify report", ("verify",))]
        self.reviewer = PlanQualityReviewer()

    def test_reports_missing_rationale_and_criterion_coverage(self):
        plan = ExecutionPlan("Create report", (
            PlannedStep("s1", "Write", "writer", {"goal": "Create report"}, (), False, ""),
        ))
        report = self.reviewer.review(plan, self.tools, success_criteria=["include references"])
        self.assertTrue(any(i.code == "missing_rationale" for i in report.issues))
        self.assertEqual(report.criterion_coverage, 0.0)
        self.assertLess(report.score, 100)

    def test_repair_only_selected_if_score_improves(self):
        plan = ExecutionPlan("Create report", (
            PlannedStep("s1", "Write", "writer", {"goal": "Create report"}, (), False, ""),
        ))
        revised, report = self.reviewer.review_and_repair(
            plan, RuleBasedPlanner(), self.tools, success_criteria=["include references"], backend=RepairBackend())
        self.assertEqual(report.selected_plan, "repaired")
        self.assertIn("references", revised.steps[0].name)

    def test_repair_cannot_invent_tools(self):
        class BadRepair:
            name = "bad"
            def revise(self, *args):
                return [{"step_id": "s1", "name": "Run", "handler_key": "shell", "inputs": {}, "depends_on": []}]
        plan = ExecutionPlan("Create report", (
            PlannedStep("s1", "Write", "writer", {}, (), False, ""),
        ))
        with self.assertRaisesRegex(ValueError, "unregistered tool"):
            self.reviewer.review_and_repair(plan, RuleBasedPlanner(), self.tools, backend=BadRepair())


if __name__ == "__main__":
    unittest.main()
