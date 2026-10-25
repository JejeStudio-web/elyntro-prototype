"""Show the critic interface with a local fake gateway; no API call is made."""
import json
from types import SimpleNamespace

from elyntro import ExecutionPlan, ModelPlanCritic, PlannedStep, PlanQualityReviewer, RuleBasedPlanner, ToolDefinition


class DemoGateway:
    def complete(self, request):
        result = {
            "critique": [{"severity": "warning", "category": "verification",
                          "finding": "The plan should explicitly verify the final deliverable.",
                          "affected_step_ids": ["draft"]}],
            "steps": [
                {"step_id": "draft", "name": "Draft report", "handler_key": "writer",
                 "inputs": {}, "depends_on": [], "rationale": "Create the requested report."},
                {"step_id": "check", "name": "Verify final deliverable", "handler_key": "checker",
                 "inputs": {}, "depends_on": ["draft"], "rationale": "Verify the final deliverable against the requested criteria."},
            ],
        }
        return SimpleNamespace(model_id="demo-fake", provider="demo", text=json.dumps(result),
                               input_tokens=200, output_tokens=100, latency_ms=1, estimated_cost=0.0)


tools = [ToolDefinition("writer", "Create a report", ("create",)),
         ToolDefinition("checker", "Verify a report", ("verify",))]
plan = ExecutionPlan("Create a report", (
    PlannedStep("draft", "Draft report", "writer", {}, (), False, ""),
))
critic = ModelPlanCritic(DemoGateway())
selected, report = PlanQualityReviewer().review_and_repair(
    plan, RuleBasedPlanner(), tools, success_criteria=["verify final deliverable"], backend=critic)
print(json.dumps({"critique": critic.last_critique, "selected_plan": report.selected_plan,
                  "score": report.score, "steps": [s.name for s in selected.steps]}, indent=2))
