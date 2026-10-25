"""Structured, conservative diagnosis of workflow failures.

This is a deterministic diagnostic layer, not an LLM or proof of root cause. It
maps observable errors and evidence gaps to bounded recommendations. Unknown cases
remain unknown and stop safely rather than being mislabeled as transient.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable

from .adaptive import ExecutionFeedback


class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    MISSING_EVIDENCE = "missing_evidence"
    CONFIGURATION = "configuration"
    INVALID_INPUT = "invalid_input"
    DEPENDENCY = "dependency"
    APPROVAL_REQUIRED = "approval_required"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureDiagnosis:
    category: FailureCategory
    summary: str
    recommended_action: str
    retryable: bool
    requires_human: bool
    confidence: float
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data

    def to_feedback(self, *, completed_step_ids: Iterable[str] = (),
                    failed_step_id: str | None = None,
                    evidence_gaps: Iterable[str] = ()) -> ExecutionFeedback:
        status = "needs_approval" if self.category == FailureCategory.APPROVAL_REQUIRED else "failed"
        return ExecutionFeedback(
            status=status,
            summary=self.summary,
            completed_step_ids=tuple(completed_step_ids),
            failed_step_id=failed_step_id,
            evidence_gaps=tuple(evidence_gaps),
            retryable=self.retryable,
        )


class FailureDiagnoser:
    """Classify failures using explicit signals; never infer success from absence of error."""

    TRANSIENT_TYPES = {"TimeoutError", "ConnectionError", "TemporaryNetworkError", "RateLimitError"}
    CONFIG_TYPES = {"KeyError", "ImportError", "ModuleNotFoundError", "NotImplementedError"}
    INPUT_TYPES = {"ValueError", "TypeError", "JSONDecodeError"}
    PERMISSION_TYPES = {"PermissionError", "AuthenticationError", "AuthorizationError"}

    def diagnose(self, *, error: BaseException | str | None = None,
                 evidence_gaps: Iterable[str] = (), approval_required: bool = False,
                 dependency_failed: bool = False, tool_registered: bool | None = None,
                 permission_denied: bool = False) -> FailureDiagnosis:
        gaps = tuple(str(g).strip() for g in evidence_gaps if str(g).strip())
        if approval_required:
            return FailureDiagnosis(FailureCategory.APPROVAL_REQUIRED,
                "Execution requires explicit approval.", "Request approval; do not execute the gated operation.",
                False, True, 1.0, ("approval_gate",))
        if permission_denied:
            return FailureDiagnosis(FailureCategory.PERMISSION,
                "The operation was denied by a permission boundary.", "Review the required permission and obtain explicit authorization.",
                False, True, 0.99, ("permission_denied",))
        if tool_registered is False:
            return FailureDiagnosis(FailureCategory.CONFIGURATION,
                "A required handler is not registered.", "Register an approved handler or revise the plan to use an available capability.",
                False, True, 0.99, ("handler_missing",))
        if dependency_failed:
            return FailureDiagnosis(FailureCategory.DEPENDENCY,
                "A prerequisite step did not complete successfully.", "Resolve or replan the prerequisite before continuing downstream steps.",
                False, True, 0.95, ("prerequisite_failed",))
        if gaps:
            return FailureDiagnosis(FailureCategory.MISSING_EVIDENCE,
                "The result lacks required evidence.", "Gather the missing evidence or revise the work; do not mark the task successful.",
                False, False, 0.95, tuple(f"evidence_gap:{g}" for g in gaps))
        if error is None:
            return FailureDiagnosis(FailureCategory.UNKNOWN,
                "No failure signal was supplied; root cause cannot be determined.", "Collect the exception, failed step and verification report before deciding.",
                False, True, 0.2, ("insufficient_diagnostic_data",))
        if isinstance(error, BaseException):
            error_type, message = type(error).__name__, str(error)
        else:
            message = str(error)
            error_type = message.split(":", 1)[0].strip() if ":" in message else ""
        signal = f"exception_type:{error_type}" if error_type else "error_message_present"
        if error_type in self.TRANSIENT_TYPES:
            return FailureDiagnosis(FailureCategory.TRANSIENT,
                f"A potentially temporary failure occurred ({error_type}).", "Retry only if the operation is idempotent and within its retry budget.",
                True, False, 0.85, (signal,))
        if error_type in self.CONFIG_TYPES:
            return FailureDiagnosis(FailureCategory.CONFIGURATION,
                f"A configuration or capability error occurred ({error_type}).", "Fix configuration or select a registered capability; do not retry unchanged.",
                False, True, 0.85, (signal,))
        if error_type in self.INPUT_TYPES:
            return FailureDiagnosis(FailureCategory.INVALID_INPUT,
                f"An input or data-validation error occurred ({error_type}).", "Validate the input schema and revise the offending arguments before another attempt.",
                False, False, 0.8, (signal,))
        if error_type in self.PERMISSION_TYPES:
            return FailureDiagnosis(FailureCategory.PERMISSION,
                f"An access-control error occurred ({error_type}).", "Check credentials and policy; require explicit authorization before changing permissions.",
                False, True, 0.85, (signal,))
        # Deliberately do not parse vague message keywords as proof of root cause.
        return FailureDiagnosis(FailureCategory.UNKNOWN,
            f"The failure cause is not classified ({error_type or 'untyped error'}).", "Capture structured logs and verification evidence; stop or request review instead of guessing.",
            False, True, 0.25, (signal,))
