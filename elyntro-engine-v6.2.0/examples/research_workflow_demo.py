"""End-to-end offline research workflow demo; all sources are synthetic."""
import json
import tempfile
from pathlib import Path

from elyntro.persistent_jobs import PersistentJobStore
from elyntro.research_evidence import ResearchToolRegistry
from elyntro.research_workflow import ResearchWorkflowService


class DemoModel:
    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        report = {
            "summary": "The supplied evidence is mixed: official documentation says export is supported, while a review describes a failed test.",
            "claims": [{"claim_id": "export-status", "text": "The system supports export in practice."}],
            "assessments": [
                {"claim_id": "export-status", "evidence_id": "official-docs", "stance": "supports", "rationale": "The official excerpt states that export is supported."},
                {"claim_id": "export-status", "evidence_id": "review-test", "stance": "contradicts", "rationale": "The review excerpt describes a failed export test."},
            ],
        }
        return json.dumps(report), 40, 80


def demo_search(query, limit):
    return [
        {"evidence_id": "official-docs", "title": "Official documentation (synthetic)",
         "url": "https://example.org/docs", "excerpt": "The system supports export.",
         "publisher": "Example Org", "retrieved_at": "2026-10-16T10:00:00+00:00"},
        {"evidence_id": "review-test", "title": "Independent review (synthetic)",
         "url": "https://example.net/review", "excerpt": "A reviewer reports export failed in one test.",
         "publisher": "Example Review", "retrieved_at": "2026-10-16T10:05:00+00:00"},
    ][:limit]


def main():
    tools = ResearchToolRegistry()
    tools.register("demo_search", demo_search)
    with tempfile.TemporaryDirectory() as tmp:
        store = PersistentJobStore(Path(tmp) / "jobs.sqlite")
        service = ResearchWorkflowService(store, tools, DemoModel(), tool_name="demo_search", model_id="offline-demo-model")
        job_id = service.submit(query="Does this system support export?", idempotency_key="demo-research-v42")
        run = service.run(job_id)
        print(json.dumps(run.to_dict(), indent=2, ensure_ascii=False))
        if run.state != "succeeded" or not run.verified:
            raise SystemExit("demo failed")
        print("NOTE: synthetic sources and a deterministic demo model; no live web search was performed.")


if __name__ == "__main__":
    main()
