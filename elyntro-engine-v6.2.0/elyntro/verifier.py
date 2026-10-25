"""Evidence verification for Elyntro Engine.

Structural checks are always performed. A criterion is VERIFIED only when an
explicit validator is registered for it and returns True. Presence of an
assertion alone is never treated as proof.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class CriterionCheck:
    criterion: str
    status: VerificationStatus
    method: str
    detail: str
    evidence_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "status": self.status.value,
            "method": self.method,
            "detail": self.detail,
            "evidence_count": self.evidence_count,
        }


@dataclass(frozen=True)
class VerificationReport:
    status: VerificationStatus
    checks: tuple[CriterionCheck, ...]
    task_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == VerificationStatus.VERIFIED

    @property
    def failed_criteria(self) -> tuple[str, ...]:
        return tuple(c.criterion for c in self.checks if c.status == VerificationStatus.FAILED)

    @property
    def unresolved_criteria(self) -> tuple[str, ...]:
        return tuple(c.criterion for c in self.checks if c.status == VerificationStatus.INCONCLUSIVE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "failed_criteria": list(self.failed_criteria),
            "unresolved_criteria": list(self.unresolved_criteria),
        }


Validator = Callable[[str, Sequence[Mapping[str, Any]], Mapping[str, Any]], bool]


@dataclass
class EvidenceVerifier:
    """Verify each success criterion with an explicitly registered checker.

    A validator receives (criterion, matching_evidence, context) and must return
    a bool. It should independently inspect artifacts or domain-specific facts,
    not merely trust the evidence detail string. Exceptions become FAILED checks.
    """
    validators: dict[str, Validator] = field(default_factory=dict)

    def register(self, criterion: str, validator: Validator, *, replace: bool = False) -> None:
        if not isinstance(criterion, str) or not criterion.strip():
            raise ValueError("criterion must be a non-empty string")
        if not callable(validator):
            raise ValueError("validator must be callable")
        key = criterion.strip()
        if key in self.validators and not replace:
            raise ValueError(f"validator already registered for criterion: {key}")
        self.validators[key] = validator

    def verify(
        self,
        criteria: Sequence[str],
        evidence: Sequence[Mapping[str, Any]],
        *,
        artifacts: Sequence[Mapping[str, Any]] = (),
        context: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> VerificationReport:
        if not isinstance(criteria, (list, tuple)):
            raise ValueError("criteria must be a list or tuple")
        if any(not isinstance(c, str) or not c.strip() for c in criteria):
            raise ValueError("criteria must contain non-empty strings")
        if len(set(criteria)) != len(criteria):
            raise ValueError("criteria must be unique")
        if not isinstance(evidence, (list, tuple)):
            raise ValueError("evidence must be a list or tuple")
        if not isinstance(artifacts, (list, tuple)):
            raise ValueError("artifacts must be a list or tuple")

        normalized_evidence: list[dict[str, Any]] = []
        for index, item in enumerate(evidence):
            if not isinstance(item, Mapping):
                raise ValueError(f"evidence item {index} must be a mapping")
            criterion = item.get("criterion")
            detail = item.get("detail")
            if not isinstance(criterion, str) or not criterion.strip():
                raise ValueError(f"evidence item {index} has no criterion")
            if not isinstance(detail, str) or not detail.strip():
                raise ValueError(f"evidence item {index} has no detail")
            normalized_evidence.append(dict(item))

        artifact_names = {
            item.get("name") for item in artifacts
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
        shared_context = dict(context or {})
        shared_context["artifacts"] = list(artifacts)
        shared_context["artifact_names"] = sorted(artifact_names)

        checks: list[CriterionCheck] = []
        for criterion in criteria:
            matching = [item for item in normalized_evidence if item["criterion"].strip() == criterion]
            if not matching:
                checks.append(CriterionCheck(
                    criterion, VerificationStatus.FAILED, "structural",
                    "No evidence item covers this criterion.", 0,
                ))
                continue

            # Evidence can optionally reference an artifact by exact name.
            invalid_refs = [item.get("artifact") for item in matching
                            if item.get("artifact") is not None
                            and item.get("artifact") not in artifact_names]
            if invalid_refs:
                checks.append(CriterionCheck(
                    criterion, VerificationStatus.FAILED, "artifact-reference",
                    f"Evidence references missing artifact(s): {invalid_refs!r}.", len(matching),
                ))
                continue

            validator = self.validators.get(criterion)
            if validator is None:
                checks.append(CriterionCheck(
                    criterion, VerificationStatus.INCONCLUSIVE, "no-validator",
                    "Evidence is present, but no independent validator is registered.", len(matching),
                ))
                continue
            try:
                verdict = validator(criterion, matching, shared_context)
                if not isinstance(verdict, bool):
                    raise TypeError("validator must return bool")
            except Exception as exc:
                checks.append(CriterionCheck(
                    criterion, VerificationStatus.FAILED, "validator-error",
                    f"Validator raised {type(exc).__name__}: {exc}", len(matching),
                ))
                continue
            checks.append(CriterionCheck(
                criterion,
                VerificationStatus.VERIFIED if verdict else VerificationStatus.FAILED,
                "registered-validator",
                "Validator accepted the criterion." if verdict else "Validator rejected the criterion.",
                len(matching),
            ))

        if not checks:
            status = VerificationStatus.INCONCLUSIVE
        elif all(check.status == VerificationStatus.VERIFIED for check in checks):
            status = VerificationStatus.VERIFIED
        elif any(check.status == VerificationStatus.FAILED for check in checks):
            status = VerificationStatus.FAILED
        else:
            status = VerificationStatus.INCONCLUSIVE
        return VerificationReport(status, tuple(checks), task_id)
