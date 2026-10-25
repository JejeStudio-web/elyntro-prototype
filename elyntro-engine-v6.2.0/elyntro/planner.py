"""Safe goal-to-plan translation for Elyntro Engine.

The default planner is deterministic and does not pretend to be an LLM. It maps
recognized intent stages to explicitly registered capabilities, validates the
resulting dependency graph, and exposes a backend protocol for a future model.
Plans never contain executable code: each step references a registered handler key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
import re
import json

from .task_executor import TaskStep


@dataclass(frozen=True)
class ToolDefinition:
    key: str
    description: str
    capabilities: tuple[str, ...]
    input_template: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    max_retries: int = 1
    timeout_seconds: float = 60.0

    def validate(self) -> None:
        if not self.key.strip() or not self.description.strip():
            raise ValueError("tool key and description must be non-empty")
        if not self.capabilities or any(not isinstance(c, str) or not c.strip() for c in self.capabilities):
            raise ValueError(f"tool {self.key!r} must declare non-empty capabilities")
        if not isinstance(self.input_template, dict):
            raise ValueError("input_template must be a dictionary")
        try:
            json.dumps(self.input_template, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"input_template must be JSON-serializable: {exc}") from exc
        if not isinstance(self.requires_approval, bool):
            raise ValueError("requires_approval must be boolean")
        if not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        if not 0.01 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 0.01 and 3600")


@dataclass(frozen=True)
class PlannedStep:
    step_id: str
    name: str
    handler_key: str
    inputs: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    requires_approval: bool = False
    rationale: str = ""

    def to_task_step(self, tool: ToolDefinition) -> TaskStep:
        return TaskStep(
            step_id=self.step_id,
            name=self.name,
            handler_key=self.handler_key,
            inputs=dict(self.inputs),
            max_retries=tool.max_retries,
            timeout_seconds=tool.timeout_seconds,
        )


@dataclass(frozen=True)
class ExecutionPlan:
    goal: str
    steps: tuple[PlannedStep, ...]
    requires_approval: bool = False
    planner_name: str = "rule-based-v1"
    rationale: tuple[str, ...] = ()

    def to_task_steps(self, tools: Sequence[ToolDefinition]) -> list[TaskStep]:
        catalog = {tool.key: tool for tool in tools}
        return [step.to_task_step(catalog[step.handler_key]) for step in self.steps]


class PlannerBackend(Protocol):
    """Optional future AI backend. It must return structured, untrusted proposals."""
    name: str

    def propose(self, goal: str, constraints: Sequence[str], success_criteria: Sequence[str],
                tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]: ...


_STAGE_TERMS: dict[str, tuple[str, ...]] = {
    "research": ("research", "investigate", "find", "search", "look up", "research", "investiga", "buscar", "busca", "averigua", "encuentra", "informacion", "información", "fuentes", "actualidad"),
    "analyze": ("analyze", "analyse", "analysis", "evaluate", "understand", "diagnose", "analiza", "analizar", "analisis", "análisis", "evalua", "evalúa", "compara", "comparar", "decide", "decidir", "pros and cons", "ventajas", "desventajas"),
    "create": ("create", "write", "draft", "build", "design", "generate", "prepare", "make", "crea", "crear", "escribe", "redacta", "diseña", "construye", "prepara", "elabora", "genera", "planifica", "diseñar"),
    "verify": ("verify", "check", "validate", "test", "review", "verify", "comprueba", "verifica", "revisa", "validar", "prueba", "corrige", "fuentes fiables"),
    "execute": ("send", "publish", "book", "buy", "delete", "submit", "deploy", "run", "envia", "envía", "publica", "reserva", "compra", "borra", "elimina", "despliega", "ejecuta", "instala"),
}

# Explicit workflows keep the default behavior predictable and inspectable.
_WORKFLOWS: dict[str, tuple[str, ...]] = {
    "research": ("research", "analyze", "verify"),
    "analyze": ("research", "analyze", "verify"),
    "create": ("research", "create", "verify"),
    "execute": ("analyze", "execute", "verify"),
    "general": ("analyze", "verify"),
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _stage_requested(goal: str, stage: str) -> bool:
    normalized = _normalize(goal)
    return any(term in normalized for term in _STAGE_TERMS[stage])


class RuleBasedPlanner:
    """A conservative planner using explicit capability metadata and workflow rules.

    This is a useful offline baseline, not a substitute for a trained reasoning model.
    If no suitable registered tool exists for a required stage, planning fails closed.
    """
    name = "rule-based-v1"

    def __init__(self, backend: PlannerBackend | None = None) -> None:
        self.backend = backend

    def plan(self, goal: str, constraints: Sequence[str], success_criteria: Sequence[str],
             tools: Sequence[ToolDefinition], *, context: dict[str, Any] | None = None) -> ExecutionPlan:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        if not tools:
            raise ValueError("no tools are registered; cannot produce an executable plan")
        catalog: dict[str, ToolDefinition] = {}
        for tool in tools:
            tool.validate()
            if tool.key in catalog:
                raise ValueError(f"duplicate tool key: {tool.key}")
            catalog[tool.key] = tool

        if self.backend is not None:
            # Optional richer protocol; preserve compatibility with existing backends.
            propose_with_context = getattr(self.backend, "propose_with_context", None)
            if callable(propose_with_context):
                proposal = propose_with_context(goal, constraints, success_criteria, tools, context or {})
            else:
                proposal = self.backend.propose(goal, constraints, success_criteria, tools)
            return self._validate_proposal(goal, proposal, catalog, self.backend.name, constraints, success_criteria)

        requested = [stage for stage in ("research", "analyze", "create", "execute", "verify") if _stage_requested(goal, stage)]
        if "execute" in requested:
            workflow = list(_WORKFLOWS["execute"])
        elif "create" in requested:
            workflow = list(_WORKFLOWS["create"])
        elif "research" in requested:
            workflow = list(_WORKFLOWS["research"])
        elif "analyze" in requested:
            workflow = list(_WORKFLOWS["analyze"])
        else:
            workflow = list(_WORKFLOWS["general"])

        # If the goal explicitly asks for verification, retain the verification stage.
        if "verify" in requested and "verify" not in workflow:
            workflow.append("verify")

        steps: list[PlannedStep] = []
        reasons: list[str] = []
        previous_id: str | None = None
        used_keys: set[str] = set()
        for index, capability in enumerate(workflow, 1):
            candidates = [tool for tool in tools if capability in {c.casefold() for c in tool.capabilities}]
            if not candidates:
                # Analysis is a default internal stage, but we never invent a handler.
                raise ValueError(f"no registered tool provides required capability: {capability}")
            tool = next((candidate for candidate in candidates if candidate.key not in used_keys), candidates[0])
            used_keys.add(tool.key)
            step_id = f"step-{index}-{capability}"
            inputs = dict(tool.input_template)
            inputs.setdefault("goal", goal.strip())
            inputs.setdefault("constraints", list(constraints))
            inputs.setdefault("success_criteria", list(success_criteria))
            depends_on = (previous_id,) if previous_id else ()
            steps.append(PlannedStep(
                step_id=step_id,
                name=f"{capability.capitalize()}: {tool.description}",
                handler_key=tool.key,
                inputs=inputs,
                depends_on=depends_on,
                requires_approval=tool.requires_approval,
                rationale=f"Selected registered tool for capability '{capability}'.",
            ))
            previous_id = step_id
            reasons.append(f"Stage '{capability}' mapped to tool '{tool.key}'.")
        return ExecutionPlan(
            goal=goal.strip(), steps=tuple(steps),
            requires_approval=any(step.requires_approval for step in steps),
            planner_name=self.name, rationale=tuple(reasons),
        )

    def _validate_proposal(self, goal: str, proposal: list[dict[str, Any]],
                           catalog: dict[str, ToolDefinition], planner_name: str,
                           constraints: Sequence[str] = (),
                           success_criteria: Sequence[str] = ()) -> ExecutionPlan:
        if not isinstance(proposal, list) or not proposal:
            raise ValueError("planner backend must return a non-empty list of steps")
        if len(proposal) > 20:
            raise ValueError("planner proposal exceeds the 20-step safety limit")
        steps: list[PlannedStep] = []
        ids: set[str] = set()
        for raw in proposal:
            if not isinstance(raw, dict):
                raise ValueError("each proposed step must be an object")
            step_id, handler_key = raw.get("step_id"), raw.get("handler_key")
            name, inputs = raw.get("name"), raw.get("inputs", {})
            deps = raw.get("depends_on", [])
            if not isinstance(step_id, str) or not step_id.strip() or step_id in ids:
                raise ValueError("step IDs must be unique non-empty strings")
            if handler_key not in catalog:
                raise ValueError(f"planner proposed unregistered tool: {handler_key!r}")
            if not isinstance(name, str) or not name.strip() or not isinstance(inputs, dict):
                raise ValueError("step name and inputs are invalid")
            try:
                json.dumps(inputs, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"step inputs must be JSON-serializable: {exc}") from exc
            if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
                raise ValueError("depends_on must be a list of step IDs")
            ids.add(step_id)
            tool = catalog[handler_key]
            safe_inputs = dict(inputs)
            # LLM-backed planners opt into host-enforced task-contract fields. Legacy
            # adaptation backends retain input compatibility for completed-step checks.
            if getattr(self.backend, "enforce_task_contract", False):
                safe_inputs["goal"] = goal.strip()
                safe_inputs["constraints"] = list(constraints)
                safe_inputs["success_criteria"] = list(success_criteria)
            else:
                safe_inputs.setdefault("goal", goal.strip())
            rationale = raw.get("rationale", "")
            if not isinstance(rationale, str):
                raise ValueError("step rationale must be text")
            if len(name) > 240 or len(rationale) > 1200:
                raise ValueError("step name or rationale exceeds the allowed length")
            steps.append(PlannedStep(step_id, name, handler_key, safe_inputs, tuple(deps), tool.requires_approval,
                                     rationale))
        self._validate_dag(steps)
        return ExecutionPlan(goal.strip(), tuple(steps), any(s.requires_approval for s in steps), planner_name,
                             tuple(s.rationale for s in steps if s.rationale))

    @staticmethod
    def _validate_dag(steps: Sequence[PlannedStep]) -> None:
        by_id = {step.step_id: step for step in steps}
        for step in steps:
            unknown = set(step.depends_on) - set(by_id)
            if unknown:
                raise ValueError(f"step {step.step_id!r} has unknown dependencies: {sorted(unknown)}")
            if step.step_id in step.depends_on:
                raise ValueError(f"step {step.step_id!r} cannot depend on itself")
        visited: set[str] = set()
        visiting: set[str] = set()
        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("plan dependency cycle detected")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)
        for step in by_id:
            visit(step)
        # The executor currently runs sequentially, so order steps topologically.
        position = {step.step_id: i for i, step in enumerate(steps)}
        for step in steps:
            if any(position[dependency] >= position[step.step_id] for dependency in step.depends_on):
                raise ValueError("steps must be ordered after their dependencies")


class PlanningService:
    """Connect a planner to persisted tasks without automatically executing them.

    Planning is deliberately separate from execution. A caller must inspect the
    returned plan and explicitly hand it to TaskExecutor. This makes approval gates
    enforceable at the application boundary instead of silently running side effects.
    """
    def __init__(self, engine: Any, planner: RuleBasedPlanner, tools: Sequence[ToolDefinition]) -> None:
        self.engine = engine
        self.planner = planner
        self.tools = tuple(tools)
        for tool in self.tools:
            tool.validate()
        keys = [tool.key for tool in self.tools]
        if len(keys) != len(set(keys)):
            raise ValueError("tool keys must be unique")

    def create_plan(self, task_id: str, *, context: dict[str, Any] | None = None) -> ExecutionPlan:
        task = self.engine.store.get_task(task_id)
        if task.status.value not in {"created", "planning"}:
            raise ValueError(f"cannot plan task in status {task.status.value}")
        plan = self.planner.plan(task.goal, task.constraints, task.success_criteria, self.tools, context=context)
        self.engine.store.add_event(task_id, "intelligent_plan_created", {
            "planner": plan.planner_name,
            "step_count": len(plan.steps),
            "requires_approval": plan.requires_approval,
            "steps": [{
                "step_id": step.step_id,
                "name": step.name,
                "handler_key": step.handler_key,
                "inputs": step.inputs,
                "depends_on": list(step.depends_on),
                "requires_approval": step.requires_approval,
                "rationale": step.rationale,
            } for step in plan.steps],
            "rationale": list(plan.rationale),
        })
        return plan
