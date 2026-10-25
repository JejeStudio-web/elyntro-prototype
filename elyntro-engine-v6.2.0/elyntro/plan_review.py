"""Deterministic quality review and bounded correction selection for plans.

This reviewer measures inspectable properties; it does not judge factual truth or
replace domain-specific verification. Optional repair proposals are untrusted and
must pass the same planner validation as initial model output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence
import re

from .planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition


@dataclass(frozen=True)
class PlanIssue:
    code: str
    severity: str  # info, warning, error
    message: str
    step_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message, "step_id": self.step_id}


@dataclass(frozen=True)
class PlanReviewReport:
    score: float
    issues: tuple[PlanIssue, ...]
    checked_steps: int
    criteria_count: int
    criterion_coverage: float
    selected_plan: str = "original"

    @property
    def acceptable(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "acceptable": self.acceptable,
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_steps": self.checked_steps, "criteria_count": self.criteria_count,
            "criterion_coverage": self.criterion_coverage, "selected_plan": self.selected_plan,
        }


class PlanRepairBackend(Protocol):
    name: str

    def revise(self, goal: str, plan: ExecutionPlan, issues: Sequence[PlanIssue],
               constraints: Sequence[str], success_criteria: Sequence[str],
               tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]: ...


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[\wáéíóúüñ]+", text.casefold()) if len(word) > 3}


class PlanQualityReviewer:
    """Score structural/coverage signals and optionally select a safer repair.

    The numeric score is a heuristic index, not a probability of correctness.
    """
    def review(self, plan: ExecutionPlan, tools: Sequence[ToolDefinition],
               constraints: Sequence[str] = (), success_criteria: Sequence[str] = ()) -> PlanReviewReport:
        issues: list[PlanIssue] = []
        catalog = {tool.key: tool for tool in tools}
        steps = plan.steps
        if not steps:
            return PlanReviewReport(0.0, (PlanIssue("empty_plan", "error", "Plan has no steps."),), 0,
                                    len(success_criteria), 0.0)
        ids = {step.step_id for step in steps}
        for step in steps:
            if step.handler_key not in catalog:
                issues.append(PlanIssue("unknown_handler", "error", "Step references an unregistered handler.", step.step_id))
                continue
            if not step.name.strip():
                issues.append(PlanIssue("empty_name", "error", "Step name is empty.", step.step_id))
            if not step.rationale.strip():
                issues.append(PlanIssue("missing_rationale", "warning", "Step has no rationale; intent is harder to audit.", step.step_id))
            if any(dep not in ids for dep in step.depends_on):
                issues.append(PlanIssue("unknown_dependency", "error", "Step has an unknown dependency.", step.step_id))
            if step.handler_key not in {s.handler_key for s in steps if s.step_id != step.step_id}:
                pass
        # Detect disconnected steps in a multi-step plan: no incoming/outgoing edge.
        if len(steps) > 1:
            connected = {dep for step in steps for dep in step.depends_on}
            connected.update(step.step_id for step in steps if step.depends_on)
            for step in steps:
                if step.step_id not in connected:
                    issues.append(PlanIssue("disconnected_step", "warning", "Step is disconnected from the dependency chain.", step.step_id))
        # Repeated handlers are not automatically wrong, but deserve a review.
        by_handler: dict[str, list[PlannedStep]] = {}
        for step in steps:
            by_handler.setdefault(step.handler_key, []).append(step)
        for key, repeated in by_handler.items():
            if len(repeated) > 1:
                issues.append(PlanIssue("repeated_handler", "info", f"Handler {key!r} is used {len(repeated)} times; confirm this is intentional."))
        # Measure whether each stated success criterion is represented in plan language.
        plan_text = " ".join(f"{s.name} {s.rationale} {s.handler_key}" for s in steps)
        plan_tokens = _tokens(plan_text)
        covered = 0
        for criterion in success_criteria:
            terms = _tokens(criterion)
            overlap = len(terms & plan_tokens) / len(terms) if terms else 1.0
            if overlap >= 0.5:
                covered += 1
            else:
                issues.append(PlanIssue("criterion_not_obvious", "warning",
                    f"No obvious lexical link between a success criterion and plan steps: {criterion[:160]}"))
        coverage = covered / len(success_criteria) if success_criteria else 1.0
        score = 100.0
        penalties = {"error": 45.0, "warning": 8.0, "info": 1.0}
        score -= sum(penalties[item.severity] for item in issues)
        score -= (1.0 - coverage) * 20.0
        if len(steps) > 12:
            score -= min(15.0, (len(steps) - 12) * 2.0)
            issues.append(PlanIssue("long_plan", "warning", "Plan has more than 12 steps; consider whether it can be simplified."))
        return PlanReviewReport(round(max(0.0, min(100.0, score)), 1), tuple(issues), len(steps),
                                len(success_criteria), round(coverage, 3))

    def review_and_repair(self, plan: ExecutionPlan, planner: RuleBasedPlanner,
                          tools: Sequence[ToolDefinition], *, constraints: Sequence[str] = (),
                          success_criteria: Sequence[str] = (), backend: PlanRepairBackend | None = None,
                          max_repairs: int = 1) -> tuple[ExecutionPlan, PlanReviewReport]:
        """Try at most one validated repair; keep it only if its score strictly improves.

        Repair output never executes here. Planner validation enforces allowlisted handlers,
        DAG constraints, serializable inputs, and the original task contract.
        """
        if not 0 <= max_repairs <= 1:
            raise ValueError("max_repairs must be 0 or 1")
        original_report = self.review(plan, tools, constraints, success_criteria)
        if backend is None or max_repairs == 0:
            return plan, original_report
        proposal = backend.revise(plan.goal, plan, original_report.issues, constraints, success_criteria, tools)
        catalog = {tool.key: tool for tool in tools}
        revised = planner._validate_proposal(plan.goal, proposal, catalog, getattr(backend, "name", "repair-backend"),
                                             constraints, success_criteria)
        revised_report = self.review(revised, tools, constraints, success_criteria)
        if revised_report.acceptable and revised_report.score > original_report.score:
            return revised, PlanReviewReport(revised_report.score, revised_report.issues,
                revised_report.checked_steps, revised_report.criteria_count, revised_report.criterion_coverage, "repaired")
        return plan, original_report
