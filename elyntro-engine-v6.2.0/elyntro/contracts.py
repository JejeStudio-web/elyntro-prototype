"""Validated data contracts for Elyntro tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    NEEDS_APPROVAL = "needs_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskRequest:
    goal: str
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("goal must be a non-empty string")
        if len(self.goal.strip()) > 10_000:
            raise ValueError("goal must be 10,000 characters or fewer")
        for field_name, values in (("constraints", self.constraints), ("success_criteria", self.success_criteria)):
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"{field_name} must be a list of non-empty strings")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key cannot be blank")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")


@dataclass
class Task:
    task_id: str
    goal: str
    constraints: list[str]
    success_criteria: list[str]
    status: TaskStatus = TaskStatus.CREATED
    plan: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    model_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_request(cls, request: TaskRequest) -> "Task":
        request.validate()
        return cls(
            task_id=str(uuid4()),
            goal=request.goal.strip(),
            constraints=list(request.constraints),
            success_criteria=list(request.success_criteria),
            metadata=dict(request.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "constraints": self.constraints,
            "success_criteria": self.success_criteria,
            "status": self.status.value,
            "plan": self.plan,
            "artifacts": self.artifacts,
            "evidence": self.evidence,
            "errors": self.errors,
            "retry_count": self.retry_count,
            "model_version": self.model_version,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        copied = dict(data)
        copied["status"] = TaskStatus(copied["status"])
        return cls(**copied)
