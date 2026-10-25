"""End-to-end evidence comparison. Defaults to deterministic offline mode.

For a live compatible API set OPENAI_API_KEY and ELYNTRO_MODEL_ID, then run:
    python examples/goal_execution_demo.py --live
"""
import json
import os
import sys
import tempfile
from pathlib import Path

from elyntro.goal_execution import GoalExecutionService, build_live_model_from_environment
from elyntro.persistent_jobs import PersistentJobStore


class DemoModel:
    """Offline stand-in for reproducible tests; not an AI model."""
    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        request = json.loads(messages[1]["content"])
        items = request["evidence"]
        selected = min(items, key=lambda x: x["cost"])
        response = {
            "recommendation_id": selected["id"],
            "rationale": f"{selected['name']} has the lowest supplied cost; assess its risks before deciding.",
            "comparison": [{"id": x["id"], "assessment": f"Cost {x['cost']}; strengths and risks supplied by the user."} for x in items],
            "cited_evidence_ids": [x["id"] for x in items],
            "limitations": ["Comparison uses only user-supplied evidence; no external research was performed."],
        }
        return json.dumps(response), 80, 120


def main():
    live = "--live" in sys.argv
    if live:
        model, model_id = build_live_model_from_environment()
    else:
        model, model_id = DemoModel(), "offline-demo-model"
    evidence = [
        {"id": "option_a", "name": "Option A", "cost": 35, "strengths": ["Simple setup"], "risks": ["Fewer integrations"]},
        {"id": "option_b", "name": "Option B", "cost": 52, "strengths": ["More integrations"], "risks": ["Higher cost"]},
        {"id": "option_c", "name": "Option C", "cost": 44, "strengths": ["Balanced features"], "risks": ["Unknown maintenance cost"]},
    ]
    with tempfile.TemporaryDirectory(prefix="elyntro-v40-") as tmp:
        store = PersistentJobStore(Path(tmp) / "jobs.sqlite")
        service = GoalExecutionService(store, model, model_id=model_id)
        job_id = service.submit(goal="Compare the options and recommend one using only supplied evidence.",
                                evidence=evidence, idempotency_key="demo-goal-comparison-v1")
        result = service.run(job_id)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        if result.state != "succeeded" or not result.verified:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
