"""Bounded research-tool registry and source/claim evidence audit.

This module deliberately does not fetch the internet itself and does not decide
whether a quotation semantically proves a claim. Applications register explicit
search/fetch handlers, preserve source provenance, and provide claim-to-evidence
assessments. The audit catches missing, malformed, and conflicting provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse


class ResearchInputError(ValueError):
    """Research request or evidence record failed validation."""


@dataclass(frozen=True)
class SourceEvidence:
    evidence_id: str
    title: str
    url: str
    excerpt: str
    retrieved_at: str
    publisher: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "title": self.title,
            "url": self.url,
            "excerpt": self.excerpt,
            "retrieved_at": self.retrieved_at,
            "publisher": self.publisher,
        }


@dataclass(frozen=True)
class ClaimAssessment:
    claim_id: str
    evidence_id: str
    stance: str  # supports | contradicts
    rationale: str


@dataclass(frozen=True)
class ClaimAudit:
    claim_id: str
    status: str  # supported | contradicted | conflicting | insufficient
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    missing_evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
            "missing_evidence_ids": list(self.missing_evidence_ids),
        }


ResearchHandler = Callable[[str, int], Iterable[Mapping[str, Any]]]


class ResearchToolRegistry:
    """Explicit allow-list of bounded research handlers; no model-selected code."""

    MAX_QUERY_LENGTH = 2000
    MAX_RESULTS = 20

    def __init__(self) -> None:
        self._handlers: dict[str, ResearchHandler] = {}

    def register(self, name: str, handler: ResearchHandler) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ResearchInputError("tool name must be a non-empty string of at most 100 characters")
        if name in self._handlers:
            raise ResearchInputError(f"research tool already registered: {name}")
        if not callable(handler):
            raise ResearchInputError("research handler must be callable")
        self._handlers[name] = handler

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def search(self, tool_name: str, query: str, *, limit: int = 5) -> list[SourceEvidence]:
        if tool_name not in self._handlers:
            raise ResearchInputError("research tool is not registered")
        if not isinstance(query, str) or not query.strip() or len(query) > self.MAX_QUERY_LENGTH:
            raise ResearchInputError("query must be non-empty and at most 2000 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.MAX_RESULTS:
            raise ResearchInputError(f"limit must be between 1 and {self.MAX_RESULTS}")
        # Materialize at most limit+1 so a faulty handler cannot force an unbounded read.
        raw_results = self._handlers[tool_name](query.strip(), limit)
        if raw_results is None:
            raise ResearchInputError("research handler returned no result collection")
        evidence: list[SourceEvidence] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_results):
            if index >= limit:
                break
            record = self._validate_source(raw, index)
            if record.evidence_id in seen:
                raise ResearchInputError("research handler returned duplicate evidence IDs")
            seen.add(record.evidence_id)
            evidence.append(record)
        return evidence

    @classmethod
    def _validate_source(cls, raw: Mapping[str, Any], index: int) -> SourceEvidence:
        if not isinstance(raw, Mapping):
            raise ResearchInputError(f"source result {index} must be an object")
        fields = ("evidence_id", "title", "url", "excerpt")
        if any(not isinstance(raw.get(field), str) or not raw[field].strip() for field in fields):
            raise ResearchInputError(f"source result {index} needs non-empty evidence_id, title, url, and excerpt")
        if any(len(raw[field]) > cap for field, cap in (("evidence_id", 200), ("title", 1000), ("url", 4000), ("excerpt", 12000))):
            raise ResearchInputError(f"source result {index} exceeds a field length limit")
        parsed = urlparse(raw["url"].strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ResearchInputError(f"source result {index} URL must be absolute HTTP(S)")
        publisher = raw.get("publisher")
        if publisher is not None and (not isinstance(publisher, str) or len(publisher) > 500):
            raise ResearchInputError(f"source result {index} has invalid publisher")
        retrieved_at = raw.get("retrieved_at")
        if retrieved_at is None:
            retrieved_at = datetime.now(timezone.utc).isoformat()
        if not isinstance(retrieved_at, str) or len(retrieved_at) > 100:
            raise ResearchInputError(f"source result {index} has invalid retrieved_at")
        try:
            datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError:
            raise ResearchInputError(f"source result {index} retrieved_at must be ISO 8601") from None
        return SourceEvidence(raw["evidence_id"].strip(), raw["title"].strip(), raw["url"].strip(),
                             raw["excerpt"].strip(), retrieved_at, publisher.strip() if isinstance(publisher, str) else None)


class ClaimEvidenceAuditor:
    """Audit claim-to-source references and flag conflicting analyst assessments.

    Statuses indicate consistency of the supplied assessments, not independently
    verified truth. A semantic assessor or human reviewer must assign each stance.
    """

    MAX_CLAIMS = 200
    MAX_ASSESSMENTS = 2000

    def audit(self, claims: Sequence[Mapping[str, Any]], evidence: Sequence[SourceEvidence | Mapping[str, Any]],
              assessments: Sequence[ClaimAssessment | Mapping[str, Any]]) -> list[ClaimAudit]:
        if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)) or not 1 <= len(claims) <= self.MAX_CLAIMS:
            raise ResearchInputError(f"claims must contain between 1 and {self.MAX_CLAIMS} items")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            raise ResearchInputError("evidence must be a sequence")
        if not isinstance(assessments, Sequence) or isinstance(assessments, (str, bytes)) or len(assessments) > self.MAX_ASSESSMENTS:
            raise ResearchInputError("assessments must be a sequence within the configured limit")
        claim_ids: list[str] = []
        for i, claim in enumerate(claims):
            if not isinstance(claim, Mapping) or not isinstance(claim.get("claim_id"), str) or not claim["claim_id"].strip():
                raise ResearchInputError(f"claim {i} needs a non-empty claim_id")
            if not isinstance(claim.get("text"), str) or not claim["text"].strip() or len(claim["text"]) > 12000:
                raise ResearchInputError(f"claim {i} needs non-empty text of at most 12000 characters")
            claim_ids.append(claim["claim_id"].strip())
        if len(set(claim_ids)) != len(claim_ids):
            raise ResearchInputError("claim IDs must be unique")

        evidence_ids: set[str] = set()
        for i, item in enumerate(evidence):
            if isinstance(item, SourceEvidence):
                normalized = ResearchToolRegistry._validate_source(item.to_dict(), i)
            else:
                normalized = ResearchToolRegistry._validate_source(item, i)
            if normalized.evidence_id in evidence_ids:
                raise ResearchInputError("evidence IDs must be unique")
            evidence_ids.add(normalized.evidence_id)

        grouped: dict[str, dict[str, set[str]]] = {
            claim_id: {"supports": set(), "contradicts": set()} for claim_id in claim_ids
        }
        for i, assessment in enumerate(assessments):
            if isinstance(assessment, ClaimAssessment):
                item = {"claim_id": assessment.claim_id, "evidence_id": assessment.evidence_id,
                        "stance": assessment.stance, "rationale": assessment.rationale}
            else:
                item = assessment
            if not isinstance(item, Mapping):
                raise ResearchInputError(f"assessment {i} must be an object")
            cid, eid, stance, rationale = (item.get("claim_id"), item.get("evidence_id"),
                                           item.get("stance"), item.get("rationale"))
            if not isinstance(cid, str) or cid not in grouped:
                raise ResearchInputError(f"assessment {i} references unknown claim ID")
            if not isinstance(eid, str) or eid not in evidence_ids:
                raise ResearchInputError(f"assessment {i} references unknown evidence ID")
            if not isinstance(stance, str) or stance not in {"supports", "contradicts"}:
                raise ResearchInputError(f"assessment {i} stance must be supports or contradicts")
            if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4000:
                raise ResearchInputError(f"assessment {i} needs a rationale of at most 4000 characters")
            grouped[cid][stance].add(eid)

        results = []
        for cid in claim_ids:
            supports = tuple(sorted(grouped[cid]["supports"]))
            contradicts = tuple(sorted(grouped[cid]["contradicts"]))
            if supports and contradicts:
                status = "conflicting"
            elif supports:
                status = "supported"
            elif contradicts:
                status = "contradicted"
            else:
                status = "insufficient"
            results.append(ClaimAudit(cid, status, supports, contradicts))
        return results
