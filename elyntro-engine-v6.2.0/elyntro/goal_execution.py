"""Goal-oriented evidence comparison using registered tools and an optional real model.

This is a bounded local workflow, not a general autonomous agent. It only compares
structured evidence supplied by the caller; it does not browse the web or execute
arbitrary model-generated code. A deterministic verifier checks the model's output
against the evidence bundle before the durable job can succeed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .durable_workflows import DurableWorkflowService
from .model_gateway import OpenAICompatibleAdapter
from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


class TextModel(Protocol):
    def complete(self, model_id: str, messages: Sequence[Mapping[str, str]],
                 max_output_tokens: int, timeout_seconds: float) -> tuple[str, int, int]: ...


class GoalInputError(ValueError):
    """The caller supplied an invalid goal or evidence bundle."""


@dataclass(frozen=True)
class GoalRun:
    job_id: str
    state: str
    verified: bool
    result: dict[str, Any] | None
    error: str | None
    model_id: str
    latency_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id, "state": self.state, "verified": self.verified,
            "result": self.result, "error": self.error, "model_id": self.model_id,
            "latency_ms": self.latency_ms,
        }


class GoalExecutionService:
    """Run an auditable comparison workflow using only caller-supplied evidence.

    Evidence items must contain id, name, cost, strengths, and risks. A caller can
    inject a fake model for tests or OpenAICompatibleAdapter for a configured live API.
    The model response is treated as untrusted data and is parsed as JSON.
    """

    TASK_TYPE = "goal_evidence_comparison"
    VERIFIER_ID = "evidence-comparison-v1"
    MAX_EVIDENCE_ITEMS = 30
    MAX_TEXT_LENGTH = 4000

    def __init__(self, store: PersistentJobStore, model: TextModel, *, model_id: str,
                 max_output_tokens: int = 700, timeout_seconds: float = 30.0,
                 max_retries: int = 1) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        if not 32 <= max_output_tokens <= 4000:
            raise ValueError("max_output_tokens must be between 32 and 4000")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.store, self.model, self.model_id = store, model, model_id
        self.max_output_tokens, self.timeout_seconds = max_output_tokens, timeout_seconds
        self.verifiers = TaskVerifierRegistry()
        self.verifiers.register(DurableWorkflowService.TASK_TYPE, self.VERIFIER_ID, self._verify_result)
        self.workflow = DurableWorkflowService(
            store, self.verifiers,
            {"validate_evidence": self._validate_evidence,
             "synthesize_comparison": self._synthesize_comparison},
            max_retries=max_retries,
        )

    @classmethod
    def _validate_evidence(cls, data: dict[str, Any]) -> dict[str, Any]:
        items = data.get("items")
        if not isinstance(items, list) or not 2 <= len(items) <= cls.MAX_EVIDENCE_ITEMS:
            raise GoalInputError(f"evidence must contain between 2 and {cls.MAX_EVIDENCE_ITEMS} items")
        normalized = []
        seen = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise GoalInputError(f"evidence item {index} must be an object")
            required = ("id", "name", "cost", "strengths", "risks")
            if any(key not in item for key in required):
                raise GoalInputError(f"evidence item {index} is missing required fields")
            item_id, name = item["id"], item["name"]
            if not isinstance(item_id, str) or not item_id.strip() or item_id in seen:
                raise GoalInputError("evidence IDs must be unique non-empty strings")
            if not isinstance(name, str) or not name.strip():
                raise GoalInputError(f"evidence item {item_id} has an invalid name")
            if isinstance(item["cost"], bool) or not isinstance(item["cost"], (int, float)) or item["cost"] < 0:
                raise GoalInputError(f"evidence item {item_id} has an invalid cost")
            for field in ("strengths", "risks"):
                if not isinstance(item[field], list) or not item[field] or any(
                    not isinstance(x, str) or not x.strip() or len(x) > cls.MAX_TEXT_LENGTH
                    for x in item[field]
                ):
                    raise GoalInputError(f"evidence item {item_id} has invalid {field}")
            if len(name) > cls.MAX_TEXT_LENGTH:
                raise GoalInputError(f"evidence item {item_id} name is too long")
            seen.add(item_id)
            normalized.append({"id": item_id, "name": name.strip(), "cost": item["cost"],
                               "strengths": item["strengths"], "risks": item["risks"]})
        return {"items": normalized, "item_count": len(normalized)}

    def _synthesize_comparison(self, data: dict[str, Any]) -> dict[str, Any]:
        goal, evidence = data.get("goal"), data.get("evidence")
        if not isinstance(goal, str) or not goal.strip() or len(goal) > self.MAX_TEXT_LENGTH:
            raise GoalInputError("goal must be a non-empty string of at most 4000 characters")
        if not isinstance(evidence, list) or len(evidence) < 2:
            raise GoalInputError("at least two validated evidence items are required")
        system = (
            "You are Elyntro's comparison component. Use only the supplied evidence. "
            "Do not invent facts, sources, or costs. Return only valid JSON with keys: "
            "recommendation_id (one supplied id), rationale (string), comparison "
            "(array of objects with id and assessment), cited_evidence_ids (array of supplied ids), "
            "limitations (array of strings). Compare every option. State uncertainty where relevant."
        )
        user = json.dumps({"goal": goal, "evidence": evidence}, ensure_ascii=False)
        started = time.monotonic()
        text, input_tokens, output_tokens = self.model.complete(
            self.model_id, ({"role": "system", "content": system},
                            {"role": "user", "content": user}),
            self.max_output_tokens, self.timeout_seconds,
        )
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("model returned invalid JSON; result rejected") from None
        if not isinstance(parsed, dict):
            raise ValueError("model response must be a JSON object")
        parsed["model_metadata"] = {
            "model_id": self.model_id, "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)), "latency_ms": latency_ms,
        }
        return parsed

    @classmethod
    def _verify_result(cls, result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if context.get("workflow_id") != cls.TASK_TYPE:
            return {"passed": False, "verifier_id": cls.VERIFIER_ID, "reason": "unsupported workflow type"}
        outputs = result.get("outputs", {})
        evidence_out = outputs.get("validate_evidence", {})
        report_out = outputs.get("synthesize_comparison", {})
        evidence = evidence_out.get("items", []) if isinstance(evidence_out, dict) else []
        report = report_out.get("value", report_out) if isinstance(report_out, dict) else None
        if not isinstance(report, dict):
            return {"passed": False, "verifier_id": cls.VERIFIER_ID, "reason": "comparison report is missing"}
        ids = {item.get("id") for item in evidence if isinstance(item, dict) and isinstance(item.get("id"), str)}
        recommendation = report.get("recommendation_id")
        cited = report.get("cited_evidence_ids")
        comparison = report.get("comparison")
        rationale = report.get("rationale")
        limitations = report.get("limitations")
        recommendation_ok = isinstance(recommendation, str) and recommendation in ids
        cited_ok = isinstance(cited, list) and bool(cited) and all(isinstance(item, str) and item in ids for item in cited)
        comparison_ids = []
        comparison_shape_ok = isinstance(comparison, list) and all(
            isinstance(item, dict) and isinstance(item.get("id"), str)
            and isinstance(item.get("assessment"), str) and bool(item.get("assessment", "").strip())
            for item in comparison
        )
        if comparison_shape_ok:
            comparison_ids = [item["id"] for item in comparison]
        comparison_ok = comparison_shape_ok and len(comparison_ids) == len(ids) and set(comparison_ids) == ids
        rationale_ok = isinstance(rationale, str) and bool(rationale.strip())
        limitations_ok = isinstance(limitations, list) and all(isinstance(x, str) for x in limitations)
        problems = []
        if not recommendation_ok:
            problems.append("recommendation_id is not in supplied evidence")
        if not cited_ok:
            problems.append("cited_evidence_ids must reference supplied evidence")
        if not comparison_ok:
            problems.append("comparison must contain every supplied option exactly once by ID with an assessment")
        if not rationale_ok:
            problems.append("rationale is missing")
        if not limitations_ok:
            problems.append("limitations must be an array of strings")
        passed = not problems
        return {"passed": passed, "verifier_id": cls.VERIFIER_ID,
                "evidence": [f"validated {len(ids)} supplied option IDs"] if passed else [],
                "reason": "; ".join(problems) if problems else None,
                "checks": {"known_recommendation": recommendation_ok,
                           "citations_grounded": cited_ok,
                           "all_options_compared": comparison_ok,
                           "rationale_present": rationale_ok,
                           "limitations_valid": limitations_ok}}

    def submit(self, *, goal: str, evidence: list[dict[str, Any]], idempotency_key: str) -> str:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > self.MAX_TEXT_LENGTH:
            raise GoalInputError("goal must be a non-empty string of at most 4000 characters")
        if not isinstance(evidence, list):
            raise GoalInputError("evidence must be a list")
        steps = [
            JobStep("validate_evidence", "validate_evidence", {"items": {"$ref": "input.evidence"}}, idempotent=True),
            JobStep("synthesize_comparison", "synthesize_comparison", {
                "goal": {"$ref": "input.goal"}, "evidence": {"$ref": "validate_evidence.output.items"}
            }, idempotent=False),
        ]
        return self.workflow.submit(workflow_id=self.TASK_TYPE,
            inputs={"goal": goal, "evidence": evidence}, steps=steps,
            idempotency_key=idempotency_key, metadata={"model_id": self.model_id, "verifier_id": self.VERIFIER_ID})

    def run(self, job_id: str) -> GoalRun:
        started = time.monotonic()
        job = self.workflow.process_one(job_id=job_id)
        if job is None:
            job = self.store.get_job(job_id)
        verification = job.get("verification") or {}
        return GoalRun(job_id=job_id, state=job.get("state", "unknown"),
            verified=verification.get("passed") is True,
            result=job.get("result"), error=job.get("error"), model_id=self.model_id,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)))


def build_live_model_from_environment() -> tuple[OpenAICompatibleAdapter, str]:
    """Create the OpenAI-compatible adapter from env; no secrets are persisted."""
    import os
    model_id = os.getenv("ELYNTRO_MODEL_ID", "")
    if not model_id.strip():
        raise ValueError("Set ELYNTRO_MODEL_ID to the model name supported by your API provider")
    return OpenAICompatibleAdapter(api_key_env="OPENAI_API_KEY"), model_id.strip()
