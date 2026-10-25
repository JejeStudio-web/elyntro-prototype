"""Explicit task lifecycle rules. Invalid transitions fail closed."""
from .contracts import TaskStatus


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.VALIDATING, TaskStatus.CANCELLED},
    TaskStatus.VALIDATING: {TaskStatus.PLANNING, TaskStatus.FAILED, TaskStatus.NEEDS_APPROVAL, TaskStatus.CANCELLED},
    TaskStatus.PLANNING: {TaskStatus.READY, TaskStatus.FAILED, TaskStatus.NEEDS_APPROVAL, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.NEEDS_APPROVAL, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.VERIFYING, TaskStatus.FAILED, TaskStatus.NEEDS_APPROVAL, TaskStatus.CANCELLED},
    TaskStatus.VERIFYING: {TaskStatus.SUCCEEDED, TaskStatus.READY, TaskStatus.FAILED, TaskStatus.NEEDS_APPROVAL, TaskStatus.CANCELLED},
    TaskStatus.NEEDS_APPROVAL: {TaskStatus.READY, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


class InvalidTransitionError(ValueError):
    pass


def ensure_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Transition not allowed: {current.value} -> {target.value}")
