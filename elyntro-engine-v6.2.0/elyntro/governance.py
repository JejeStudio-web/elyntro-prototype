"""Explicit action authorization and bounded resource accounting for Elyntro.

This module is a policy primitive, not a sandbox. It can decide whether a trusted
host application may invoke a registered action, but it cannot make an unsafe
handler safe or replace OS/container isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Iterable


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


@dataclass(frozen=True)
class ActionRequest:
    """A normalized description of one proposed tool/action invocation."""

    action_id: str
    tool_key: str
    description: str
    risk: RiskLevel = RiskLevel.MEDIUM
    estimated_cost_usd: float = 0.0
    estimated_tokens: int = 0
    requires_network: bool = False
    writes_external_state: bool = False
    user_approved: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for name, value in (("action_id", self.action_id), ("tool_key", self.tool_key), ("description", self.description)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("risk must be a RiskLevel")
        if isinstance(self.estimated_cost_usd, bool) or not isinstance(self.estimated_cost_usd, (int, float)) or self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be a non-negative number")
        if isinstance(self.estimated_tokens, bool) or not isinstance(self.estimated_tokens, int) or self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must be a non-negative integer")
        if not isinstance(self.requires_network, bool) or not isinstance(self.writes_external_state, bool) or not isinstance(self.user_approved, bool):
            raise ValueError("action flags must be booleans")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")


@dataclass(frozen=True)
class GovernancePolicy:
    """Default-deny allowlist and approval policy for registered tools."""

    allowed_tools: frozenset[str]
    denied_tools: frozenset[str] = frozenset()
    approval_threshold: RiskLevel = RiskLevel.HIGH
    allow_critical_actions: bool = False
    allow_network: bool = True
    allow_external_writes: bool = True
    max_single_action_cost_usd: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_tools, (set, frozenset)) or any(not isinstance(x, str) or not x.strip() for x in self.allowed_tools):
            raise ValueError("allowed_tools must be a set of non-empty tool keys")
        if not isinstance(self.denied_tools, (set, frozenset)) or any(not isinstance(x, str) or not x.strip() for x in self.denied_tools):
            raise ValueError("denied_tools must be a set of non-empty tool keys")
        if not isinstance(self.approval_threshold, RiskLevel):
            raise ValueError("approval_threshold must be a RiskLevel")
        if isinstance(self.max_single_action_cost_usd, bool) or not isinstance(self.max_single_action_cost_usd, (int, float)) or self.max_single_action_cost_usd < 0:
            raise ValueError("max_single_action_cost_usd must be non-negative")


@dataclass(frozen=True)
class ResourceLimits:
    max_actions: int = 100
    max_total_tokens: int = 100_000
    max_total_cost_usd: float = 5.0

    def __post_init__(self) -> None:
        if isinstance(self.max_actions, bool) or not isinstance(self.max_actions, int) or self.max_actions < 1:
            raise ValueError("max_actions must be a positive integer")
        if isinstance(self.max_total_tokens, bool) or not isinstance(self.max_total_tokens, int) or self.max_total_tokens < 0:
            raise ValueError("max_total_tokens must be a non-negative integer")
        if isinstance(self.max_total_cost_usd, bool) or not isinstance(self.max_total_cost_usd, (int, float)) or self.max_total_cost_usd < 0:
            raise ValueError("max_total_cost_usd must be non-negative")


@dataclass(frozen=True)
class BudgetSnapshot:
    actions_reserved: int
    tokens_reserved: int
    cost_usd_reserved: float
    limits: ResourceLimits

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions_reserved": self.actions_reserved,
            "tokens_reserved": self.tokens_reserved,
            "cost_usd_reserved": round(self.cost_usd_reserved, 8),
            "limits": {
                "max_actions": self.limits.max_actions,
                "max_total_tokens": self.limits.max_total_tokens,
                "max_total_cost_usd": self.limits.max_total_cost_usd,
            },
        }


class BudgetLedger:
    """Thread-safe reservation ledger; reservations are conservative estimates."""

    def __init__(self, limits: ResourceLimits | None = None) -> None:
        self.limits = limits or ResourceLimits()
        self._actions = 0
        self._tokens = 0
        self._cost = 0.0
        self._lock = Lock()

    def reserve(self, *, tokens: int, cost_usd: float) -> tuple[bool, str | None]:
        with self._lock:
            if self._actions + 1 > self.limits.max_actions:
                return False, "max_actions_exceeded"
            if self._tokens + tokens > self.limits.max_total_tokens:
                return False, "max_total_tokens_exceeded"
            if self._cost + cost_usd > self.limits.max_total_cost_usd + 1e-12:
                return False, "max_total_cost_usd_exceeded"
            self._actions += 1
            self._tokens += tokens
            self._cost += float(cost_usd)
            return True, None

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(self._actions, self._tokens, self._cost, self.limits)


@dataclass(frozen=True)
class PolicyDecision:
    outcome: str  # allow | require_approval | deny | budget_exceeded
    reason: str
    action_id: str
    tool_key: str
    budget: BudgetSnapshot

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "reason": self.reason, "action_id": self.action_id,
                "tool_key": self.tool_key, "budget": self.budget.to_dict()}


class PolicyEngine:
    """Evaluates policy before the host invokes any tool handler.

    Call ``authorize`` exactly once per intended invocation. A successful decision
    reserves the estimated resources; retries should use a new action ID and budget
    reservation. The engine deliberately does not execute tools itself.
    """

    def __init__(self, policy: GovernancePolicy, budget: BudgetLedger | None = None) -> None:
        self.policy = policy
        self.budget = budget or BudgetLedger()

    def authorize(self, action: ActionRequest) -> PolicyDecision:
        action.validate()
        outcome, reason = self._evaluate(action)
        if outcome != "allow":
            return PolicyDecision(outcome, reason, action.action_id, action.tool_key, self.budget.snapshot())
        reserved, budget_reason = self.budget.reserve(tokens=action.estimated_tokens, cost_usd=float(action.estimated_cost_usd))
        if not reserved:
            return PolicyDecision("budget_exceeded", budget_reason or "resource budget exceeded", action.action_id,
                                  action.tool_key, self.budget.snapshot())
        return PolicyDecision("allow", "policy passed; estimated resources reserved", action.action_id,
                              action.tool_key, self.budget.snapshot())

    def _evaluate(self, action: ActionRequest) -> tuple[str, str]:
        if action.tool_key in self.policy.denied_tools:
            return "deny", "tool explicitly denied by policy"
        if action.tool_key not in self.policy.allowed_tools:
            return "deny", "tool is not on the explicit allowlist"
        if action.estimated_cost_usd > self.policy.max_single_action_cost_usd:
            return "deny", "single-action cost limit exceeded"
        if action.requires_network and not self.policy.allow_network:
            return "deny", "network access disabled by policy"
        if action.writes_external_state and not self.policy.allow_external_writes:
            return "deny", "external writes disabled by policy"
        if action.risk == RiskLevel.CRITICAL and not self.policy.allow_critical_actions:
            return "deny", "critical-risk actions are disabled"
        if _RISK_ORDER[action.risk] >= _RISK_ORDER[self.policy.approval_threshold] and not action.user_approved:
            return "require_approval", "explicit user approval required for this risk level"
        return "allow", "policy passed"
