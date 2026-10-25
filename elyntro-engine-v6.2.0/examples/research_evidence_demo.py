"""Offline demo for the research boundary and claim/evidence audit."""
from elyntro.research_evidence import ClaimAssessment, ClaimEvidenceAuditor, ResearchToolRegistry


def demo_search(query, limit):
    """Deterministic stand-in for an application-provided search connector."""
    rows = [
        {"evidence_id": "source-a", "title": "Provider documentation", "url": "https://example.org/docs",
         "excerpt": "The service supports data export.", "publisher": "Example Provider",
         "retrieved_at": "2026-10-16T10:00:00+00:00"},
        {"evidence_id": "source-b", "title": "Independent review", "url": "https://example.net/review",
         "excerpt": "The reviewer reports that exports failed in a test.", "publisher": "Example Review",
         "retrieved_at": "2026-10-16T10:05:00+00:00"},
    ]
    return rows[:limit]


def main():
    tools = ResearchToolRegistry()
    tools.register("demo_search", demo_search)
    sources = tools.search("demo_search", "does this service support export?", limit=5)
    claims = [{"claim_id": "export-support", "text": "The service supports data export."}]
    assessments = [
        ClaimAssessment("export-support", "source-a", "supports", "Provider documentation states export is supported."),
        ClaimAssessment("export-support", "source-b", "contradicts", "Independent review reports a failed export test."),
    ]
    audit = ClaimEvidenceAuditor().audit(claims, sources, assessments)
    print({"sources": [item.to_dict() for item in sources], "claim_audit": [item.to_dict() for item in audit]})
    print("Note: demo sources are synthetic; 'conflicting' describes supplied assessments, not established truth.")


if __name__ == "__main__":
    main()
