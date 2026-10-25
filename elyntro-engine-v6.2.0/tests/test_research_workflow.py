import json
import tempfile
import unittest
from pathlib import Path

from elyntro.persistent_jobs import PersistentJobStore
from elyntro.research_evidence import ResearchToolRegistry
from elyntro.research_workflow import ResearchWorkflowError, ResearchWorkflowService


def sources(query, limit):
    rows = [
        {"evidence_id": "official", "title": "Official report", "url": "https://example.org/report",
         "excerpt": "The system supports export.", "publisher": "Example Org",
         "retrieved_at": "2026-10-16T10:00:00+00:00"},
        {"evidence_id": "review", "title": "Independent review", "url": "https://example.net/review",
         "excerpt": "A reviewer reports export failed during one test.", "publisher": "Example Review",
         "retrieved_at": "2026-10-16T10:05:00+00:00"},
    ]
    return rows[:limit]


class FakeModel:
    def __init__(self, payload=None, raw=None, error=None):
        self.payload = payload or {"summary": "Evidence is mixed.",
            "claims": [{"claim_id": "c1", "text": "The system supports export."}],
            "assessments": [
                {"claim_id": "c1", "evidence_id": "official", "stance": "supports", "rationale": "Official report states support."},
                {"claim_id": "c1", "evidence_id": "review", "stance": "contradicts", "rationale": "Review reports a failed test."},
            ]}
        self.raw, self.error, self.calls = raw, error, 0

    def complete(self, *args):
        self.calls += 1
        if self.error:
            raise self.error
        return self.raw if self.raw is not None else json.dumps(self.payload), 12, 24


class ResearchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(Path(self.tmp.name) / "jobs.sqlite")
        self.tools = ResearchToolRegistry()
        self.tools.register("search", sources)

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, model=None):
        return ResearchWorkflowService(self.store, self.tools, model or FakeModel(),
            tool_name="search", model_id="fake-model")

    def run_job(self, model=None, key="job-key"):
        service = self.service(model)
        jid = service.submit(query="Does the system support export?", idempotency_key=key)
        return service.run(jid)


    def test_research_quality_gate_flags_unresolved_claims_and_never_claims_truth_score(self):
        run = self.run_job(key="quality-gate-unresolved")
        self.assertEqual(run.state, "succeeded")
        quality = run.result["outputs"]["audit_report"]["research_quality"]
        self.assertEqual(quality["policy_version"], "research-quality-gate-v1")
        self.assertEqual(quality["unresolved_claim_count"], 1)
        self.assertEqual(quality["recommendation"], "seek_more_evidence_or_report_uncertainty")
        self.assertIsNone(quality["truth_score"])
        self.assertTrue(run.verified)

    def test_research_quality_gate_distinguishes_single_domain_support(self):
        audits = [{"status": "supported", "independent_corroboration_established": False}]
        sources = [{"quality_signals": {"triage_score": 4}}]
        result = ResearchWorkflowService._research_quality_assessment(
            audits, sources, {"unique_domains": 1}, {"new_sources": 0})
        self.assertEqual(result["recommendation"], "seek_independent_corroboration_before_reliance")
        self.assertEqual(result["supported_claims_without_independent_domain_corroboration"], 1)
        self.assertEqual(result["mean_source_triage_score"], 4.0)

    def test_research_quality_gate_allows_review_only_when_structural_checks_are_met(self):
        audits = [{"status": "supported", "independent_corroboration_established": True}]
        sources = [{"quality_signals": {"triage_score": 5}}, {"quality_signals": {"triage_score": 3}}]
        result = ResearchWorkflowService._research_quality_assessment(
            audits, sources, {"unique_domains": 2}, {"new_sources": 1})
        self.assertEqual(result["recommendation"], "ready_for_human_review")
        self.assertEqual(result["mean_source_triage_score"], 4.0)
        self.assertIsNone(result["truth_score"])

    def test_durable_research_returns_grounded_conflict(self):
        run = self.run_job()
        self.assertEqual(run.state, "succeeded")
        self.assertTrue(run.verified)
        report = run.result["outputs"]["audit_report"]
        self.assertEqual(report["claim_audit"][0]["status"], "conflicting")
        self.assertEqual(len(report["sources"]), 2)

    def test_report_exposes_domain_diversity_and_corroboration_limits(self):
        run = self.run_job(key="domain-diversity")
        self.assertEqual(run.state, "succeeded")
        report = run.result["outputs"]["audit_report"]
        self.assertEqual(report["source_diversity"]["unique_domains"], 2)
        audit = report["claim_audit"][0]
        self.assertEqual(audit["supporting_domain_count"], 1)
        self.assertEqual(audit["contradicting_domain_count"], 1)
        self.assertFalse(audit["independent_corroboration_established"])
        self.assertIn("proxy", report["source_diversity"]["independence_warning"])

    def test_same_domain_sources_do_not_count_as_independent_corroboration(self):
        def same_domain(query, limit):
            return [
                {"evidence_id": "one", "title": "Page one", "url": "https://www.example.org/a", "excerpt": "Claim supported.", "publisher": "Example"},
                {"evidence_id": "two", "title": "Page two", "url": "https://example.org/b", "excerpt": "Claim supported again.", "publisher": "Example"},
            ][:limit]
        tools = ResearchToolRegistry()
        tools.register("same", same_domain)
        payload = {"summary": "A claim is stated.", "claims": [{"claim_id": "c1", "text": "Claim."}],
            "assessments": [
                {"claim_id": "c1", "evidence_id": "one", "stance": "supports", "rationale": "Page one says so."},
                {"claim_id": "c1", "evidence_id": "two", "stance": "supports", "rationale": "Page two says so."},
            ]}
        service = ResearchWorkflowService(self.store, tools, FakeModel(payload), tool_name="same", model_id="fake")
        jid = service.submit(query="Claim?", idempotency_key="same-domain")
        run = service.run(jid)
        self.assertEqual(run.state, "succeeded")
        report = run.result["outputs"]["audit_report"]
        self.assertEqual(report["source_diversity"]["unique_domains"], 1)
        self.assertEqual(report["claim_audit"][0]["supporting_domain_count"], 1)
        self.assertFalse(report["claim_audit"][0]["independent_corroboration_established"])

    def test_invalid_model_json_fails_closed(self):
        run = self.run_job(FakeModel(raw="not json"), "bad-json")
        self.assertEqual(run.state, "failed")
        self.assertFalse(run.verified)

    def test_hallucinated_evidence_id_fails_closed(self):
        payload = {"summary": "A claim.", "claims": [{"claim_id": "c1", "text": "Claim."}],
            "assessments": [{"claim_id": "c1", "evidence_id": "made-up", "stance": "supports", "rationale": "Looks right."}]}
        run = self.run_job(FakeModel(payload), "fake-evidence")
        self.assertEqual(run.state, "failed")
        self.assertIn("unknown evidence ID", run.error)

    def test_duplicate_claim_ids_fail_closed(self):
        payload = {"summary": "A claim.", "claims": [
            {"claim_id": "c1", "text": "Claim one."}, {"claim_id": "c1", "text": "Claim duplicate."}], "assessments": []}
        run = self.run_job(FakeModel(payload), "duplicate-claim")
        self.assertEqual(run.state, "failed")

    def test_empty_search_results_fail_closed(self):
        tools = ResearchToolRegistry()
        tools.register("empty", lambda q, n: [])
        service = ResearchWorkflowService(self.store, tools, FakeModel(), tool_name="empty", model_id="fake")
        jid = service.submit(query="something", idempotency_key="empty-search")
        run = service.run(jid)
        self.assertEqual(run.state, "failed")
        self.assertIn("no sources", run.error)
        self.assertEqual(FakeModel().calls, 0)

    def test_non_idempotent_model_step_is_not_replayed_after_timeout(self):
        model = FakeModel(error=TimeoutError("provider timeout"))
        run = self.run_job(model, "timeout")
        self.assertEqual(run.state, "failed")
        self.assertEqual(model.calls, 1)

    def test_adaptive_search_expands_query_when_source_target_is_not_met(self):
        seen_queries = []
        def adaptive(query, limit):
            seen_queries.append(query)
            if "official documentation" in query:
                return [{"evidence_id": "official-2", "title": "Official docs", "url": "https://docs.example.com/product",
                         "excerpt": "The official documentation explains the supported export behavior in detail.", "publisher": "Docs Team"}]
            return [{"evidence_id": "first", "title": "Initial source", "url": "https://news.example.org/story",
                     "excerpt": "A report discusses the product and its capabilities in a useful context.", "publisher": "News"}]
        tools = ResearchToolRegistry()
        tools.register("adaptive", adaptive)
        payload = {"summary": "Two sources were found.", "claims": [{"claim_id": "c1", "text": "The topic is documented."}],
                   "assessments": [{"claim_id": "c1", "evidence_id": "first", "stance": "supports", "rationale": "Initial source discusses the topic."},
                                   {"claim_id": "c1", "evidence_id": "official-2", "stance": "supports", "rationale": "Official docs discuss the topic."}]}
        service = ResearchWorkflowService(self.store, tools, FakeModel(payload), tool_name="adaptive", model_id="fake",
                                          result_limit=4, min_sources=2, min_unique_domains=2, max_search_rounds=2)
        jid = service.submit(query="product export", idempotency_key="adaptive-search")
        run = service.run(jid)
        self.assertEqual(run.state, "succeeded")
        self.assertGreaterEqual(len(seen_queries), 2)
        search = run.result["outputs"]["search_sources"]
        self.assertEqual(search["search_rounds_used"], 2)
        self.assertGreaterEqual(run.result["outputs"]["search_evidence_gaps"]["follow_up_search"]["planned_queries"], 0)
        self.assertTrue(search["evidence_target"]["met"])
        self.assertIn("official documentation", seen_queries[1])

    def test_adaptive_search_is_bounded_by_configured_rounds(self):
        seen_queries = []
        def sparse(query, limit):
            seen_queries.append(query)
            return [{"evidence_id": "only", "title": "One source", "url": "https://only.example.org/a",
                     "excerpt": "A short but valid excerpt about the topic.", "publisher": "One"}]
        tools = ResearchToolRegistry()
        tools.register("sparse", sparse)
        payload = {"summary": "Only one domain was found.", "claims": [{"claim_id": "c1", "text": "The topic is mentioned."}],
                   "assessments": [{"claim_id": "c1", "evidence_id": "only", "stance": "supports", "rationale": "The source mentions the topic."}]}
        service = ResearchWorkflowService(self.store, tools, FakeModel(payload), tool_name="sparse", model_id="fake",
                                          result_limit=3, min_sources=2, min_unique_domains=2, max_search_rounds=2)
        jid = service.submit(query="topic", idempotency_key="bounded-search")
        run = service.run(jid)
        self.assertEqual(run.state, "succeeded")
        self.assertGreaterEqual(len(seen_queries), 2)
        search = run.result["outputs"]["search_sources"]
        self.assertFalse(search["evidence_target"]["met"])
        self.assertTrue(any("target not met" in note for note in search["search_notes"]))

    def test_evidence_gap_plan_prioritizes_conflicts_and_is_deterministic(self):
        claims = [
            {"claim_id": "c1", "text": "Feature A is available."},
            {"claim_id": "c2", "text": "Feature B is available."},
            {"claim_id": "c3", "text": "Feature C is available."},
        ]
        audits = [
            {"claim_id": "c1", "status": "supported"},
            {"claim_id": "c2", "status": "conflicting"},
            {"claim_id": "c3", "status": "insufficient"},
        ]
        sources = [{"search_query": "original research question"}]
        first = ResearchWorkflowService._build_evidence_gap_plan(claims, audits, sources)
        second = ResearchWorkflowService._build_evidence_gap_plan(claims, audits, sources)
        self.assertEqual(first, second)
        self.assertEqual(first["queries"][0]["claim_id"], "c2")
        self.assertEqual(first["queries"][0]["current_status"], "conflicting")
        self.assertEqual(first["execution_status"], "not_executed")

    def test_evidence_gap_plan_avoids_queries_already_used(self):
        claims = [{"claim_id": "c1", "text": "Feature X exists."}]
        audits = [{"claim_id": "c1", "status": "insufficient"}]
        query = "reliable evidence for: Feature X exists."
        plan = ResearchWorkflowService._build_evidence_gap_plan(claims, audits, [{"search_query": query}])
        self.assertEqual(plan["query_count"], 0)

    def test_evidence_gap_plan_is_bounded_and_deduplicates_same_claim_text(self):
        claims = [{"claim_id": f"c{i}", "text": "Repeated claim."} for i in range(10)]
        audits = [{"claim_id": f"c{i}", "status": "conflicting"} for i in range(10)]
        plan = ResearchWorkflowService._build_evidence_gap_plan(claims, audits, [])
        self.assertLessEqual(plan["query_count"], 6)
        self.assertEqual(plan["query_count"], 1)

    def test_gap_search_executes_followup_and_reassesses_new_sources(self):
        calls = []
        def search(query, limit):
            calls.append(query)
            if query.startswith("independent corroboration for:"):
                return [{"evidence_id": "followup", "title": "Independent source",
                    "url": "https://second.example.net/claim", "excerpt": "An independent source also describes the claim.",
                    "publisher": "Second source"}]
            return [{"evidence_id": "initial", "title": "Initial source",
                "url": "https://first.example.org/claim", "excerpt": "The source describes the claim.",
                "publisher": "First source"}]
        class DynamicModel:
            def __init__(self): self.calls = 0
            def complete(self, model_id, messages, max_tokens, timeout):
                self.calls += 1
                payload = json.loads(messages[1]["content"])
                evidence = payload["sources"]
                return json.dumps({"summary": "The claim is described in the retrieved sources.",
                    "claims": [{"claim_id": "c1", "text": "The claim is documented."}],
                    "assessments": [{"claim_id": "c1", "evidence_id": item["evidence_id"],
                        "stance": "supports", "rationale": "The excerpt discusses the claim."} for item in evidence]}), 10, 20
        tools = ResearchToolRegistry()
        tools.register("targeted", search)
        model = DynamicModel()
        service = ResearchWorkflowService(self.store, tools, model, tool_name="targeted", model_id="fake",
            result_limit=4, max_search_rounds=1, min_sources=1, min_unique_domains=1)
        jid = service.submit(query="claim", idempotency_key="iterative-gap-search")
        run = service.run(jid)
        self.assertEqual(run.state, "succeeded")
        self.assertEqual(model.calls, 2)
        self.assertTrue(any(query.startswith("independent corroboration for:") for query in calls))
        report = run.result["outputs"]["audit_report"]
        self.assertEqual(len(report["sources"]), 2)
        self.assertEqual(report["follow_up_search"]["new_sources"], 1)
        self.assertIn("followup", {source["evidence_id"] for source in report["sources"]})

    def test_final_report_includes_executed_gap_search_and_next_gap_plan(self):
        run = self.run_job(key="gap-plan-report")
        self.assertEqual(run.state, "succeeded")
        report = run.result["outputs"]["audit_report"]
        self.assertIn("evidence_gap_plan", report)
        self.assertEqual(report["follow_up_search"]["execution_status"], "executed")
        self.assertGreaterEqual(report["follow_up_search"]["planned_queries"], 1)
        self.assertIn(report["evidence_gap_plan"]["execution_status"], {"not_executed"})

    def test_adaptive_search_rejects_impossible_source_target(self):
        with self.assertRaises(ResearchWorkflowError):
            ResearchWorkflowService(self.store, self.tools, FakeModel(), tool_name="search", model_id="fake",
                                    result_limit=2, min_sources=3)

    def test_unregistered_tool_rejected_at_construction(self):
        with self.assertRaises(ResearchWorkflowError):
            ResearchWorkflowService(self.store, self.tools, FakeModel(), tool_name="web", model_id="fake")

    def test_blank_query_rejected(self):
        with self.assertRaises(ResearchWorkflowError):
            self.service().submit(query=" ", idempotency_key="blank")


if __name__ == "__main__":
    unittest.main()
