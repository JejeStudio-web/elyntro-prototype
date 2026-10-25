"""Bounded verify -> diagnose -> repair -> re-verify cycle.

This module is an opt-in orchestration primitive. It does not automatically
wrap every engine route; callers must explicitly invoke it around their task.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .verifier import EvidenceVerifier, VerificationReport, VerificationStatus


@dataclass(frozen=True)
class VerificationCycleResult:
    report: VerificationReport
    attempts: int
    repaired: bool
    repair_history: tuple[Mapping[str, Any], ...]
    final_evidence: tuple[Mapping[str, Any], ...] = ()
    final_artifacts: tuple[Mapping[str, Any], ...] = ()

    @property
    def accepted(self) -> bool:
        return self.report.status == VerificationStatus.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "attempts": self.attempts,
            "repaired": self.repaired,
            "report": self.report.to_dict(),
            "repair_history": [dict(item) for item in self.repair_history],
            "final_evidence_count": len(self.final_evidence),
            "final_artifact_count": len(self.final_artifacts),
        }


RepairCallback = Callable[[VerificationReport, Sequence[Mapping[str, Any]], Mapping[str, Any]], tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]]


class VerificationCycle:
    """Run verification and, only after a failed report, bounded repair rounds.

    The repair callback returns (new_evidence, new_artifacts). It is never called
    for VERIFIED or INCONCLUSIVE reports. INCONCLUSIVE is fail-closed and returned
    immediately for human review. Repair callbacks are application code and must
    apply their own tool permissions/governance.
    """

    def __init__(self, verifier: EvidenceVerifier, *, max_repairs: int = 1) -> None:
        if not isinstance(max_repairs, int) or isinstance(max_repairs, bool) or not 0 <= max_repairs <= 5:
            raise ValueError("max_repairs must be an integer from 0 to 5")
        self.verifier = verifier
        self.max_repairs = max_repairs

    def run(
        self,
        criteria: Sequence[str],
        evidence: Sequence[Mapping[str, Any]],
        *,
        artifacts: Sequence[Mapping[str, Any]] = (),
        context: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        repair: RepairCallback | None = None,
    ) -> VerificationCycleResult:
        current_evidence = [dict(item) for item in evidence]
        current_artifacts = [dict(item) for item in artifacts]
        shared_context = dict(context or {})
        history: list[Mapping[str, Any]] = []
        report = self.verifier.verify(criteria, current_evidence, artifacts=current_artifacts,
                                      context=shared_context, task_id=task_id)
        if report.status != VerificationStatus.FAILED or repair is None or self.max_repairs == 0:
            return VerificationCycleResult(report, 1, False, tuple(history), tuple(current_evidence), tuple(current_artifacts))

        for repair_number in range(1, self.max_repairs + 1):
            try:
                new_evidence, new_artifacts = repair(report, tuple(current_evidence), shared_context)
                if not isinstance(new_evidence, (list, tuple)) or not isinstance(new_artifacts, (list, tuple)):
                    raise TypeError("repair callback must return (evidence list/tuple, artifacts list/tuple)")
                # Validate types and copy before accepting callback output.
                if any(not isinstance(item, Mapping) for item in new_evidence):
                    raise TypeError("repair evidence items must be mappings")
                if any(not isinstance(item, Mapping) for item in new_artifacts):
                    raise TypeError("repair artifact items must be mappings")
                current_evidence = [dict(item) for item in new_evidence]
                current_artifacts = [dict(item) for item in new_artifacts]
                history.append({"repair": repair_number, "status": "completed"})
            except Exception as exc:
                history.append({"repair": repair_number, "status": "error", "error_type": type(exc).__name__, "message": str(exc)})
                break
            report = self.verifier.verify(criteria, current_evidence, artifacts=current_artifacts,
                                          context=shared_context, task_id=task_id)
            if report.status != VerificationStatus.FAILED:
                break
        return VerificationCycleResult(report, 1 + len(history), bool(history), tuple(history), tuple(current_evidence), tuple(current_artifacts))
