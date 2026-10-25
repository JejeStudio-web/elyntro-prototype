import unittest
from elyntro.research_evidence import (
    ClaimAssessment, ClaimEvidenceAuditor, ResearchInputError,
    ResearchToolRegistry, SourceEvidence,
)


def source(eid="src-1", url="https://example.org/report"):
    return {"evidence_id": eid, "title": "Example report", "url": url,
            "excerpt": "A bounded excerpt from the source.",
            "retrieved_at": "2026-10-16T10:00:00+00:00", "publisher": "Example"}


class ResearchToolRegistryTests(unittest.TestCase):
    def test_registered_tool_returns_normalized_source(self):
        registry = ResearchToolRegistry()
        registry.register("search", lambda query, limit: [source()])
        results = registry.search("search", "topic", limit=3)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], SourceEvidence)
        self.assertEqual(results[0].url, "https://example.org/report")

    def test_tool_allowlist_blocks_unregistered_names(self):
        with self.assertRaises(ResearchInputError):
            ResearchToolRegistry().search("web", "topic")

    def test_query_and_limit_are_bounded(self):
        registry = ResearchToolRegistry()
        registry.register("search", lambda query, limit: [])
        with self.assertRaises(ResearchInputError):
            registry.search("search", " ")
        with self.assertRaises(ResearchInputError):
            registry.search("search", "topic", limit=21)

    def test_non_http_url_rejected(self):
        registry = ResearchToolRegistry()
        registry.register("search", lambda query, limit: [source(url="javascript:alert(1)")])
        with self.assertRaises(ResearchInputError):
            registry.search("search", "topic")

    def test_duplicate_source_ids_rejected(self):
        registry = ResearchToolRegistry()
        registry.register("search", lambda query, limit: [source(), source()])
        with self.assertRaises(ResearchInputError):
            registry.search("search", "topic", limit=2)

    def test_results_are_capped_even_if_handler_returns_more(self):
        registry = ResearchToolRegistry()
        registry.register("search", lambda query, limit: [source(f"s{i}", f"https://example.org/{i}") for i in range(10)])
        self.assertEqual(len(registry.search("search", "topic", limit=2)), 2)

    def test_duplicate_tool_registration_rejected(self):
        registry = ResearchToolRegistry()
        registry.register("search", lambda query, limit: [])
        with self.assertRaises(ResearchInputError):
            registry.register("search", lambda query, limit: [])


class ClaimEvidenceAuditorTests(unittest.TestCase):
    def setUp(self):
        self.auditor = ClaimEvidenceAuditor()
        self.claims = [{"claim_id": "c1", "text": "The service supports exports."}]
        self.evidence = [source(), source("src-2", "https://example.net/review")]

    def test_support_status_uses_grounded_evidence_ids(self):
        report = self.auditor.audit(self.claims, self.evidence, [
            ClaimAssessment("c1", "src-1", "supports", "Source says export is supported.")])
        self.assertEqual(report[0].status, "supported")
        self.assertEqual(report[0].supporting_evidence_ids, ("src-1",))

    def test_conflicting_assessments_are_flagged_not_collapsed(self):
        report = self.auditor.audit(self.claims, self.evidence, [
            ClaimAssessment("c1", "src-1", "supports", "Explicit support."),
            ClaimAssessment("c1", "src-2", "contradicts", "Explicit contradiction.")])
        self.assertEqual(report[0].status, "conflicting")

    def test_no_assessment_means_insufficient_not_supported(self):
        report = self.auditor.audit(self.claims, self.evidence, [])
        self.assertEqual(report[0].status, "insufficient")

    def test_contradicted_status_is_distinct(self):
        report = self.auditor.audit(self.claims, self.evidence, [
            ClaimAssessment("c1", "src-2", "contradicts", "Source contradicts the claim.")])
        self.assertEqual(report[0].status, "contradicted")

    def test_unknown_evidence_id_fails_closed(self):
        with self.assertRaises(ResearchInputError):
            self.auditor.audit(self.claims, self.evidence, [
                ClaimAssessment("c1", "invented", "supports", "Not grounded.")])

    def test_unknown_claim_id_fails_closed(self):
        with self.assertRaises(ResearchInputError):
            self.auditor.audit(self.claims, self.evidence, [
                ClaimAssessment("invented", "src-1", "supports", "Not grounded.")])

    def test_malformed_unhashable_claim_id_fails_closed(self):
        with self.assertRaises(ResearchInputError):
            self.auditor.audit(self.claims, self.evidence, [{
                "claim_id": ["c1"], "evidence_id": "src-1", "stance": "supports", "rationale": "x"}])

    def test_duplicate_claim_ids_rejected(self):
        with self.assertRaises(ResearchInputError):
            self.auditor.audit(self.claims * 2, self.evidence, [])

    def test_source_records_are_validated_during_audit(self):
        with self.assertRaises(ResearchInputError):
            self.auditor.audit(self.claims, [source(url="file:///etc/passwd")], [])

    def test_audit_serialization_is_json_friendly(self):
        result = self.auditor.audit(self.claims, self.evidence, [])
        self.assertEqual(result[0].to_dict()["status"], "insufficient")


if __name__ == "__main__":
    unittest.main()
